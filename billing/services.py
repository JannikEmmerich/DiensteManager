"""Duty pay of the units: what is billable, the overview per unit, creating billings."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from accounts.models import Unit
from duties.models import Duty, Report

from .models import BankAccount, Billing, BillingItem

ACTIVE_BILLING = (Billing.Status.ISSUED, Billing.Status.PAID)


class AlreadyBilled(Exception):
    """One of the reports has been billed in the meantime."""


def unbilled_reports():
    """Confirmed helpers of paid duties that are not billed (yet) and can still be.

    Left out: anonymised helpers (nothing to bill under), and helpers of duties older
    than the retention period of billings, whose billing may have been deleted
    (lifecycle/retention.py) – otherwise they would count as unbilled again.
    """
    reports = (
        Report.objects.filter(
            status=Report.Status.CONFIRMED,
            duty__status=Duty.Status.RELEASED,
            anonymized_at__isnull=True,
        )
        .exclude(duty__pay_type=Duty.PayType.NONE)
        .exclude(billing_items__billing__status__in=ACTIVE_BILLING)
    )
    if settings.RETENTION_BILLINGS_YEARS:
        # Billings are deleted after full calendar years, counted from their year, which
        # is never before the year of their duties.
        first_year = timezone.localdate().year - settings.RETENTION_BILLINGS_YEARS
        reports = reports.filter(duty__starts_at__year__gte=first_year)
    return reports


def billable_reports(unit):
    """Confirmed helpers of finished, paid duties that are not billed yet."""
    return (
        unbilled_reports()
        .filter(unit=unit, duty__ends_at__lt=timezone.now())
        .select_related("duty", "position__qualification", "assigned_position__qualification")
        .order_by("duty__starts_at", "duty__title", "name")
    )


def units_with_billable_reports():
    """Units with something to bill, with the number and the expected amount."""
    result = []
    for unit in Unit.objects.all():
        reports = list(billable_reports(unit))
        if reports:
            total = sum((report.pay_for() for report in reports), Decimal(0))
            result.append((unit, len(reports), total))
    return result


@dataclass
class PayLine:
    """One helper at one duty in the overview of a unit."""

    class State:
        UPCOMING = "upcoming"  # the duty has not ended yet
        OPEN = "open"  # to be billed
        BILLED = "billed"  # in an open billing
        PAID = "paid"

    STATES = (State.UPCOMING, State.OPEN, State.BILLED, State.PAID)

    state: str
    date: date
    duty: Duty | None
    duty_title: str
    helper_name: str
    pay_type: str
    rate: Decimal
    hours: Decimal | None
    amount: Decimal
    billing: Billing | None = None


def unit_pay_lines(unit, year):
    """Duty pay of the unit in a year: billed items plus confirmed helpers not billed yet."""
    lines = [
        PayLine(
            state=PayLine.State.PAID
            if item.billing.status == Billing.Status.PAID
            else PayLine.State.BILLED,
            date=item.duty_date,
            duty=item.duty,
            duty_title=item.duty_title,
            helper_name=item.helper_name,
            pay_type=item.pay_type,
            rate=item.rate,
            hours=item.hours,
            amount=item.amount,
            billing=item.billing,
        )
        for item in BillingItem.objects.filter(
            billing__unit=unit, billing__status__in=ACTIVE_BILLING, duty_date__year=year
        ).select_related("billing", "duty")
    ]
    reports = (
        unbilled_reports()
        .filter(unit=unit, duty__starts_at__year=year)
        .select_related("duty", "position__qualification", "assigned_position__qualification")
    )
    for report in reports:
        duty = report.duty
        hourly = duty.pay_type == Duty.PayType.HOURLY
        lines.append(
            PayLine(
                state=PayLine.State.OPEN if duty.is_past else PayLine.State.UPCOMING,
                date=timezone.localdate(duty.starts_at),
                duty=duty,
                duty_title=duty.title,
                helper_name=report.name,
                pay_type=duty.pay_type,
                rate=report.pay_rate,
                hours=duty.duration_hours if hourly else None,
                amount=report.pay_for(),
            )
        )
    lines.sort(key=lambda line: (line.date, line.duty_title, line.helper_name))
    return lines


def unit_pay_years(unit):
    """Years with duty pay for the unit, newest first; always includes the current year."""
    years = {timezone.localdate().year}
    years.update(
        timezone.localtime(moment).year
        for moment in Duty.objects.filter(reports__unit=unit)
        .exclude(pay_type=Duty.PayType.NONE)
        .values_list("starts_at", flat=True)
    )
    years.update(
        BillingItem.objects.filter(billing__unit=unit).values_list("duty_date__year", flat=True)
    )
    return sorted(years, reverse=True)


def _next_sequence(year):
    return (Billing.objects.filter(year=year).aggregate(Max("sequence"))["sequence__max"] or 0) + 1


@dataclass
class ReportLine:
    """A billable helper with the values for the billing; ``None`` keeps the report's."""

    report: Report
    hours: Decimal | None = None
    rate: Decimal | None = None
    duty_title: str | None = None
    duty_date: date | None = None
    helper_name: str | None = None
    qualification: str | None = None

    def item(self, billing):
        report, duty = self.report, self.report.duty
        hourly = duty.pay_type == Duty.PayType.HOURLY
        position = report.filled_position
        rate = report.pay_rate if self.rate is None else self.rate
        return BillingItem(
            billing=billing,
            report=report,
            duty=duty,
            duty_title=duty.title if self.duty_title is None else self.duty_title,
            duty_date=timezone.localdate(duty.starts_at)
            if self.duty_date is None
            else self.duty_date,
            helper_name=report.name if self.helper_name is None else self.helper_name,
            qualification=(position.short_label if position else "")
            if self.qualification is None
            else self.qualification,
            pay_type=duty.pay_type,
            rate=rate,
            hours=self.hours if hourly else None,
            amount=duty.pay_for(self.hours if hourly else None, rate),
        )


