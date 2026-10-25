"""Report deadline, and withdrawing reports: by the unit while they are not confirmed,
afterwards only by the dispatchers, whom the unit can ask to."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from duties.models import Duty, Report

from .conftest import create_duty, create_report, positions_data


def _duty_data(**data):
    starts_at = timezone.localtime() + timedelta(days=3)
    values = {
        "title": "City festival",
        "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": (starts_at + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M"),
        "pay_type": "none",
        "all_units": "True",
        **positions_data({"count": 4}),
    }
    values.update(data)
    return values


def _recipients(outbox):
    return sorted(address for message in outbox for address in message.to)


def _past_deadline(**fields):
    return create_duty(report_deadline=timezone.now() - timedelta(hours=1), **fields)


# --- Report deadline ----------------------------------------------------------------------------


def test_dispatcher_sets_a_report_deadline(client, dispatcher):
    client.force_login(dispatcher)
    deadline = timezone.localtime() + timedelta(days=1)
    client.post(
        reverse("duty_create"), _duty_data(report_deadline=deadline.strftime("%Y-%m-%dT%H:%M"))
    )
    duty = Duty.objects.get()
    assert duty.report_deadline == deadline.replace(second=0, microsecond=0)
    assert duty.reports_close_at == duty.report_deadline


def test_report_deadline_before_the_start(client, dispatcher):
    client.force_login(dispatcher)
    data = _duty_data()
    response = client.post(reverse("duty_create"), {**data, "report_deadline": data["starts_at"]})
    assert "The report deadline must be before the start." in response.text
    assert not Duty.objects.exists()


def test_without_deadline_reports_are_open_until_the_start(db):
    duty = create_duty()
    assert duty.reports_close_at == duty.starts_at
    assert duty.is_open_for_reports


def test_no_reports_after_the_deadline(client, member):
    duty = _past_deadline()
    assert not duty.is_open_for_reports
    client.force_login(member)
    page = client.get(duty.get_absolute_url()).text
    assert "The report deadline is over." in page
    assert reverse("report_create", args=[duty.pk]) not in page
    response = client.post(
        reverse("report_create", args=[duty.pk]),
        {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-name": "Late"},
    )
    assert response["Location"] == duty.get_absolute_url()
    assert not Report.objects.exists()


def test_deadline_is_shown_and_sent(client, dispatcher, member, mailoutbox):
    deadline = timezone.now() + timedelta(days=1)
    duty = create_duty(status=Duty.Status.DRAFT, report_deadline=deadline)
    client.force_login(dispatcher)
    client.post(reverse("duty_release", args=[duty.pk]))
    assert "Report deadline:" in mailoutbox[0].body
    client.force_login(member)
    assert "Report deadline" in client.get(duty.get_absolute_url()).text
    assert "Report deadline:" in client.get(reverse("duty_list")).text


def test_changed_deadline_is_announced(client, dispatcher, unit, unit_lead, mailoutbox):
    duty = create_duty()
    create_report(duty, unit, status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    start = timezone.localtime(duty.starts_at)
    data = _duty_data(
        title=duty.title,
        location=duty.location,
        starts_at=start.strftime("%Y-%m-%dT%H:%M"),
        ends_at=timezone.localtime(duty.ends_at).strftime("%Y-%m-%dT%H:%M"),
        report_deadline=(start - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
        **positions_data(duty=duty),
    )
    client.post(reverse("duty_update", args=[duty.pk]), data)
    (message,) = mailoutbox
    assert message.subject == "[DiensteManager] Duty changed: Marathon"
    assert "(report deadline)" in message.body


# --- Units withdraw what is not confirmed yet ----------------------------------------------------


def test_units_withdraw_pending_reports(client, member, unit):
    report = create_report(create_duty(), unit, reported_by=member)
    client.force_login(member)
    client.post(reverse("report_withdraw", args=[report.pk]))
    report.refresh_from_db()
    assert report.status == Report.Status.WITHDRAWN


@pytest.mark.parametrize("duty_fields", [{}, {"report_deadline": "past"}])
def test_confirmed_or_late_reports_need_the_dispatchers(client, unit_lead, unit, duty_fields):
    if duty_fields:
        duty, status = _past_deadline(), Report.Status.REPORTED
    else:
        duty, status = create_duty(), Report.Status.CONFIRMED
    report = create_report(duty, unit, status=status)
    client.force_login(unit_lead)
    assert client.post(reverse("report_withdraw", args=[report.pk])).status_code == 403
    page = client.get(duty.get_absolute_url()).text
    assert reverse("report_request_withdrawal", args=[report.pk]) in page
    assert reverse("report_withdraw", args=[report.pk]) not in page


def test_pending_reports_do_not_count_until_confirmed(client, dispatcher, unit):
    duty = create_duty(helpers_needed=2)
    report = create_report(duty, unit)
    assert Duty.objects.with_staffing().get(pk=duty.pk).confirmed_count == 0
    client.force_login(dispatcher)
    client.post(reverse("report_confirm", args=[report.pk]))
    assert Duty.objects.with_staffing().get(pk=duty.pk).confirmed_count == 1


# --- Asking the dispatchers --------------------------------------------------------------------


def test_unit_asks_to_withdraw_a_confirmed_report(
    client, admin, dispatcher, unit, unit_lead, member, mailoutbox
):
    duty = create_duty()
    report = create_report(
        duty, unit, name="Mia", status=Report.Status.CONFIRMED, reported_by=member
    )
    client.force_login(member)
    url = reverse("report_request_withdrawal", args=[report.pk])
    assert "can only be withdrawn" in client.get(url).text
    response = client.post(url, {"withdrawal_note": "Ill, Erik could step in"})
    assert response["Location"] == duty.get_absolute_url()
    report.refresh_from_db()
    assert report.status == Report.Status.CONFIRMED  # still planned
    assert report.withdrawal_requested_by == member
    assert report.withdrawal_note == "Ill, Erik could step in"
    assert _recipients(mailoutbox) == ["admin@example.org", "dispatcher@example.org"]
    message = mailoutbox[0]
    assert message.subject == "[DiensteManager] Unit North asks to withdraw Mia from Marathon"
    assert "Reason: Ill, Erik could step in" in message.body
    # Asking twice sends nothing more.
    assert client.get(url).status_code == 403
    client.force_login(dispatcher)
    page = client.get(duty.get_absolute_url()).text
    assert "Withdrawal requested" in page
    assert "Ill, Erik could step in" in page
    assert "1 unit asks you to withdraw a report." in client.get(reverse("home")).text


def test_members_ask_only_for_their_own_reports(client, member, unit_lead, unit):
    report = create_report(
        create_duty(), unit, status=Report.Status.CONFIRMED, reported_by=unit_lead
    )
    client.force_login(member)
    url = reverse("report_request_withdrawal", args=[report.pk])
    assert client.post(url).status_code == 403
    client.force_login(unit_lead)
    client.post(url)
    report.refresh_from_db()
    assert report.withdrawal_requested_at is not None


def test_no_requests_after_the_duty(client, unit_lead, unit):
    duty = create_duty(starts_in=timedelta(days=-2))
    report = create_report(duty, unit, status=Report.Status.CONFIRMED)
    client.force_login(unit_lead)
    url = reverse("report_request_withdrawal", args=[report.pk])
    assert client.post(url).status_code == 403


def test_dispatcher_withdraws_as_requested(client, dispatcher, unit, unit_lead, member, mailoutbox):
    duty = create_duty()
    report = create_report(
        duty, unit, name="Mia", status=Report.Status.CONFIRMED, reported_by=member
    )
    client.force_login(member)
    client.post(reverse("report_request_withdrawal", args=[report.pk]))
    mailoutbox.clear()
    client.force_login(dispatcher)
    client.post(reverse("report_take_back", args=[report.pk]))
    report.refresh_from_db()
    assert report.status == Report.Status.WITHDRAWN
    assert _recipients(mailoutbox) == ["lead@example.org", "member@example.org"]
    assert mailoutbox[0].subject == "[DiensteManager] Mia withdrawn from Marathon"
    assert "As requested, the dispatchers have withdrawn the report for Mia." in mailoutbox[0].body
    # Settled: a withdrawn report is no longer "requested".
    assert report.withdrawal_requested is False


def test_dispatcher_keeps_the_report(client, dispatcher, unit, unit_lead, mailoutbox):
    duty = create_duty()
    report = create_report(duty, unit, name="Mia", status=Report.Status.CONFIRMED)
    client.force_login(unit_lead)
    client.post(reverse("report_request_withdrawal", args=[report.pk]), {"withdrawal_note": "?"})
    mailoutbox.clear()
    client.force_login(dispatcher)
    client.post(reverse("report_reject_withdrawal", args=[report.pk]))
    report.refresh_from_db()
    assert report.status == Report.Status.CONFIRMED
    assert (report.withdrawal_requested_at, report.withdrawal_note) == (None, "")
    assert _recipients(mailoutbox) == ["lead@example.org"]
    assert mailoutbox[0].subject == "[DiensteManager] Mia stays on Marathon"
    # The unit can ask again.
    client.force_login(unit_lead)
    assert client.get(reverse("report_request_withdrawal", args=[report.pk])).status_code == 200


def test_dispatcher_withdraws_a_confirmed_report_unasked(
    client, dispatcher, unit, unit_lead, mailoutbox
):
    report = create_report(create_duty(), unit, name="Mia", status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    page = client.get(report.duty.get_absolute_url()).text
    assert reverse("report_decline", args=[report.pk]) not in page  # taken back instead
    client.post(reverse("report_decline", args=[report.pk]))
    report.refresh_from_db()
    assert report.status == Report.Status.CONFIRMED
    client.post(reverse("report_take_back", args=[report.pk]))
    report.refresh_from_db()
    assert report.status == Report.Status.WITHDRAWN
    assert "Mia is no longer planned for this duty." in mailoutbox[0].body


@pytest.mark.parametrize("user", ["unit_lead", "member"])
def test_units_cannot_take_back_or_keep(client, request, user, unit):
    report = create_report(create_duty(), unit, status=Report.Status.CONFIRMED)
    client.force_login(request.getfixturevalue(user))
    for name in ["report_take_back", "report_reject_withdrawal"]:
        assert client.post(reverse(name, args=[report.pk])).status_code == 403
    report.refresh_from_db()
    assert report.status == Report.Status.CONFIRMED


def test_declining_settles_a_request(client, dispatcher, unit, unit_lead):
    report = create_report(_past_deadline(), unit)
    client.force_login(unit_lead)
    client.post(reverse("report_request_withdrawal", args=[report.pk]))
    client.force_login(dispatcher)
    client.post(reverse("report_decline", args=[report.pk]))
    report.refresh_from_db()
    assert report.status == Report.Status.DECLINED
    assert report.withdrawal_requested_at is None
