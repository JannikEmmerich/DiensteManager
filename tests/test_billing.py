from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from billing import notifications, pdf, services
from billing.models import BankAccount, Billing
from billing.validators import validate_bic, validate_iban
from duties.models import Duty, Report

from .conftest import create_account, create_duty, create_report

IBAN = "DE89370400440532013000"


def _paid_duty(**fields):
    """A finished duty of four hours with 12.50 € per helper hour."""
    values = {
        "starts_in": timedelta(days=-3),
        "pay_type": Duty.PayType.HOURLY,
        "pay_rate": Decimal("12.50"),
    }
    values.update(fields)
    return create_duty(**values)


def _confirmed(duty, unit, name="Hanna Helper", **fields):
    return create_report(duty, unit, name=name, status=Report.Status.CONFIRMED, **fields)


def _bill(client, unit, data):
    return client.post(reverse("billing_create", args=[unit.pk]), data)


# --- IBAN and bank details ---------------------------------------------------------------------


def test_iban_validation():
    validate_iban(IBAN)
    validate_iban("de89 3704 0044 0532 0130 00")
    with pytest.raises(ValidationError):
        validate_iban("DE89370400440532013001")  # wrong check digits
    with pytest.raises(ValidationError):
        validate_iban("not an iban")
    validate_bic("COBADEFFXXX")
    validate_bic("COBADEFF")
    with pytest.raises(ValidationError):
        validate_bic("COBA")


def test_unit_lead_enters_bank_details(client, unit_lead, unit):
    client.force_login(unit_lead)
    response = client.post(
        reverse("bank_account_edit", args=[unit.pk]),
        {"account_holder": "DRK Unit North", "iban": "de89 3704 0044 0532 0130 00", "bic": ""},
    )
    assert response["Location"] == reverse("unit_pay", args=[unit.pk])
    account = BankAccount.objects.get(unit=unit)
    assert account.iban == IBAN
    assert account.iban_display == "DE89 3704 0044 0532 0130 00"
    assert account.updated_by == unit_lead


def test_wrong_iban_is_rejected(client, unit_lead, unit):
    client.force_login(unit_lead)
    response = client.post(
        reverse("bank_account_edit", args=[unit.pk]),
        {"account_holder": "DRK Unit North", "iban": "DE89370400440532013001"},
    )
    assert "The check digits of this IBAN are wrong." in response.text
    assert not BankAccount.objects.exists()


@pytest.mark.parametrize("user", ["member", "other_unit_lead"])
def test_bank_details_are_private(client, request, user, unit):
    BankAccount.objects.create(unit=unit, account_holder="Holder", iban=IBAN)
    client.force_login(request.getfixturevalue(user))
    assert client.get(reverse("bank_account_edit", args=[unit.pk])).status_code == 403
    assert client.get(reverse("unit_pay", args=[unit.pk])).status_code == 403


def test_dispatcher_sees_and_edits_bank_details(client, dispatcher, unit):
    BankAccount.objects.create(unit=unit, account_holder="Holder", iban=IBAN)
    client.force_login(dispatcher)
    assert "DE89 3704 0044 0532 0130 00" in client.get(reverse("unit_pay", args=[unit.pk])).text
    assert client.get(reverse("bank_account_edit", args=[unit.pk])).status_code == 200


# --- Overview of the duty pay ------------------------------------------------------------------