@dataclass
class ExtraLine:
    """Another item of a billing, e.g. a vehicle flat rate: quantity × price, or the price."""

    description: str
    rate: Decimal
    date: date
    quantity: Decimal | None = None

    def item(self, billing):
        amount = self.rate if self.quantity is None else self.quantity * self.rate
        return BillingItem(
            billing=billing,
            duty_title=self.description,
            duty_date=self.date,
            pay_type=BillingItem.PayType.OTHER,
            rate=self.rate,
            hours=self.quantity,
            amount=amount.quantize(Decimal("0.01"), ROUND_HALF_UP),
        )


def create_billing(unit, lines, note, user, extra_lines=()):
    """Creates a billing from ``lines`` (``ReportLine``s or pairs of a billable report and
    its hours, which only count for hourly pay) and ``extra_lines`` (``ExtraLine``s).

    Raises ``AlreadyBilled`` if one of the reports has been billed in the meantime.
    """
    lines = [line if isinstance(line, ReportLine) else ReportLine(*line) for line in lines]
    with transaction.atomic():
        report_ids = [line.report.pk for line in lines]
        # Locks the reports so that nobody bills them at the same time.
        list(Report.objects.select_for_update().filter(pk__in=report_ids))
        if BillingItem.objects.filter(
            report_id__in=report_ids, billing__status__in=ACTIVE_BILLING
        ).exists():
            raise AlreadyBilled
        bank_account = BankAccount.objects.filter(unit=unit).first()
        billing = Billing(
            unit=unit,
            year=timezone.localdate().year,
            note=note,
            created_by=user,
            account_holder=bank_account.account_holder if bank_account else "",
            iban=bank_account.iban if bank_account else "",
            bic=bank_account.bic if bank_account else "",
            bank_name=bank_account.bank_name if bank_account else "",
        )
        # The numbers run consecutively per year; retry if someone took the same one.
        for attempt in range(3):
            billing.sequence = _next_sequence(billing.year)
            try:
                with transaction.atomic():
                    billing.save()
                break
            except IntegrityError:
                if attempt == 2:
                    raise
        items = [line.item(billing) for line in [*lines, *extra_lines]]
        BillingItem.objects.bulk_create(items)
        billing.total = sum((item.amount for item in items), Decimal(0))
        billing.save(update_fields=["total"])
    return billing


def change_status(billing, new_status, timestamp_field):
    """Open → paid or cancelled, unless another request got there first."""
    now = timezone.now()
    changed = Billing.objects.filter(pk=billing.pk, status=Billing.Status.ISSUED).update(
        status=new_status, **{timestamp_field: now}
    )
    if changed:
        billing.status = new_status
        setattr(billing, timestamp_field, now)
    return bool(changed)
