from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from duties.models import Duty, Report
from duties.views import _decide

from .conftest import create_duty, create_report


def _duty_data(**data):
    starts_at = timezone.localtime() + timedelta(days=3)
    values = {
        "title": "City festival",
        "location": "Town hall",
        "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": (starts_at + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M"),
        "helpers_needed": 4,
        "description": "Meet at the main entrance.",
        "all_units": "True",
    }
    values.update(data)
    return values


def _report_data(*rows):
    data = {
        "form-TOTAL_FORMS": "3",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "1",
        "form-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f"form-{index}-{field}"] = value
    return data


# --- Creating and releasing duties ----------------------------------------------------------


def test_dispatcher_creates_duty_as_draft(client, dispatcher):
    client.force_login(dispatcher)
    response = client.post(reverse("duty_create"), _duty_data())
    duty = Duty.objects.get()
    assert response.status_code == 302
    assert response["Location"] == duty.get_absolute_url()
    assert duty.status == Duty.Status.DRAFT
    assert duty.source == Duty.Source.MANUAL
    assert duty.created_by == dispatcher
    assert duty.all_units


def test_create_and_release_at_once(client, dispatcher, mailoutbox):
    client.force_login(dispatcher)
    client.post(reverse("duty_create"), {**_duty_data(), "release": "1"})
    duty = Duty.objects.get()
    assert duty.status == Duty.Status.RELEASED
    assert duty.released_at is not None


@pytest.mark.parametrize("user", ["unit_lead", "member"])
def test_units_may_not_manage_duties(client, request, user):
    client.force_login(request.getfixturevalue(user))
    duty = create_duty(status=Duty.Status.DRAFT)
    assert client.get(reverse("duty_create")).status_code == 403
    assert client.post(reverse("duty_create"), _duty_data()).status_code == 403
    for name in ("duty_update", "duty_release", "duty_cancel", "duty_delete"):
        assert client.post(reverse(name, args=[duty.pk])).status_code == 403, name
    duty.refresh_from_db()
    assert duty.status == Duty.Status.DRAFT


def test_end_must_be_after_start(client, dispatcher):
    client.force_login(dispatcher)
    data = _duty_data()
    data["ends_at"] = data["starts_at"]
    response = client.post(reverse("duty_create"), data)
    assert "The end must be after the start." in response.text
    assert not Duty.objects.exists()


def test_selected_units_require_a_unit(client, dispatcher):
    client.force_login(dispatcher)
    response = client.post(reverse("duty_create"), _duty_data(all_units="False"))
    assert "Please select at least one unit." in response.text
    assert not Duty.objects.exists()


def test_duty_for_selected_units(client, dispatcher, unit, other_unit):
    client.force_login(dispatcher)
    client.post(reverse("duty_create"), _duty_data(all_units="False", units=[unit.pk]))
    duty = Duty.objects.get()
    assert not duty.all_units
    assert list(duty.units.all()) == [unit]


def test_all_units_clears_the_selection(client, dispatcher, unit):
    client.force_login(dispatcher)
    client.post(reverse("duty_create"), _duty_data(all_units="True", units=[unit.pk]))
    assert not Duty.objects.get().units.exists()


def test_release_draft(client, dispatcher):
    duty = create_duty(status=Duty.Status.DRAFT)
    client.force_login(dispatcher)
    client.post(reverse("duty_release", args=[duty.pk]))
    duty.refresh_from_db()
    assert duty.status == Duty.Status.RELEASED


def test_started_duty_cannot_be_released(client, dispatcher):
    duty = create_duty(status=Duty.Status.DRAFT, starts_in=timedelta(hours=-1))
    client.force_login(dispatcher)
    response = client.post(reverse("duty_release", args=[duty.pk]), follow=True)
    assert "already started" in response.text
    duty.refresh_from_db()
    assert duty.status == Duty.Status.DRAFT


def test_release_requires_post(client, dispatcher):
    duty = create_duty(status=Duty.Status.DRAFT)
    client.force_login(dispatcher)
    assert client.get(reverse("duty_release", args=[duty.pk])).status_code == 405


def test_only_drafts_can_be_deleted(client, dispatcher):
    released = create_duty()
    draft = create_duty(status=Duty.Status.DRAFT, title="Draft")
    client.force_login(dispatcher)
    client.post(reverse("duty_delete", args=[released.pk]))
    client.post(reverse("duty_delete", args=[draft.pk]))
    assert list(Duty.objects.all()) == [released]


def test_cancel_duty(client, dispatcher):
    duty = create_duty()
    client.force_login(dispatcher)
    client.post(reverse("duty_cancel", args=[duty.pk]))
    duty.refresh_from_db()
    assert duty.status == Duty.Status.CANCELLED
    assert duty.cancelled_at is not None
    assert client.get(reverse("duty_update", args=[duty.pk])).status_code == 302


# --- Who sees which duty? -----------------------------------------------------------------


def test_units_do_not_see_drafts(client, member):
    draft = create_duty(status=Duty.Status.DRAFT, title="Secret draft")
    client.force_login(member)
    assert "Secret draft" not in client.get(reverse("duty_list")).text
    assert client.get(draft.get_absolute_url()).status_code == 404


def test_units_see_released_duties_for_all_units(client, member):
    duty = create_duty(title="Open for everyone")
    client.force_login(member)
    assert "Open for everyone" in client.get(reverse("duty_list")).text
    assert client.get(duty.get_absolute_url()).status_code == 200


def test_duty_for_selected_units_is_hidden_from_others(client, member, other_unit_lead, unit):
    duty = create_duty(units=[unit], title="Only North")
    client.force_login(other_unit_lead)
    assert "Only North" not in client.get(reverse("duty_list")).text
    assert client.get(duty.get_absolute_url()).status_code == 404
    client.force_login(member)
    assert "Only North" in client.get(reverse("duty_list")).text


def test_dispatchers_see_drafts(client, dispatcher):
    create_duty(status=Duty.Status.DRAFT, title="My draft")
    client.force_login(dispatcher)
    assert "My draft" in client.get(reverse("duty_list")).text


def test_past_duties_are_listed_separately(client, member):
    create_duty(title="Long ago", starts_in=timedelta(days=-10))
    create_duty(title="Soon")
    client.force_login(member)
    upcoming = client.get(reverse("duty_list")).text
    past = client.get(reverse("duty_list") + "?show=past").text
    assert "Soon" in upcoming and "Long ago" not in upcoming
    assert "Long ago" in past and "Soon" not in past


def test_home_lists_duties_with_open_needs(client, member, unit):
    full = create_duty(title="Fully staffed", helpers_needed=1)
    create_report(full, unit, status=Report.Status.CONFIRMED)
    create_duty(title="Needs people")
    client.force_login(member)
    page = client.get(reverse("home")).text
    assert "Needs people" in page
    assert "Fully staffed" not in page


def test_home_shows_pending_reports_to_dispatchers(client, dispatcher, unit):
    create_report(create_duty(), unit)
    client.force_login(dispatcher)
    assert "1 reported helper is awaiting your decision." in client.get(reverse("home")).text


# --- Reporting helpers ----------------------------------------------------------------------


def test_unit_lead_reports_several_helpers(client, unit_lead, unit):
    duty = create_duty()
    client.force_login(unit_lead)
    assert client.get(reverse("report_create", args=[duty.pk])).status_code == 200
    response = client.post(
        reverse("report_create", args=[duty.pk]),
        _report_data(
            {"name": "Hanna Helper", "qualification": "Paramedic"},
            {"name": "Paul Partner", "note": "Arrives at 9"},
        ),
    )
    assert response.status_code == 302
    assert response["Location"] == duty.get_absolute_url()
    reports = list(Report.objects.order_by("name"))
    assert [r.name for r in reports] == ["Hanna Helper", "Paul Partner"]
    assert all(r.unit == unit and r.reported_by == unit_lead for r in reports)
    assert all(r.status == Report.Status.REPORTED for r in reports)
    assert reports[0].qualification == "Paramedic"


def test_member_may_report(client, member):
    duty = create_duty()
    client.force_login(member)
    client.post(reverse("report_create", args=[duty.pk]), _report_data({"name": "Mia"}))
    assert Report.objects.get().reported_by == member


def test_report_and_add_more(client, member):
    duty = create_duty()
    client.force_login(member)
    response = client.post(
        reverse("report_create", args=[duty.pk]), {**_report_data({"name": "Mia"}), "another": "1"}
    )
    assert response["Location"] == reverse("report_create", args=[duty.pk])


def test_at_least_one_helper_is_required(client, member):
    duty = create_duty()
    client.force_login(member)
    response = client.post(reverse("report_create", args=[duty.pk]), _report_data())
    assert response.status_code == 200
    assert not Report.objects.exists()


def test_same_person_cannot_be_reported_twice(client, member, unit):
    duty = create_duty()
    create_report(duty, unit, name="Hanna Helper")
    client.force_login(member)
    response = client.post(
        reverse("report_create", args=[duty.pk]), _report_data({"name": "hanna helper"})
    )
    assert "This person has already been reported for this duty." in response.text
    response = client.post(
        reverse("report_create", args=[duty.pk]), _report_data({"name": "Tom"}, {"name": "TOM"})
    )
    assert "This person is listed twice." in response.text
    assert Report.objects.count() == 1


def test_withdrawn_person_can_be_reported_again(client, member, unit):
    duty = create_duty()
    create_report(duty, unit, name="Hanna Helper", status=Report.Status.WITHDRAWN)
    client.force_login(member)
    client.post(reverse("report_create", args=[duty.pk]), _report_data({"name": "Hanna Helper"}))
    assert Report.objects.filter(status=Report.Status.REPORTED).count() == 1


def test_another_unit_may_report_the_same_name(client, member, other_unit_lead, unit):
    duty = create_duty()
    create_report(duty, unit, name="Alex")
    client.force_login(other_unit_lead)
    client.post(reverse("report_create", args=[duty.pk]), _report_data({"name": "Alex"}))
    assert Report.objects.count() == 2


@pytest.mark.parametrize(
    "duty_fields",
    [
        {"status": Duty.Status.CANCELLED},
        {"starts_in": timedelta(hours=-1)},
    ],
)
def test_no_reports_for_cancelled_or_started_duties(client, member, duty_fields):
    duty = create_duty(**duty_fields)
    client.force_login(member)
    response = client.post(reverse("report_create", args=[duty.pk]), _report_data({"name": "Mia"}))
    assert response.status_code == 302
    assert not Report.objects.exists()


def test_no_reports_for_duties_of_other_units(client, other_unit_lead, unit):
    duty = create_duty(units=[unit])
    client.force_login(other_unit_lead)
    response = client.post(reverse("report_create", args=[duty.pk]), _report_data({"name": "Mia"}))
    assert response.status_code == 404
    assert not Report.objects.exists()


def test_dispatchers_do_not_report(client, dispatcher):
    duty = create_duty()
    client.force_login(dispatcher)
    assert client.get(reverse("report_create", args=[duty.pk])).status_code == 403


def test_units_only_see_their_own_reports(client, member, unit, other_unit):
    duty = create_duty()
    create_report(duty, unit, name="North Person")
    create_report(duty, other_unit, name="South Person", status=Report.Status.CONFIRMED)
    client.force_login(member)
    page = client.get(duty.get_absolute_url()).text
    assert "North Person" in page
    assert "South Person" not in page
    assert "1 / 3" in page  # but the overall staffing


def test_dispatchers_see_all_reports(client, dispatcher, unit, other_unit):
    duty = create_duty()
    create_report(duty, unit, name="North Person")
    create_report(duty, other_unit, name="South Person")
    client.force_login(dispatcher)
    page = client.get(duty.get_absolute_url()).text
    assert "North Person" in page and "South Person" in page


# --- Withdrawing ------------------------------------------------------------------------


def test_member_withdraws_own_report_only(client, member, unit_lead, unit):
    duty = create_duty()
    own = create_report(duty, unit, name="Own", reported_by=member)
    others = create_report(duty, unit, name="Others", reported_by=unit_lead)
    client.force_login(member)
    client.post(reverse("report_withdraw", args=[own.pk]))
    assert client.post(reverse("report_withdraw", args=[others.pk])).status_code == 403
    own.refresh_from_db()
    others.refresh_from_db()
    assert own.status == Report.Status.WITHDRAWN
    assert own.withdrawn_at is not None
    assert others.status == Report.Status.REPORTED


def test_unit_lead_withdraws_any_report_of_the_unit(client, member, unit_lead, unit):
    report = create_report(create_duty(), unit, reported_by=member)
    client.force_login(unit_lead)
    client.post(reverse("report_withdraw", args=[report.pk]))
    report.refresh_from_db()
    assert report.status == Report.Status.WITHDRAWN


def test_other_units_cannot_withdraw(client, other_unit_lead, unit):
    report = create_report(create_duty(), unit)
    client.force_login(other_unit_lead)
    assert client.post(reverse("report_withdraw", args=[report.pk])).status_code == 404


def test_no_withdrawal_after_the_start(client, unit_lead, unit):
    report = create_report(create_duty(starts_in=timedelta(hours=-1)), unit)
    client.force_login(unit_lead)
    assert client.post(reverse("report_withdraw", args=[report.pk])).status_code == 403


# --- Confirming and declining ---------------------------------------------------------------


def test_dispatcher_confirms_and_declines(client, dispatcher, unit):
    duty = create_duty()
    first = create_report(duty, unit, name="First")
    second = create_report(duty, unit, name="Second")
    client.force_login(dispatcher)
    client.post(reverse("report_confirm", args=[first.pk]))
    client.post(reverse("report_decline", args=[second.pk]))
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == Report.Status.CONFIRMED
    assert first.decided_by == dispatcher
    assert second.status == Report.Status.DECLINED


def test_confirm_all_pending(client, dispatcher, unit, other_unit):
    duty = create_duty()
    create_report(duty, unit, name="A")
    create_report(duty, other_unit, name="B")
    create_report(duty, unit, name="C", status=Report.Status.WITHDRAWN)
    client.force_login(dispatcher)
    client.post(reverse("report_confirm_all", args=[duty.pk]))
    assert set(Report.objects.values_list("name", "status")) == {
        ("A", Report.Status.CONFIRMED),
        ("B", Report.Status.CONFIRMED),
        ("C", Report.Status.WITHDRAWN),
    }


@pytest.mark.parametrize("user", ["unit_lead", "member"])
def test_units_cannot_decide(client, request, user, unit):
    report = create_report(create_duty(), unit)
    client.force_login(request.getfixturevalue(user))
    assert client.post(reverse("report_confirm", args=[report.pk])).status_code == 403
    report.refresh_from_db()
    assert report.status == Report.Status.REPORTED


def test_withdrawn_reports_cannot_be_confirmed(client, dispatcher, unit):
    report = create_report(create_duty(), unit, status=Report.Status.WITHDRAWN)
    client.force_login(dispatcher)
    client.post(reverse("report_confirm", args=[report.pk]))
    report.refresh_from_db()
    assert report.status == Report.Status.WITHDRAWN


# --- Editing released duties ------------------------------------------------------------------


def test_units_with_reports_must_stay_selected(client, dispatcher, unit, other_unit):
    duty = create_duty(units=[unit, other_unit])
    create_report(duty, unit)
    client.force_login(dispatcher)
    response = client.post(
        reverse("duty_update", args=[duty.pk]),
        _duty_data(all_units="False", units=[other_unit.pk]),
    )
    assert "These units have reported helpers and must stay selected: Unit North" in response.text
    assert set(duty.units.all()) == {unit, other_unit}


def test_decisions_never_overwrite_a_withdrawal(rf, dispatcher, unit):
    report = create_report(create_duty(), unit)
    stale = Report.objects.get(pk=report.pk)  # loaded before the unit withdraws
    Report.objects.filter(pk=report.pk).update(status=Report.Status.WITHDRAWN)
    request = rf.post("/")
    request.user = dispatcher
    assert _decide(request, [stale], Report.Status.CONFIRMED) == []
    report.refresh_from_db()
    assert report.status == Report.Status.WITHDRAWN
