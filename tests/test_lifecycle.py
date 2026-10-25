from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from accounts.models import Invitation, Role
from billing import services
from billing.models import BankAccount, Billing
from duties.models import Duty, Report
from lifecycle import history, retention
from lifecycle.models import HistoryEntry
from mailings.models import Mailing

from .conftest import (
    create_account,
    create_duty,
    create_invitation,
    create_report,
    positions_data,
)

IBAN = "DE89370400440532013000"
OTHER_IBAN = "GB82WEST12345698765432"


def _entries(scope):
    return list(history.entries(scope).order_by("created_at", "pk"))


def _duty_data(duty, **changes):
    data = {
        "title": duty.title,
        "location": duty.location,
        "starts_at": timezone.localtime(duty.starts_at).strftime("%Y-%m-%dT%H:%M"),
        "ends_at": timezone.localtime(duty.ends_at).strftime("%Y-%m-%dT%H:%M"),
        "helpers_needed": duty.helpers_needed,
        "description": duty.description,
        "pay_type": duty.pay_type,
        "pay_rate": duty.pay_rate or "",
        "all_units": str(duty.all_units),
        "units": [unit.pk for unit in duty.units.all()],
        **positions_data(duty=duty),
    }
    data.update(changes)
    return data


# --- Change history -------------------------------------------------------------------------


def test_history_of_a_duty(client, dispatcher, unit, other_unit):
    client.force_login(dispatcher)
    duty = create_duty(status=Duty.Status.DRAFT, title="Marathon")
    later = timezone.localtime(duty.starts_at) + timedelta(hours=2)
    client.post(
        reverse("duty_update", args=[duty.pk]),
        _duty_data(
            duty,
            title="City marathon",
            starts_at=later.strftime("%Y-%m-%dT%H:%M"),
            all_units="False",
            units=[unit.pk],
        ),
    )
    client.post(reverse("duty_release", args=[duty.pk]))
    client.post(reverse("duty_cancel", args=[duty.pk]))
    entries = _entries(history.scope_for_duty(duty.pk))
    assert [entry.action for entry in entries] == ["changed", "released", "cancelled"]
    assert all(entry.user == dispatcher for entry in entries)
    changes = {item["field"]: (item["old"], item["new"]) for item in entries[0].changes}
    assert changes["title"] == ("Marathon", "City marathon")
    assert changes["all_units"] == (True, False)
    assert changes["units"] == ([], ["Unit North"])
    assert set(changes) == {"title", "starts_at", "all_units", "units"}


def test_saving_without_changes_records_nothing(client, dispatcher):
    duty = create_duty()
    client.force_login(dispatcher)
    client.post(reverse("duty_update", args=[duty.pk]), _duty_data(duty))
    assert not HistoryEntry.objects.exists()


