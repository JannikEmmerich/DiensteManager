from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from accounts.models import Unit

from . import staffing

CENT = Decimal("0.01")


class Qualification(models.Model):
    """A qualification that positions of duties can ask for, e.g. "Rettungssanitäter"."""

    name = models.CharField(_("name"), max_length=100, unique=True)
    abbreviation = models.CharField(_("abbreviation"), max_length=20, blank=True)
    sort_order = models.PositiveSmallIntegerField(
        _("order"), default=0, help_text=_("Lower numbers come first, e.g. 10, 20, 30.")
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_("Inactive qualifications cannot be chosen for new positions any more."),
    )
    # Helpers with this qualification also fill positions for the one it includes, and
    # for the one that one includes, and so on (see duties.staffing).
    includes = models.ForeignKey(
        "self",
        verbose_name=_("also counts as"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="included_by",
        help_text=_("E.g. paramedics also count as first aiders."),
    )

    class Meta:
        verbose_name = _("qualification")
        verbose_name_plural = _("qualifications")
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.abbreviation})" if self.abbreviation else self.name

    @property
    def short_label(self):
        """For narrow places such as the PDF."""
        return self.abbreviation or self.name

    @classmethod
    def includes_map(cls):
        """The ID of each qualification mapped to the ID of the one it includes."""
        return dict(cls.objects.values_list("pk", "includes"))


class DutyQuerySet(models.QuerySet):
    def upcoming(self):
        return self.filter(ends_at__gte=timezone.now())

    def past(self):
        return self.filter(ends_at__lt=timezone.now())

    def offered_to(self, unit_id):
        """Released or cancelled duties that are offered to the unit; never drafts."""
        selected = Duty.units.through.objects.filter(unit_id=unit_id).values("duty_id")
        return self.exclude(status=Duty.Status.DRAFT).filter(Q(all_units=True) | Q(pk__in=selected))

    def with_staffing(self, unit_id=None):
        """Adds the number of confirmed and pending reports.

        With ``unit_id``, additionally the number of active reports of that unit.
        """
        queryset = self.annotate(
            confirmed_count=Count("reports", filter=Q(reports__status=Report.Status.CONFIRMED)),
            pending_count=Count("reports", filter=Q(reports__status=Report.Status.REPORTED)),
        )
        if unit_id is not None:
            queryset = queryset.annotate(
                unit_report_count=Count(
                    "reports",
                    filter=Q(reports__unit_id=unit_id, reports__status__in=Report.ACTIVE),
                )
            )
        return queryset