def test_unit_pay_overview(client, unit_lead, unit, other_unit):
    past = _paid_duty(title="Marathon")
    _confirmed(past, unit, "Hanna")
    _confirmed(past, unit, "Paul")
    create_report(past, unit, name="Declined", status=Report.Status.DECLINED)
    _confirmed(past, other_unit, "Other unit")
    upcoming = _paid_duty(
        title="Festival",
        starts_in=timedelta(days=5),
        pay_type=Duty.PayType.FLAT,
        pay_rate=Decimal("40"),
    )
    _confirmed(upcoming, unit, "Mia")
    _confirmed(_paid_duty(title="Unpaid", pay_type=Duty.PayType.NONE, pay_rate=None), unit)
    _confirmed(_paid_duty(title="Cancelled", status=Duty.Status.CANCELLED), unit)

    client.force_login(unit_lead)

    def lines(duty):
        year = timezone.localdate(duty.starts_at).year
        response = client.get(reverse("unit_pay", args=[unit.pk]), {"year": year})
        return response, [
            (line.helper_name, line.state, line.amount) for line in response.context["lines"]
        ]

    response, past_lines = lines(past)
    assert ("Hanna", "open", Decimal("50.00")) in past_lines
    assert ("Paul", "open", Decimal("50.00")) in past_lines
    assert {name for name, _state, _amount in past_lines} <= {"Hanna", "Paul", "Mia"}
    assert response.context["totals"]["open"] == Decimal("100.00")
    _response, upcoming_lines = lines(upcoming)
    assert ("Mia", "upcoming", Decimal("40.00")) in upcoming_lines


def test_overview_shows_billed_and_paid_items(client, dispatcher, unit_lead, unit):
    duty = _paid_duty()
    report = _confirmed(duty, unit)
    billing = services.create_billing(unit, [(report, Decimal("3"))], "", dispatcher)
    client.force_login(unit_lead)
    response = client.get(reverse("unit_pay", args=[unit.pk]))
    assert [(line.state, line.amount) for line in response.context["lines"]] == [
        ("billed", Decimal("37.50"))
    ]
    services.change_status(billing, Billing.Status.PAID, "paid_at")
    response = client.get(reverse("unit_pay", args=[unit.pk]))
    assert response.context["totals"]["paid"] == Decimal("37.50")


# --- What can be billed? ------------------------------------------------------------------------


def test_billable_reports(dispatcher, unit, other_unit):
    duty = _paid_duty()
    billable = _confirmed(duty, unit, "Billable")
    create_report(duty, unit, name="Pending")
    _confirmed(duty, other_unit, "Other unit")
    _confirmed(_paid_duty(starts_in=timedelta(days=2)), unit, "Upcoming")
    _confirmed(_paid_duty(pay_type=Duty.PayType.NONE, pay_rate=None), unit, "No pay")
    _confirmed(_paid_duty(status=Duty.Status.CANCELLED), unit, "Cancelled duty")
    billed = _confirmed(duty, unit, "Billed")
    services.create_billing(unit, [(billed, Decimal("4"))], "", dispatcher)
    assert list(services.billable_reports(unit)) == [billable]


def test_units_with_billable_reports(unit, other_unit):
    _confirmed(_paid_duty(), unit)
    assert services.units_with_billable_reports() == [(unit, 1, Decimal("50.00"))]


# --- Creating billings ---------------------------------------------------------------------------


def test_dispatcher_creates_billing(client, dispatcher, unit, unit_lead, member, mailoutbox):
    BankAccount.objects.create(unit=unit, account_holder="DRK Unit North", iban=IBAN)
    hourly = _paid_duty(title="Marathon")
    flat = _paid_duty(title="Concert", pay_type=Duty.PayType.FLAT, pay_rate=Decimal("40"))
    hanna = _confirmed(hourly, unit, "Hanna")
    paul = _confirmed(hourly, unit, "Paul")
    no_show = _confirmed(hourly, unit, "No show")
    mia = _confirmed(flat, unit, "Mia")
    client.force_login(dispatcher)
    assert client.get(reverse("billing_create", args=[unit.pk])).status_code == 200
    response = _bill(
        client,
        unit,
        {
            f"include_{hanna.pk}": "on",
            f"hours_{hanna.pk}": "4",
            f"include_{paul.pk}": "on",
            f"hours_{paul.pk}": "2.5",
            f"hours_{no_show.pk}": "4",
            f"include_{mia.pk}": "on",
            "note": "Thank you!",
        },
    )
    billing = Billing.objects.get()
    assert response["Location"] == billing.get_absolute_url()
    assert billing.number == f"{timezone.localdate().year}-001"
    assert billing.status == Billing.Status.ISSUED
    assert billing.created_by == dispatcher
    assert (billing.account_holder, billing.iban) == ("DRK Unit North", IBAN)
    items = [(i.helper_name, i.hours, i.amount) for i in billing.items.order_by("helper_name")]
    assert items == [
        ("Hanna", Decimal("4.00"), Decimal("50.00")),
        ("Mia", None, Decimal("40.00")),
        ("Paul", Decimal("2.50"), Decimal("31.25")),
    ]
    assert billing.total == Decimal("121.25")
    assert list(services.billable_reports(unit)) == [no_show]

    # The unit leads get the statement with the PDF attached.
    (message,) = mailoutbox
    assert message.to == ["lead@example.org"]
    assert message.subject == f"[DiensteManager] Statement of duty pay {billing.number}: 121.25 €"
    ((filename, content, mimetype),) = message.attachments
    assert (filename, mimetype) == (f"Billing-{billing.number}.pdf", "application/pdf")
    assert content.startswith(b"%PDF")