def test_history_of_reports(client, dispatcher, member, unit):
    duty = create_duty()
    client.force_login(member)
    client.post(
        reverse("report_create", args=[duty.pk]),
        {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-name": "Mia"},
    )
    report = Report.objects.get()
    client.force_login(dispatcher)
    client.post(reverse("report_confirm", args=[report.pk]))
    client.post(
        reverse("report_add", args=[duty.pk]),
        {
            "unit": unit.pk,
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-0-name": "Erik",
        },
    )
    client.force_login(member)
    client.post(reverse("report_request_withdrawal", args=[report.pk]), {"withdrawal_note": "Ill"})
    client.force_login(dispatcher)
    client.post(reverse("report_take_back", args=[report.pk]))
    entries = _entries(history.scope_for_duty(duty.pk))
    assert [(e.object_repr, e.action, e.user_repr) for e in entries] == [
        ("Mia", "reported", str(member)),
        ("Mia", "confirmed", str(dispatcher)),
        ("Erik", "added", str(dispatcher)),
        ("Mia", "withdrawal_requested", str(member)),
        ("Mia", "withdrawn", str(dispatcher)),
    ]
    assert entries[3].changes == [{"field": "withdrawal_note", "old": "", "new": "Ill"}]


def test_duty_history_is_for_dispatchers_only(client, dispatcher, member):
    duty = create_duty(title="Marathon")
    history.record(duty, history.Action.CHANGED, dispatcher, history.scope_for_duty(duty.pk))
    client.force_login(dispatcher)
    page = client.get(duty.get_absolute_url())
    assert "Change history" in page.text
    assert "Marathon</strong> changed" in page.text
    client.force_login(member)
    assert "Change history" not in client.get(duty.get_absolute_url()).text


def test_history_shows_readable_values(client, dispatcher):
    duty = create_duty(pay_type=Duty.PayType.HOURLY, pay_rate=Decimal("12.5"))
    client.force_login(dispatcher)
    client.post(
        reverse("duty_update", args=[duty.pk]),
        _duty_data(duty, pay_type="flat", pay_rate="40", all_units="True"),
    )
    page = client.get(duty.get_absolute_url()).text
    assert "Duty pay: <del>Per helper hour</del> → Flat rate per helper" in page
    assert "Rate in euros: <del>12.50</del> → 40.00" in page


def test_history_of_billings_and_bank_details(client, dispatcher, unit, unit_lead, mailoutbox):
    client.force_login(unit_lead)
    client.post(
        reverse("bank_account_edit", args=[unit.pk]),
        {"account_holder": "Unit North", "iban": IBAN},
    )
    client.force_login(dispatcher)
    client.post(
        reverse("bank_account_edit", args=[unit.pk]),
        {"account_holder": "Unit North", "iban": OTHER_IBAN},
    )
    created, changed = _entries(history.scope_for_unit(unit.pk))
    assert (created.action, changed.action) == ("created", "changed")
    # The history never contains a complete IBAN.
    assert changed.changes == [
        {
            "field": "iban",
            "old": "DE89 **** **** **** **30 00",
            "new": "GB82 **** **** **** **54 32",
        }
    ]
    assert IBAN not in str(created.changes) + created.object_repr

    duty = create_duty(
        starts_in=timedelta(days=-2), pay_type=Duty.PayType.FLAT, pay_rate=Decimal("40")
    )
    report = create_report(duty, unit, status=Report.Status.CONFIRMED)
    client.post(reverse("billing_create", args=[unit.pk]), {f"include_{report.pk}": "on"})
    billing = Billing.objects.get()
    client.post(reverse("billing_mark_paid", args=[billing.pk]))
    assert [e.action for e in _entries(history.scope_for_billing(billing.pk))] == [
        "created",
        "paid",
    ]
    client.force_login(unit_lead)
    assert "marked as paid" in client.get(billing.get_absolute_url()).text
    assert "Changes of the bank details" in client.get(reverse("unit_pay", args=[unit.pk])).text


def test_changed_bank_details_are_reported(client, dispatcher, unit, unit_lead, mailoutbox):
    BankAccount.objects.create(unit=unit, account_holder="Unit North", iban=IBAN)
    client.force_login(dispatcher)
    client.post(
        reverse("bank_account_edit", args=[unit.pk]),
        {"account_holder": "Someone else", "iban": OTHER_IBAN},
    )
    (message,) = mailoutbox
    assert message.to == ["lead@example.org"]
    assert message.subject == "[DiensteManager] Bank details of Unit North changed"
    assert "GB82 **** **** **** **54 32" in message.body
    assert OTHER_IBAN not in message.body
    mailoutbox.clear()
    client.post(
        reverse("bank_account_edit", args=[unit.pk]),
        {"account_holder": "Someone else", "iban": OTHER_IBAN, "bank_name": "Bank"},
    )
    assert mailoutbox == []  # only the bank name changed


def test_dispatchers_hear_about_bank_changes_by_unit_leads(
    client, admin, dispatcher, unit, unit_lead, mailoutbox
):
    create_account("quiet@example.org", Role.DISPATCHER, notify_billing=False)
    BankAccount.objects.create(unit=unit, account_holder="Unit North", iban=IBAN)
    client.force_login(unit_lead)
    client.post(
        reverse("bank_account_edit", args=[unit.pk]),
        {"account_holder": "Someone else", "iban": OTHER_IBAN},
    )
    assert sorted(message.to[0] for message in mailoutbox) == [
        "admin@example.org",
        "dispatcher@example.org",
    ]
    body = mailoutbox[0].body
    assert "lead@example.org has changed the bank details of Unit North." in body
    assert "e.g. by calling the unit lead" in body
    assert "please contact the Disposition" not in body


def test_billing_warns_about_recent_bank_changes(client, dispatcher, unit):
    BankAccount.objects.create(unit=unit, account_holder="Unit North", iban=IBAN)
    create_report(
        create_duty(starts_in=timedelta(days=-2), pay_type=Duty.PayType.FLAT, pay_rate=1),
        unit,
        status=Report.Status.CONFIRMED,
    )
    client.force_login(dispatcher)
    page = client.get(reverse("billing_create", args=[unit.pk])).text
    assert "check that the change was agreed" in page
    BankAccount.objects.update(updated_at=timezone.now() - timedelta(days=60))
    page = client.get(reverse("billing_create", args=[unit.pk])).text
    assert "check that the change was agreed" not in page


# --- Retention ------------------------------------------------------------------------------


def test_old_reports_are_anonymised(dispatcher, unit, other_unit):
    old = create_duty(starts_in=timedelta(days=-400))
    recent = create_duty(starts_in=timedelta(days=-10))
    first = create_report(old, unit, name="Old 1", qualification="Paramedic", note="Note")
    second = create_report(old, unit, name="Old 2", status=Report.Status.CONFIRMED)
    kept = create_report(recent, unit, name="Recent")
    history.record(first, history.Action.REPORTED, dispatcher, history.scope_for_duty(old.pk))
    history.record(old, history.Action.RELEASED, dispatcher, history.scope_for_duty(old.pk))

    assert retention.apply()["reports_anonymised"] == 2
    for report in (first, second):
        report.refresh_from_db()
        assert (report.name, report.qualification, report.note) == ("", "", "")
        assert report.anonymized_at is not None
    kept.refresh_from_db()
    assert kept.name == "Recent"
    # The history of the anonymised reports is gone, that of the duty stays.
    assert [e.action for e in _entries(history.scope_for_duty(old.pk))] == ["released"]
    # The statistics stay.
    assert Duty.objects.with_staffing().get(pk=old.pk).confirmed_count == 1


def test_billable_reports_wait_for_their_billing(dispatcher, unit):
    duty = create_duty(
        starts_in=timedelta(days=-400), pay_type=Duty.PayType.FLAT, pay_rate=Decimal("40")
    )
    report = create_report(duty, unit, name="Unbilled", status=Report.Status.CONFIRMED)
    assert retention.apply()["reports_anonymised"] == 0
    billing = services.create_billing(unit, [(report, None)], "", dispatcher)
    services.change_status(billing, Billing.Status.PAID, "paid_at")
    assert retention.apply()["reports_anonymised"] == 1
    # The billing keeps its own copy of the name.
    assert billing.items.get().helper_name == "Unbilled"


def test_helpers_in_open_billings_are_kept(dispatcher, unit):
    duty = create_duty(
        starts_in=timedelta(days=-400), pay_type=Duty.PayType.FLAT, pay_rate=Decimal("40")
    )
    report = create_report(duty, unit, name="Hanna", status=Report.Status.CONFIRMED)
    billing = services.create_billing(unit, [(report, None)], "", dispatcher)
    assert retention.apply()["reports_anonymised"] == 0
    # Cancelling the billing makes the helper billable again – still with a name.
    services.change_status(billing, Billing.Status.CANCELLED, "cancelled_at")
    assert [r.name for r in services.billable_reports(unit)] == ["Hanna"]


def test_anonymised_helpers_are_never_billed(unit):
    duty = create_duty(starts_in=timedelta(days=-400))
    create_report(duty, unit, name="Hanna", status=Report.Status.CONFIRMED)
    assert retention.apply()["reports_anonymised"] == 1
    # Duty pay added afterwards: there is nobody to bill any more.
    Duty.objects.filter(pk=duty.pk).update(pay_type=Duty.PayType.FLAT, pay_rate=Decimal("40"))
    assert not services.billable_reports(unit).exists()


def test_helpers_of_deleted_billings_are_not_billed_again(settings, dispatcher, unit):
    settings.RETENTION_REPORTS_DAYS = 0  # the helpers keep their names
    duty = create_duty(
        starts_in=timedelta(days=-11 * 366), pay_type=Duty.PayType.FLAT, pay_rate=Decimal("40")
    )
    report = create_report(duty, unit, name="Hanna", status=Report.Status.CONFIRMED)
    billing = services.create_billing(unit, [(report, None)], "", dispatcher)
    services.change_status(billing, Billing.Status.PAID, "paid_at")
    Billing.objects.filter(pk=billing.pk).update(year=timezone.localdate(duty.starts_at).year)
    assert retention.apply()["billings_deleted"] == 1
    assert not services.billable_reports(unit).exists()


def test_anonymised_reports_are_shown_as_such(client, dispatcher, unit):
    duty = create_duty(starts_in=timedelta(days=-400))
    create_report(duty, unit, name="Old")
    retention.apply()
    client.force_login(dispatcher)
    page = client.get(duty.get_absolute_url()).text
    assert "(anonymised)" in page
    assert "Old" not in page


def test_old_history_mailings_and_invitations_are_deleted(dispatcher, unit):
    now = timezone.now()
    duty = create_duty()
    old_entry = history.record(duty, history.Action.CHANGED, dispatcher, "duty:1")
    HistoryEntry.objects.filter(pk=old_entry.pk).update(created_at=now - timedelta(days=800))
    history.record(duty, history.Action.CHANGED, dispatcher, "duty:1")
    old_mail = Mailing.objects.create(subject="Old", body="x", sent_by=dispatcher)
    Mailing.objects.filter(pk=old_mail.pk).update(sent_at=now - timedelta(days=400))
    Mailing.objects.create(subject="New", body="x", sent_by=dispatcher)
    expired, _ = create_invitation("expired@example.org", Role.MEMBER, unit)
    Invitation.objects.filter(pk=expired.pk).update(expires_at=now - timedelta(days=200))
    revoked, _ = create_invitation("revoked@example.org", Role.MEMBER, unit)
    Invitation.objects.filter(pk=revoked.pk).update(revoked_at=now - timedelta(days=200))
    recently_revoked, _ = create_invitation("recent@example.org", Role.MEMBER, unit)
    Invitation.objects.filter(pk=recently_revoked.pk).update(revoked_at=now - timedelta(days=5))
    pending, _ = create_invitation("pending@example.org", Role.MEMBER, unit)

    result = retention.apply()
    assert (
        result["history_deleted"],
        result["mailings_deleted"],
        result["invitations_deleted"],
    ) == (1, 1, 2)
    assert HistoryEntry.objects.count() == 1
    assert list(Mailing.objects.values_list("subject", flat=True)) == ["New"]
    assert set(Invitation.objects.values_list("email", flat=True)) == {
        "recent@example.org",
        "pending@example.org",
    }


def test_billings_are_kept_for_ten_full_years(dispatcher, unit):
    duty = create_duty(
        starts_in=timedelta(days=-2), pay_type=Duty.PayType.FLAT, pay_rate=Decimal("40")
    )
    paid = services.create_billing(
        unit, [(create_report(duty, unit, name="A", status="confirmed"), None)], "", dispatcher
    )
    open_billing = services.create_billing(
        unit, [(create_report(duty, unit, name="B", status="confirmed"), None)], "", dispatcher
    )
    services.change_status(paid, Billing.Status.PAID, "paid_at")
    history.record(paid, history.Action.PAID, dispatcher, history.scope_for_billing(paid.pk))
    year = timezone.localdate().year

    in_ten_years = timezone.now().replace(year=year + 10)
    assert retention.apply(now=in_ten_years)["billings_deleted"] == 0
    in_eleven_years = timezone.now().replace(year=year + 11)
    assert retention.apply(now=in_eleven_years)["billings_deleted"] == 1
    assert list(Billing.objects.all()) == [open_billing]
    assert not HistoryEntry.objects.filter(scope=history.scope_for_billing(paid.pk)).exists()


def test_dry_run_changes_nothing(unit):
    report = create_report(create_duty(starts_in=timedelta(days=-400)), unit, name="Old")
    output = StringIO()
    call_command("apply_retention", "--dry-run", stdout=output)
    assert "Dry run, nothing changed: reports_anonymised=1" in output.getvalue()
    report.refresh_from_db()
    assert report.name == "Old"
    call_command("apply_retention", stdout=StringIO())
    report.refresh_from_db()
    assert report.name == ""


@pytest.mark.parametrize(
    "setting",
    [
        "RETENTION_REPORTS_DAYS",
        "RETENTION_HISTORY_DAYS",
        "RETENTION_MAILINGS_DAYS",
        "RETENTION_INVITATIONS_DAYS",
        "RETENTION_BILLINGS_YEARS",
    ],
)
def test_zero_keeps_data_forever(settings, setting, db):
    setattr(settings, setting, 0)
    names = {
        "RETENTION_REPORTS_DAYS": retention.anonymise_reports,
        "RETENTION_HISTORY_DAYS": retention.delete_history,
        "RETENTION_MAILINGS_DAYS": retention.delete_mailings,
        "RETENTION_INVITATIONS_DAYS": retention.delete_invitations,
        "RETENTION_BILLINGS_YEARS": retention.delete_billings,
    }
    assert names[setting](timezone.now() + timedelta(days=100 * 365)) == 0


def test_several_anonymised_reports_of_one_unit(unit):
    duty = create_duty(starts_in=timedelta(days=-400))
    for name in ("A", "B", "C"):
        create_report(duty, unit, name=name)
    assert retention.apply()["reports_anonymised"] == 3
    assert Report.objects.filter(name="").count() == 3