class Duty(models.Model):
    """A duty for which external units report helpers.

    Entered manually by dispatchers (later also imported from HiOrg-Server). Units only
    see a duty after it has been released, and only if it is offered to them.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        RELEASED = "released", _("Released")
        CANCELLED = "cancelled", _("Cancelled")

    class Source(models.TextChoices):
        MANUAL = "manual", _("Entered manually")
        HIORG = "hiorg", _("HiOrg-Server")

    class PayType(models.TextChoices):
        NONE = "none", _("No duty pay")
        HOURLY = "hourly", _("Per helper hour")
        FLAT = "flat", _("Flat rate per helper")

    title = models.CharField(_("title"), max_length=200)
    location = models.CharField(_("location"), max_length=200, blank=True)
    starts_at = models.DateTimeField(_("start"))
    ends_at = models.DateTimeField(_("end"))
    report_deadline = models.DateTimeField(
        _("report deadline"),
        null=True,
        blank=True,
        help_text=_(
            "Optional. Afterwards, units can no longer report helpers or withdraw reports "
            "themselves; they can ask you to withdraw them. Without it: until the start."
        ),
    )
    helpers_needed = models.PositiveSmallIntegerField(
        _("helpers needed"), validators=[MinValueValidator(1)]
    )
    description = models.TextField(
        _("description"),
        blank=True,
        help_text=_("Visible to the units, e.g. meeting point, qualifications, equipment."),
    )
    # Duty pay ("Dienstgeld") that the units get for each confirmed helper.
    pay_type = models.CharField(
        _("duty pay"), max_length=10, choices=PayType.choices, default=PayType.NONE
    )
    pay_rate = models.DecimalField(
        _("rate in euros"),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text=_("Per helper hour, or per helper for the whole duty."),
    )
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    all_units = models.BooleanField(_("offered to all units"), default=True)
    units = models.ManyToManyField(Unit, verbose_name=_("units"), blank=True, related_name="duties")
    # For the import from HiOrg-Server: where the duty comes from and its ID there.
    source = models.CharField(
        _("source"), max_length=20, choices=Source.choices, default=Source.MANUAL, editable=False
    )
    external_id = models.CharField(_("external ID"), max_length=100, blank=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("created by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    released_at = models.DateTimeField(_("released at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)

    objects = DutyQuerySet.as_manager()

    class Meta:
        verbose_name = _("duty")
        verbose_name_plural = _("duties")
        ordering = ["starts_at", "title"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="duty_ends_after_start"
            ),
            models.CheckConstraint(condition=Q(helpers_needed__gte=1), name="duty_needs_helpers"),
            models.CheckConstraint(
                condition=Q(pay_type="none", pay_rate__isnull=True)
                | (~Q(pay_type="none") & Q(pay_rate__gt=0)),
                name="duty_pay_rate_matches_type",
            ),
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=~Q(external_id=""),
                name="duty_unique_external_id",
            ),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("duty_detail", args=[self.pk])

    @property
    def has_started(self):
        return self.starts_at <= timezone.now()

    @property
    def is_past(self):
        return self.ends_at < timezone.now()

    @property
    def has_billed_reports(self):
        """Whether a helper of this duty is in a billing that is not cancelled."""
        from billing.models import Billing, BillingItem

        return (
            BillingItem.objects.filter(report__duty=self)
            .exclude(billing__status=Billing.Status.CANCELLED)
            .exists()
        )

    @property
    def reports_close_at(self):
        """Until when units report helpers and withdraw reports: the deadline or the start."""
        if self.report_deadline and self.report_deadline < self.starts_at:
            return self.report_deadline
        return self.starts_at

    @property
    def report_deadline_passed(self):
        """The deadline is over, but the duty has not started yet."""
        return self.reports_close_at <= timezone.now() < self.starts_at

    @property
    def is_open_for_reports(self):
        return self.status == self.Status.RELEASED and timezone.now() < self.reports_close_at

    @property
    def duration_hours(self):
        """Length of the duty in hours, to two decimals."""
        hours = Decimal((self.ends_at - self.starts_at).total_seconds()) / 3600
        return hours.quantize(CENT, ROUND_HALF_UP)

    @property
    def has_pay(self):
        return self.pay_type != self.PayType.NONE

    def pay_for(self, hours=None, rate=None):
        """Duty pay for one helper; hourly pay for ``hours`` (default: the whole duty).

        ``rate`` replaces the rate of the duty, e.g. by the rate of a position.
        """
        rate = self.pay_rate if rate is None else rate
        if self.pay_type == self.PayType.HOURLY:
            hours = self.duration_hours if hours is None else hours
            return (rate * hours).quantize(CENT, ROUND_HALF_UP)
        if self.pay_type == self.PayType.FLAT:
            return rate
        return Decimal(0)

    def positions_with_staffing(self):
        """The positions with the number of confirmed and pending helpers who fill them.

        Helpers also fill positions whose qualification theirs includes (see
        duties.staffing).
        """
        positions = list(self.positions.select_related("qualification"))
        reports = self.reports.filter(status__in=Report.ACTIVE).values_list(
            "helper_qualification", "position", "assigned_position", "status"
        )
        staffing.count(positions, list(reports), Qualification.includes_map())
        return positions

    def assign_position(self, report):
        """The position ``report`` fills once it is confirmed (see duties.staffing)."""
        positions = list(self.positions.select_related("qualification"))
        filled = Counter(
            assigned_id or position_id
            for assigned_id, position_id in self.reports.filter(status=Report.Status.CONFIRMED)
            .exclude(pk=report.pk)
            .values_list("assigned_position", "position")
        )
        own = next((p for p in positions if p.pk == report.position_id), None)
        return staffing.assign(
            report.helper_qualification_id, own, positions, filled, Qualification.includes_map()
        )

    def update_helpers_needed(self):
        """``helpers_needed`` is the sum of the positions."""
        total = self.positions.aggregate(total=Sum("count"))["total"] or 0
        if total and total != self.helpers_needed:
            self.helpers_needed = total
            self.save(update_fields=["helpers_needed", "updated_at"])

    def offered_unit_ids(self):
        if self.all_units:
            return set(Unit.objects.values_list("pk", flat=True))
        return set(self.units.values_list("pk", flat=True))

    def is_offered_to(self, unit_id):
        if self.status == self.Status.DRAFT or unit_id is None:
            return False
        return self.all_units or self.units.filter(pk=unit_id).exists()


class DutyPosition(models.Model):
    """How many helpers with a qualification a duty needs, optionally at their own rate."""

    duty = models.ForeignKey(
        Duty, verbose_name=_("duty"), on_delete=models.CASCADE, related_name="positions"
    )
    qualification = models.ForeignKey(
        Qualification,
        verbose_name=_("qualification"),
        on_delete=models.PROTECT,
        related_name="positions",
    )
    count = models.PositiveSmallIntegerField(_("number"), validators=[MinValueValidator(1)])
    pay_rate = models.DecimalField(
        _("own rate in euros"),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text=_("Empty: the rate of the duty."),
    )

    class Meta:
        verbose_name = _("position")
        verbose_name_plural = _("positions")
        ordering = ["qualification__sort_order", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(count__gte=1), name="duty_position_count"),
        ]

    def __str__(self):
        return f"{self.count} × {self.label}"

    @property
    def label(self):
        return str(self.qualification)

    @property
    def short_label(self):
        """For narrow places such as the PDF."""
        return self.qualification.short_label

    @property
    def rate(self):
        return self.duty.pay_rate if self.pay_rate is None else self.pay_rate

    @property
    def open_count(self):
        """Helpers still missing besides the confirmed and pending ones.

        Only for positions from ``Duty.positions_with_staffing()``.
        """
        return max(self.count - self.confirmed_count - self.pending_count, 0)


class Report(models.Model):
    """A helper whom a unit reports for a duty; the dispatchers confirm or decline."""

    class Status(models.TextChoices):
        REPORTED = "reported", _("Reported")
        CONFIRMED = "confirmed", _("Confirmed")
        DECLINED = "declined", _("Declined")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    # Reports that still count: the helper is expected (or may be) at the duty.
    ACTIVE = (Status.REPORTED, Status.CONFIRMED)

    duty = models.ForeignKey(
        Duty, verbose_name=_("duty"), on_delete=models.CASCADE, related_name="reports"
    )
    unit = models.ForeignKey(
        Unit, verbose_name=_("unit"), on_delete=models.CASCADE, related_name="reports"
    )
    # The qualification the helper has; it may be higher than any a position asks for,
    # e.g. an emergency paramedic for a paramedic's position.
    helper_qualification = models.ForeignKey(
        Qualification,
        verbose_name=_("qualification of the helper"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    # The position the helper is reported for: the one closest to their qualification.
    position = models.ForeignKey(
        DutyPosition,
        verbose_name=_("position"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    # The position the helper fills once confirmed; it sets their pay (see duties.staffing).
    assigned_position = models.ForeignKey(
        DutyPosition,
        verbose_name=_("assigned to"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_reports",
    )
    name = models.CharField(_("name"), max_length=150)
    # What units entered as free text before they reported helpers with a qualification
    # from the catalogue; no longer asked for.
    qualification = models.CharField(_("qualification"), max_length=100, blank=True)
    note = models.CharField(_("note"), max_length=500, blank=True)
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.REPORTED
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("reported by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(_("reported at"), auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("decided by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    withdrawn_at = models.DateTimeField(_("withdrawn at"), null=True, blank=True)
    # Once a report is confirmed (or the report deadline is over), the unit can only ask
    # the dispatchers to withdraw it.
    withdrawal_requested_at = models.DateTimeField(
        _("withdrawal requested at"), null=True, blank=True
    )
    withdrawal_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("withdrawal requested by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    withdrawal_note = models.CharField(_("reason for the withdrawal"), max_length=300, blank=True)
    # Data lifecycle: name, qualification and notes are erased after the retention period.
    anonymized_at = models.DateTimeField(_("anonymised at"), null=True, blank=True)

    class Meta:
        verbose_name = _("report")
        verbose_name_plural = _("reports")
        ordering = ["unit__name", "created_at"]
        constraints = [
            # A unit reports a person only once per duty; after withdrawing, it may
            # report them again. Anonymised reports have no name any more.
            models.UniqueConstraint(
                Lower("name"),
                "duty",
                "unit",
                condition=~Q(status="withdrawn") & Q(anonymized_at__isnull=True),
                name="report_unique_name_per_duty_and_unit",
            ),
        ]

    def __str__(self):
        return self.name or gettext("(anonymised)")

    @property
    def is_active(self):
        return self.status in self.ACTIVE

    @property
    def filled_position(self):
        """The position the helper fills; until confirmed, the one they were reported for."""
        return self.assigned_position if self.assigned_position_id else self.position

    @property
    def pay_rate(self):
        """The rate of the position the helper fills, or else of the duty."""
        position = self.filled_position
        if position is not None and position.pay_rate is not None:
            return position.pay_rate
        return self.duty.pay_rate

    def pay_for(self, hours=None):
        return self.duty.pay_for(hours, self.pay_rate)

    @property
    def qualification_label(self):
        """The qualification the helper was reported with.

        Older reports may have what the unit entered as free text instead.
        """
        if self.helper_qualification_id:
            return str(self.helper_qualification)
        return self.qualification

    @property
    def qualification_short_label(self):
        """For narrow places such as lists in emails."""
        if self.helper_qualification_id:
            return self.helper_qualification.short_label
        return self.qualification

    @property
    def withdrawal_requested(self):
        return self.is_active and self.withdrawal_requested_at is not None

    @property
    def can_be_withdrawn(self):
        """By the unit itself: until it is confirmed, and while reports are open."""
        return self.status == self.Status.REPORTED and self.duty.is_open_for_reports

    @property
    def can_request_withdrawal(self):
        """Afterwards the unit asks the dispatchers, until the duty has ended."""
        return (
            self.is_active
            and not self.can_be_withdrawn
            and self.withdrawal_requested_at is None
            and self.duty.status == Duty.Status.RELEASED
            and not self.duty.is_past
        )

    @property
    def can_be_decided(self):
        """Confirm or decline: reports that are not confirmed (nor withdrawn) yet."""
        return (
            self.status in (self.Status.REPORTED, self.Status.DECLINED)
            and self.duty.status == Duty.Status.RELEASED
        )

    @property
    def can_be_taken_back(self):
        """By the dispatchers: confirmed reports, and those the unit asked to withdraw."""
        return self.duty.status == Duty.Status.RELEASED and (
            self.status == self.Status.CONFIRMED or self.withdrawal_requested
        )