def test_numbers_run_consecutively(dispatcher, unit, other_unit):
    duty = _paid_duty()
    first = services.create_billing(
        unit, [(_confirmed(duty, unit, "A"), Decimal(1))], "", dispatcher
    )
    second = services.create_billing(
        other_unit, [(_confirmed(duty, other_unit, "B"), Decimal(1))], "", dispatcher
    )
    year = timezone.localdate().year
    assert (first.number, second.number) == (f"{year}-001", f"{year}-002")


def test_a_report_is_billed_only_once(dispatcher, unit):
    report = _confirmed(_paid_duty(), unit)
    services.create_billing(unit, [(report, Decimal(4))], "", dispatcher)
    with pytest.raises(services.AlreadyBilled):
        services.create_billing(unit, [(report, Decimal(4))], "", dispatcher)
    assert Billing.objects.count() == 1


def test_hours_are_required_for_hourly_pay(client, dispatcher, unit):
    report = _confirmed(_paid_duty(), unit)
    client.force_login(dispatcher)
    response = _bill(client, unit, {f"include_{report.pk}": "on", f"hours_{report.pk}": ""})
    assert "Please enter the hours." in response.text
    assert not Billing.objects.exists()


def test_at_least_one_helper(client, dispatcher, unit):
    report = _confirmed(_paid_duty(), unit)
    client.force_login(dispatcher)
    response = _bill(client, unit, {f"hours_{report.pk}": "4"})
    assert "Please select at least one helper." in response.text
    assert not Billing.objects.exists()


def test_rate_changes_do_not_change_issued_billings(dispatcher, unit):
    duty = _paid_duty()
    billing = services.create_billing(unit, [(_confirmed(duty, unit), Decimal(4))], "", dispatcher)
    duty.pay_rate = Decimal("99")
    duty.save()
    item = billing.items.get()
    assert (item.rate, item.amount) == (Decimal("12.50"), Decimal("50.00"))


@pytest.mark.parametrize("user", ["unit_lead", "member"])
def test_only_dispatchers_create_billings(client, request, user, unit):
    report = _confirmed(_paid_duty(), unit)
    client.force_login(request.getfixturevalue(user))
    assert client.get(reverse("billing_list")).status_code == 403
    response = _bill(client, unit, {f"include_{report.pk}": "on", f"hours_{report.pk}": "4"})
    assert response.status_code == 403
    assert not Billing.objects.exists()


# --- Paying and cancelling -------------------------------------------------------------------


def test_mark_paid(client, dispatcher, unit, unit_lead, mailoutbox):
    billing = services.create_billing(
        unit, [(_confirmed(_paid_duty(), unit), Decimal(4))], "", dispatcher
    )
    client.force_login(dispatcher)
    client.post(reverse("billing_mark_paid", args=[billing.pk]))
    billing.refresh_from_db()
    assert billing.status == Billing.Status.PAID
    assert billing.paid_at is not None
    assert mailoutbox[-1].subject == f"[DiensteManager] Statement {billing.number} has been paid"
    # Paid statements are final.
    client.post(reverse("billing_cancel", args=[billing.pk]))
    billing.refresh_from_db()
    assert billing.status == Billing.Status.PAID


