from datetime import timedelta
from smtplib import SMTPException

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from duties.models import Duty, Report

from .conftest import create_account, create_duty, create_report, positions_data


def _recipients(outbox):
    return sorted(address for message in outbox for address in message.to)


@pytest.fixture
def south_member(other_unit):
    return create_account("member-south@example.org", Role.MEMBER, other_unit)


# --- New duties -----------------------------------------------------------------------------


def test_release_notifies_all_unit_accounts(
    client, dispatcher, unit_lead, member, other_unit_lead, mailoutbox
):
    create_account("quiet@example.org", Role.MEMBER, unit_lead.unit, notify_new_duties=False)
    create_account("blocked@example.org", Role.MEMBER, unit_lead.unit, is_active=False)
    duty = create_duty(status=Duty.Status.DRAFT, title="Marathon")
    client.force_login(dispatcher)
    client.post(reverse("duty_release", args=[duty.pk]))
    assert _recipients(mailoutbox) == [
        "lead-south@example.org",
        "lead@example.org",
        "member@example.org",
    ]
    message = mailoutbox[0]
    assert message.subject.startswith("[DiensteManager] New duty: Marathon (")
    assert "A new duty has been released for your unit:" in message.body
    assert "Market square" in message.body
    assert f"http://testserver{duty.get_absolute_url()}" in message.body
    assert "http://testserver/profile/" in message.body


def test_release_for_selected_units_only_notifies_them(
    client, dispatcher, unit, member, other_unit_lead, mailoutbox
):
    duty = create_duty(status=Duty.Status.DRAFT, units=[unit])
    client.force_login(dispatcher)
    client.post(reverse("duty_release", args=[duty.pk]))
    assert _recipients(mailoutbox) == ["member@example.org"]


def test_drafts_are_not_announced(client, dispatcher, member, mailoutbox):
    client.force_login(dispatcher)
    starts_at = timezone.localtime() + timedelta(days=2)
    client.post(
        reverse("duty_create"),
        {
            "title": "Draft",
            "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (starts_at + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "pay_type": "none",
            "all_units": "True",
            **positions_data({"count": 1}),
        },
    )
    assert Duty.objects.get().status == Duty.Status.DRAFT
    assert mailoutbox == []


def _edit(client, duty, **changes):
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
    return client.post(reverse("duty_update", args=[duty.pk]), data)


def test_units_added_later_are_notified(
    client, dispatcher, unit, other_unit, member, south_member, mailoutbox
):
    duty = create_duty(units=[unit])
    client.force_login(dispatcher)
    _edit(client, duty, units=[unit.pk, other_unit.pk])
    assert _recipients(mailoutbox) == ["member-south@example.org"]
    assert "New duty" in mailoutbox[0].subject


# --- Reports ------------------------------------------------------------------------------


def test_reports_notify_dispatchers_in_one_email(client, dispatcher, admin, member, mailoutbox):
    create_account("quiet-dispatcher@example.org", Role.DISPATCHER, notify_reports=False)
    duty = create_duty(title="Marathon")
    client.force_login(member)
    client.post(
        reverse("report_create", args=[duty.pk]),
        {
            "form-TOTAL_FORMS": "2",
            "form-INITIAL_FORMS": "0",
            "form-0-name": "Hanna Helper",
            "form-1-name": "Paul Partner",
        },
    )
    assert _recipients(mailoutbox) == ["admin@example.org", "dispatcher@example.org"]
    message = mailoutbox[0]
    assert message.subject == "[DiensteManager] Unit North reported 2 helpers for Marathon"
    assert "- Hanna Helper (Helper)\n- Paul Partner (Helper)\n" in message.body


def test_withdrawal_notifies_dispatchers(client, dispatcher, member, unit, mailoutbox):
    report = create_report(create_duty(title="Marathon"), unit, name="Hanna", reported_by=member)
    client.force_login(member)
    client.post(reverse("report_withdraw", args=[report.pk]))
    assert _recipients(mailoutbox) == ["dispatcher@example.org"]
    assert mailoutbox[0].subject == "[DiensteManager] Unit North withdrew Hanna from Marathon"


def test_decision_notifies_reporter_and_unit_leads(
    client, dispatcher, unit, unit_lead, member, other_unit_lead, mailoutbox
):
    create_account("other-member@example.org", Role.MEMBER, unit)
    report = create_report(create_duty(title="Marathon"), unit, name="Mia", reported_by=member)
    client.force_login(dispatcher)
    client.post(reverse("report_confirm", args=[report.pk]))
    assert _recipients(mailoutbox) == ["lead@example.org", "member@example.org"]
    assert mailoutbox[0].subject == "[DiensteManager] Decision on your reports for Marathon"
    assert "- Mia: Confirmed" in mailoutbox[0].body


def test_confirm_all_sends_one_email_per_unit(
    client, dispatcher, unit, other_unit, unit_lead, other_unit_lead, mailoutbox
):
    duty = create_duty()
    create_report(duty, unit, name="North 1")
    create_report(duty, unit, name="North 2")
    create_report(duty, other_unit, name="South 1")
    client.force_login(dispatcher)
    client.post(reverse("report_confirm_all", args=[duty.pk]))
    assert _recipients(mailoutbox) == ["lead-south@example.org", "lead@example.org"]
    north = next(m for m in mailoutbox if m.to == ["lead@example.org"])
    assert "North 1" in north.body and "North 2" in north.body
    assert "South 1" not in north.body