def test_cancelled_billing_frees_its_helpers(client, dispatcher, unit, unit_lead, mailoutbox):
    report = _confirmed(_paid_duty(), unit)
    billing = services.create_billing(unit, [(report, Decimal(4))], "", dispatcher)
    client.force_login(dispatcher)
    client.post(reverse("billing_cancel", args=[billing.pk]))
    billing.refresh_from_db()
    assert billing.status == Billing.Status.CANCELLED
    assert list(services.billable_reports(unit)) == [report]
    assert "has been cancelled" in mailoutbox[-1].subject


def test_unit_leads_cannot_change_billings(client, dispatcher, unit, unit_lead):
    billing = services.create_billing(
        unit, [(_confirmed(_paid_duty(), unit), Decimal(4))], "", dispatcher
    )
    client.force_login(unit_lead)
    assert client.post(reverse("billing_mark_paid", args=[billing.pk])).status_code == 403
    assert client.post(reverse("billing_cancel", args=[billing.pk])).status_code == 403


def test_billing_emails_respect_the_setting(client, dispatcher, unit, unit_lead, mailoutbox):
    unit_lead.notify_billing = False
    unit_lead.save()
    report = _confirmed(_paid_duty(), unit)
    client.force_login(dispatcher)
    _bill(client, unit, {f"include_{report.pk}": "on", f"hours_{report.pk}": "4"})
    assert Billing.objects.exists()
    assert mailoutbox == []


# --- Viewing and the PDF -----------------------------------------------------------------------


def test_unit_lead_sees_own_billings_and_pdf(client, dispatcher, unit, unit_lead, other_unit_lead):
    billing = services.create_billing(
        unit, [(_confirmed(_paid_duty(), unit), Decimal(4))], "", dispatcher
    )
    client.force_login(unit_lead)
    assert client.get(billing.get_absolute_url()).status_code == 200
    response = client.get(reverse("billing_pdf", args=[billing.pk]))
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'attachment; filename="Billing-{billing.number}.pdf"'
    assert response.content.startswith(b"%PDF")
    client.force_login(other_unit_lead)
    assert client.get(billing.get_absolute_url()).status_code == 404
    assert client.get(reverse("billing_pdf", args=[billing.pk])).status_code == 404


def test_members_see_no_billings(client, dispatcher, unit, member):
    billing = services.create_billing(
        unit, [(_confirmed(_paid_duty(), unit), Decimal(4))], "", dispatcher
    )
    client.force_login(member)
    assert client.get(reverse("billing_pdf", args=[billing.pk])).status_code == 404


def test_pdf_with_names_beyond_latin_1(dispatcher, unit):
    BankAccount.objects.create(unit=unit, account_holder="Jörg & Söhne <e.V.>", iban=IBAN)
    duty = _paid_duty(title="Stadtfest „Sommer“ & Co.")
    lines = [
        (_confirmed(duty, unit, name), Decimal("3.75"))
        for name in ["Şahin Yıldız", "Łukasz Dvořák", "Zoë O'Neil"]
    ]
    billing = services.create_billing(unit, lines, "Line one\nLine <two>", dispatcher)
    content = pdf.render(billing)
    assert content.startswith(b"%PDF")
    assert b"DejaVuSans" in content


def test_profile_offers_billing_emails_to_unit_leads_only(client, unit_lead, member):
    client.force_login(unit_lead)
    assert "notify_billing" in client.get(reverse("profile")).context["form"].fields
    client.force_login(member)
    assert "notify_billing" not in client.get(reverse("profile")).context["form"].fields


def test_blocked_unit_leads_get_no_billing_emails(dispatcher, unit, rf, mailoutbox):
    create_account("blocked-lead@example.org", Role.UNIT_LEAD, unit, is_active=False)
    billing = services.create_billing(
        unit, [(_confirmed(_paid_duty(), unit), Decimal(4))], "", dispatcher
    )
    request = rf.post("/")
    request.user = dispatcher
    notifications.billing_created(request, billing)
    assert mailoutbox == []