def test_unchanged_decision_sends_nothing(client, dispatcher, unit, unit_lead, mailoutbox):
    report = create_report(create_duty(), unit, status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    client.post(reverse("report_confirm", args=[report.pk]))
    assert mailoutbox == []


# --- Changed and cancelled duties --------------------------------------------------------------


def test_time_change_notifies_units_with_active_reports(
    client, dispatcher, unit, other_unit, unit_lead, other_unit_lead, member, mailoutbox
):
    duty = create_duty()
    create_report(duty, unit, reported_by=member)
    create_report(duty, other_unit, status=Report.Status.WITHDRAWN)
    client.force_login(dispatcher)
    later = timezone.localtime(duty.starts_at) + timedelta(hours=1)
    _edit(client, duty, starts_at=later.strftime("%Y-%m-%dT%H:%M"), location="Harbour")
    assert _recipients(mailoutbox) == ["lead@example.org", "member@example.org"]
    message = mailoutbox[0]
    assert message.subject == "[DiensteManager] Duty changed: Marathon"
    assert "(start, location)" in message.body
    assert "Harbour" in message.body


def test_other_changes_send_nothing(client, dispatcher, unit, unit_lead, mailoutbox):
    duty = create_duty()
    create_report(duty, unit)
    client.force_login(dispatcher)
    _edit(client, duty, description="Bring a jacket.", helpers_needed=5)
    assert mailoutbox == []


def test_cancellation_notifies_units_with_active_reports(
    client, dispatcher, unit, other_unit, unit_lead, other_unit_lead, mailoutbox
):
    duty = create_duty(title="Marathon")
    create_report(duty, unit, status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    client.post(reverse("duty_cancel", args=[duty.pk]))
    assert _recipients(mailoutbox) == ["lead@example.org"]
    assert mailoutbox[0].subject == "[DiensteManager] Duty cancelled: Marathon"


def test_subjects_are_plain_text(client, dispatcher, unit, unit_lead, mailoutbox):
    duty = create_duty(title="Rock'n'Roll & <Co>")
    create_report(duty, unit, status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    client.post(reverse("duty_cancel", args=[duty.pk]))
    assert mailoutbox[0].subject == "[DiensteManager] Duty cancelled: Rock'n'Roll & <Co>"


# --- Settings and errors --------------------------------------------------------------------


def test_opted_out_accounts_get_no_report_emails(client, dispatcher, unit, unit_lead, mailoutbox):
    unit_lead.notify_reports = False
    unit_lead.save()
    report = create_report(create_duty(), unit, reported_by=unit_lead)
    client.force_login(dispatcher)
    client.post(reverse("report_decline", args=[report.pk]))
    assert mailoutbox == []


def test_the_acting_person_gets_no_email(client, admin, dispatcher, unit_lead, mailoutbox):
    duty = create_duty(status=Duty.Status.DRAFT)
    report_duty = create_duty()
    client.force_login(unit_lead)
    client.post(
        reverse("report_create", args=[report_duty.pk]),
        {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-name": "Self"},
    )
    assert "lead@example.org" not in _recipients(mailoutbox)
    mailoutbox.clear()
    client.force_login(admin)
    client.post(reverse("duty_release", args=[duty.pk]))
    assert "admin@example.org" not in _recipients(mailoutbox)


def test_sending_errors_do_not_undo_the_change(client, dispatcher, member, monkeypatch):
    def fail(self, messages):
        raise SMTPException("no connection")

    monkeypatch.setattr(mail.backends.locmem.EmailBackend, "send_messages", fail)
    duty = create_duty(status=Duty.Status.DRAFT)
    client.force_login(dispatcher)
    response = client.post(reverse("duty_release", args=[duty.pk]), follow=True)
    assert "the notification emails could not be sent" in response.text
    duty.refresh_from_db()
    assert duty.status == Duty.Status.RELEASED


def test_profile_notification_settings(client, member, dispatcher):
    client.force_login(member)
    client.post(
        reverse("profile"),
        {"first_name": "Mia", "last_name": "Member", "language": "", "notify_reports": "on"},
    )
    member.refresh_from_db()
    assert not member.notify_new_duties
    assert member.notify_reports

    client.force_login(dispatcher)
    form = client.get(reverse("profile")).context["form"]
    assert "notify_new_duties" not in form.fields
    assert "notify_reports" in form.fields


def test_releasing_twice_notifies_once(client, dispatcher, member, mailoutbox):
    duty = create_duty(status=Duty.Status.DRAFT)
    client.force_login(dispatcher)
    client.post(reverse("duty_release", args=[duty.pk]))
    response = client.post(reverse("duty_release", args=[duty.pk]), follow=True)
    assert "Only drafts can be released." in response.text
    assert len(mailoutbox) == 1


def test_pay_change_notifies_units_with_active_reports(
    client, dispatcher, unit, unit_lead, mailoutbox
):
    duty = create_duty()
    create_report(duty, unit)
    client.force_login(dispatcher)
    _edit(client, duty, pay_type="flat", pay_rate="30")
    assert _recipients(mailoutbox) == ["lead@example.org"]
    assert "(duty pay, rate in euros)" in mailoutbox[0].body
    assert "Duty pay: 30.00 € flat rate per helper" in mailoutbox[0].body
