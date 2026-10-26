"""Staffing needs per qualification: positions of duties, reports for a position, rates."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from billing import services
from duties import staffing
from duties.models import Duty, DutyPosition, Qualification, Report
from lifecycle import history

from .conftest import create_duty, create_report, positions_data


@pytest.fixture
def paramedic(db):
    return Qualification.objects.create(name="Paramedic", abbreviation="RS", sort_order=10)


@pytest.fixture
def first_aider(db):
    return Qualification.objects.create(name="First aider", abbreviation="SanH", sort_order=20)


def _duty_data(*rows, **data):
    starts_at = timezone.localtime() + timedelta(days=3)
    values = {
        "title": "City festival",
        "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": (starts_at + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
        "pay_type": "hourly",
        "pay_rate": "10",
        "all_units": "True",
        **positions_data(*rows),
    }
    values.update(data)
    return values


def _duty_with_positions(paramedic, first_aider, **fields):
    duty = Duty.objects.create(
        title="Marathon",
        starts_at=timezone.now() + timedelta(days=5),
        ends_at=timezone.now() + timedelta(days=5, hours=4),
        helpers_needed=5,
        status=Duty.Status.RELEASED,
        pay_type=Duty.PayType.HOURLY,
        pay_rate=Decimal("10"),
        **fields,
    )
    rs = DutyPosition.objects.create(
        duty=duty, qualification=paramedic, count=2, pay_rate=Decimal("15")
    )
    sanh = DutyPosition.objects.create(duty=duty, qualification=first_aider, count=3)
    return duty, rs, sanh


# --- The catalogue of qualifications ---------------------------------------------------------


def test_dispatcher_maintains_qualifications(client, dispatcher):
    client.force_login(dispatcher)
    assert client.get(reverse("qualification_list")).status_code == 200
    response = client.post(
        reverse("qualification_list"),
        {
            "qualifications-TOTAL_FORMS": "3",
            "qualifications-INITIAL_FORMS": "0",
            "qualifications-0-name": "Paramedic",
            "qualifications-0-abbreviation": "RS",
            "qualifications-0-sort_order": "10",
            "qualifications-0-is_active": "on",
            "qualifications-1-name": "Emergency doctor",
            "qualifications-1-sort_order": "5",
            # What a browser sends for the empty row: the preset values.
            "qualifications-2-sort_order": "0",
            "qualifications-2-is_active": "on",
        },
    )
    assert response["Location"] == reverse("qualification_list")
    assert [str(q) for q in Qualification.objects.all()] == ["Emergency doctor", "Paramedic (RS)"]
    assert not Qualification.objects.get(name="Emergency doctor").is_active


def test_catalogue_rows(client, dispatcher):
    client.force_login(dispatcher)
    # Empty: one row to start with.
    page = client.get(reverse("qualification_list")).text
    assert 'name="qualifications-0-name"' in page
    assert 'value="10"' in page
    Qualification.objects.create(name="Paramedic", sort_order=20)
    # Otherwise only the saved ones; new rows come last.
    page = client.get(reverse("qualification_list")).text
    assert 'name="qualifications-1-name"' not in page
    assert "data-formset-add" in page
    assert 'name="qualifications-__prefix__-sort_order" value="30"' in page


def test_empty_new_qualification_rows_are_ignored(client, dispatcher):
    client.force_login(dispatcher)
    response = client.post(
        reverse("qualification_list"),
        {
            "qualifications-TOTAL_FORMS": "2",
            "qualifications-INITIAL_FORMS": "0",
            "qualifications-0-name": "Paramedic",
            "qualifications-0-sort_order": "10",
            # Rendered before another dispatcher added a qualification: an outdated order.
            "qualifications-1-sort_order": "10",
            "qualifications-1-is_active": "on",
        },
    )
    assert response["Location"] == reverse("qualification_list")
    assert [q.name for q in Qualification.objects.all()] == ["Paramedic"]


@pytest.mark.parametrize("user", ["unit_lead", "member"])
def test_units_cannot_change_qualifications(client, request, user):
    client.force_login(request.getfixturevalue(user))
    assert client.get(reverse("qualification_list")).status_code == 403


# --- Positions of a duty ---------------------------------------------------------------------


def test_duty_with_positions(client, dispatcher, paramedic, first_aider):
    client.force_login(dispatcher)
    client.post(
        reverse("duty_create"),
        _duty_data(
            {"qualification": paramedic.pk, "count": "2", "pay_rate": "15"},
            {"qualification": first_aider.pk, "count": "3"},
        ),
    )
    duty = Duty.objects.get()
    assert duty.helpers_needed == 5
    assert [(p.label, p.count, p.rate) for p in duty.positions.all()] == [
        ("Paramedic (RS)", 2, Decimal("15.00")),
        ("First aider (SanH)", 3, Decimal("10.00")),
    ]


def test_positions_need_a_qualification(client, dispatcher, paramedic):
    client.force_login(dispatcher)
    response = client.post(reverse("duty_create"), _duty_data({"qualification": "", "count": "3"}))
    assert "This field is required." in response.text
    assert not Duty.objects.exists()
    assert "Any qualification" not in client.get(reverse("duty_create")).text


def test_duty_form_asks_for_qualifications_first(client, dispatcher):
    client.force_login(dispatcher)
    assert "there are none yet" in client.get(reverse("duty_create")).text


def test_qualification_of_positions_with_helpers_stays(
    client, dispatcher, unit, paramedic, first_aider
):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, position=rs)
    client.force_login(dispatcher)
    other = Qualification.objects.create(name="Doctor", sort_order=5)
    data = _duty_data(**positions_data(duty=duty))
    data["title"] = duty.title
    data["starts_at"] = timezone.localtime(duty.starts_at).strftime("%Y-%m-%dT%H:%M")
    data["ends_at"] = timezone.localtime(duty.ends_at).strftime("%Y-%m-%dT%H:%M")
    data["positions-0-qualification"] = other.pk
    response = client.post(reverse("duty_update", args=[duty.pk]), data)
    assert "its qualification stays" in response.text
    rs.refresh_from_db()
    assert rs.qualification == paramedic


def test_duty_form_rows(client, dispatcher, paramedic, first_aider):
    client.force_login(dispatcher)
    page = client.get(reverse("duty_create")).text
    assert 'name="positions-0-count"' in page
    assert 'name="positions-1-count"' not in page
    assert 'name="positions-__prefix__-count"' in page
    # The rates only matter with duty pay.
    assert 'data-hidden-if="pay_type=none"' in page
    duty, _rs, _sanh = _duty_with_positions(paramedic, first_aider)
    page = client.get(reverse("duty_update", args=[duty.pk])).text
    assert 'name="positions-1-count"' in page
    assert 'name="positions-2-count"' not in page


def test_each_qualification_only_once(client, dispatcher, paramedic):
    client.force_login(dispatcher)
    response = client.post(
        reverse("duty_create"),
        _duty_data(
            {"qualification": paramedic.pk, "count": "2"},
            {"qualification": paramedic.pk, "count": "1"},
        ),
    )
    assert "This qualification is listed twice." in response.text
    assert not Duty.objects.exists()


def test_staffing_is_limited(client, dispatcher, paramedic, first_aider):
    client.force_login(dispatcher)
    response = client.post(
        reverse("duty_create"),
        _duty_data({"qualification": paramedic.pk, "count": "10001"}),
    )
    assert response.status_code == 200
    assert not Duty.objects.exists()
    response = client.post(
        reverse("duty_create"),
        _duty_data(
            {"qualification": paramedic.pk, "count": "6000"},
            {"qualification": first_aider.pk, "count": "6000"},
        ),
    )
    assert "at most 10000 helpers in total" in response.text
    assert not Duty.objects.exists()


def test_a_duty_needs_a_position(client, dispatcher):
    client.force_login(dispatcher)
    response = client.post(reverse("duty_create"), _duty_data())
    assert response.status_code == 200
    assert not Duty.objects.exists()


def test_positions_with_declined_reports_stay(client, dispatcher, unit, paramedic, first_aider):
    """Declined helpers can still be confirmed, for the position they were reported for."""
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, position=rs, status=Report.Status.DECLINED)
    client.force_login(dispatcher)
    data = _duty_data(**positions_data(duty=duty))
    data["positions-0-qualification"] = first_aider.pk
    data["positions-1-qualification"] = paramedic.pk
    response = client.post(reverse("duty_update", args=[duty.pk]), data)
    assert "its qualification stays" in response.text
    data = _duty_data(**positions_data(duty=duty))
    data["positions-0-DELETE"] = "on"
    response = client.post(reverse("duty_update", args=[duty.pk]), data)
    assert "keep them: Paramedic (RS)" in response.text
    assert list(duty.positions.order_by("pk")) == [rs, sanh]
    assert duty.positions.get(pk=rs.pk).qualification == paramedic


def test_positions_with_reports_cannot_be_removed(client, dispatcher, unit, paramedic, first_aider):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, position=rs)
    client.force_login(dispatcher)
    data = _duty_data(**positions_data(duty=duty))
    data["positions-0-DELETE"] = "on"
    response = client.post(reverse("duty_update", args=[duty.pk]), data)
    assert (
        "Helpers have been reported for these positions; keep them: Paramedic (RS)" in response.text
    )
    assert duty.positions.count() == 2
    del data["positions-0-DELETE"]
    data["positions-1-DELETE"] = "on"
    client.post(reverse("duty_update", args=[duty.pk]), data)
    assert list(duty.positions.all()) == [rs]
    duty.refresh_from_db()
    assert duty.helpers_needed == 2


def test_inactive_qualifications_only_for_existing_positions(dispatcher, paramedic, first_aider):
    from duties.forms import DutyPositionFormSet

    duty, rs, _sanh = _duty_with_positions(paramedic, first_aider)
    paramedic.is_active = False
    paramedic.save()
    formset = DutyPositionFormSet(instance=duty, prefix="positions")
    existing, _other = formset.forms
    assert paramedic in existing.fields["qualification"].queryset
    assert paramedic not in formset.empty_form.fields["qualification"].queryset


def test_without_duty_pay_positions_have_no_rate(client, dispatcher, paramedic):
    client.force_login(dispatcher)
    client.post(
        reverse("duty_create"),
        _duty_data(
            {"qualification": paramedic.pk, "count": "2", "pay_rate": "15"}, pay_type="none"
        ),
    )
    assert DutyPosition.objects.get().pay_rate is None


def _update_data(duty, **data):
    values = _duty_data(**positions_data(duty=duty))
    values["title"] = duty.title
    values["starts_at"] = timezone.localtime(duty.starts_at).strftime("%Y-%m-%dT%H:%M")
    values["ends_at"] = timezone.localtime(duty.ends_at).strftime("%Y-%m-%dT%H:%M")
    values.update(data)
    return values


def test_changed_positions_are_recorded_but_not_announced(
    client, dispatcher, unit, unit_lead, paramedic, first_aider, mailoutbox
):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, position=sanh)
    client.force_login(dispatcher)
    page = client.get(reverse("duty_update", args=[duty.pk])).text
    assert "Changing the helpers needed sends no email." in page
    client.post(
        reverse("duty_update", args=[duty.pk]), _update_data(duty, **{"positions-1-count": "2"})
    )
    duty.refresh_from_db()
    assert duty.helpers_needed == 4
    assert mailoutbox == []
    (entry,) = history.entries(history.scope_for_duty(duty.pk))
    change = next(item for item in entry.changes if item["field"] == "positions")
    assert change["old"] == ["2 × Paramedic (RS)", "3 × First aider (SanH)"]
    assert change["new"] == ["2 × First aider (SanH)", "2 × Paramedic (RS)"]  # sorted
    page = client.get(duty.get_absolute_url()).text
    assert "Positions: <del>2 × Paramedic (RS), 3 × First aider (SanH)</del>" in page


def test_other_changes_are_still_announced(
    client, dispatcher, unit, unit_lead, paramedic, first_aider, mailoutbox
):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, position=sanh)
    client.force_login(dispatcher)
    client.post(
        reverse("duty_update", args=[duty.pk]),
        _update_data(duty, title="City marathon", **{"positions-1-count": "2"}),
    )
    (message,) = mailoutbox
    assert "(title)" in message.body
    assert "positions" not in message.body


def test_warning_when_more_helpers_are_confirmed_than_needed(
    client, dispatcher, unit, paramedic, first_aider
):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    for name in ["A", "B", "C"]:
        create_report(duty, unit, name=name, position=sanh, status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    response = client.post(
        reverse("duty_update", args=[duty.pk]),
        _update_data(duty, **{"positions-0-count": "1", "positions-1-count": "1"}),
        follow=True,
    )
    assert "More helpers are confirmed (3) than needed (2)." in response.text


# --- Reporting for a position ----------------------------------------------------------------


def _report_data(*rows):
    data = {"form-TOTAL_FORMS": str(len(rows)), "form-INITIAL_FORMS": "0"}
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f"form-{index}-{field}"] = value
    return data


def test_units_report_helpers_for_positions(client, member, paramedic, first_aider):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert "Paramedic (RS)" in page
    client.post(
        reverse("report_create", args=[duty.pk]),
        _report_data(
            {"helper_qualification": paramedic.pk, "name": "Rita"},
            {"helper_qualification": first_aider.pk, "name": "Sam"},
            {"helper_qualification": paramedic.pk},  # an empty row, preselected
        ),
    )
    assert {(r.name, r.helper_qualification, r.position) for r in Report.objects.all()} == {
        ("Rita", paramedic, rs),
        ("Sam", first_aider, sanh),
    }


def test_empty_rows_stay_empty_when_places_fill_up(client, member, paramedic, first_aider):
    """The page preselected RS; meanwhile RS is taken, so SanH would be preselected now."""
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    client.force_login(member)
    create_report(duty, member.unit, name="A", position=rs)
    create_report(duty, member.unit, name="B", position=rs)
    response = client.post(
        reverse("report_create", args=[duty.pk]),
        _report_data(
            {"helper_qualification": paramedic.pk, "name": "Rita"},
            {"helper_qualification": paramedic.pk},  # added in the browser, left empty
        ),
    )
    assert response.status_code == 302
    assert Report.objects.get(name="Rita").position == rs
    assert Report.objects.count() == 3


def test_choices_show_the_open_places(client, member, unit, other_unit, paramedic, first_aider):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, name="A", position=rs, status=Report.Status.CONFIRMED)
    create_report(duty, other_unit, name="B", position=rs)
    create_report(duty, unit, name="C", position=sanh, status=Report.Status.DECLINED)
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert ">Paramedic (RS) – all taken</option>" in page
    # Preselected: the first qualification with open places.
    assert f'value="{first_aider.pk}" selected>First aider (SanH) – 3 open</option>' in page
    assert "<th>Qualification</th>" in page


def test_single_qualification_is_named(client, member, paramedic):
    duty = create_duty(helpers_needed=4)
    duty.positions.update(qualification=paramedic)
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert "Needed: 4 helpers with the qualification <strong>Paramedic (RS)</strong>." in page
    assert 'name="form-0-helper_qualification"' not in page


def test_reports_show_the_qualification(
    client, dispatcher, unit, unit_lead, paramedic, first_aider, mailoutbox
):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    client.force_login(dispatcher)
    client.post(
        reverse("report_add", args=[duty.pk]),
        {
            "unit": unit.pk,
            **_report_data(
                {"helper_qualification": paramedic.pk, "name": "Rita"},
                {"helper_qualification": first_aider.pk, "name": "Ann"},
            ),
        },
    )
    assert "- Rita (RS)\n- Ann (SanH)\n" in mailoutbox[0].body
    # Older reports have the qualification as free text.
    create_report(duty, unit, name="Olga", qualification="Doctor")
    page = client.get(duty.get_absolute_url()).text
    assert "<td>Paramedic (RS)</td>" in page
    assert "<td>Doctor</td>" in page


def test_positions_are_listed_in_the_order_of_the_qualifications(
    client, member, paramedic, first_aider
):
    duty, _rs, _sanh = _duty_with_positions(paramedic, first_aider)
    paramedic.sort_order = 30
    paramedic.save()
    client.force_login(member)
    page = client.get(duty.get_absolute_url()).text
    assert page.index("3 × First aider (SanH)") < page.index("2 × Paramedic (RS)")


def test_qualification_must_fit_a_position(client, member, paramedic, first_aider):
    duty, _rs, _sanh = _duty_with_positions(paramedic, first_aider)
    doctor = Qualification.objects.create(name="Doctor")
    client.force_login(member)
    assert "Doctor" not in client.get(reverse("report_create", args=[duty.pk])).text
    response = client.post(
        reverse("report_create", args=[duty.pk]),
        _report_data({"helper_qualification": doctor.pk, "name": "Rita"}),
    )
    assert response.status_code == 200
    assert not Report.objects.exists()


def test_single_position_needs_no_choice(client, member):
    duty = create_duty()
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert 'name="form-0-helper_qualification"' not in page
    client.post(reverse("report_create", args=[duty.pk]), _report_data({"name": "Mia"}))
    report = Report.objects.get()
    assert report.position == duty.positions.get()
    assert report.helper_qualification == report.position.qualification


def test_staffing_per_position(client, member, unit, paramedic, first_aider):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, name="A", position=rs, status=Report.Status.CONFIRMED)
    create_report(duty, unit, name="B", position=rs, status=Report.Status.CONFIRMED)
    create_report(duty, unit, name="C", position=sanh)
    client.force_login(member)
    page = client.get(duty.get_absolute_url()).text
    assert "2 × Paramedic (RS) · 15.00 € per helper hour" in page
    assert ">2/2</span>" in page
    assert ">0/3</span>" in page


# --- Higher qualifications count for lower ones -------------------------------------------------


def _positions(*rows):
    return [
        SimpleNamespace(pk=pk, qualification_id=qualification, count=count)
        for pk, qualification, count in rows
    ]


def _counts(positions, reports, includes):
    """Runs the staffing count on plain positions (pk, qualification, count)."""
    positions = _positions(*positions)
    staffing.count(positions, reports, includes)
    return {p.pk: (p.confirmed_count, p.pending_count) for p in positions}


CONFIRMED, PENDING = Report.Status.CONFIRMED, Report.Status.REPORTED
# Qualifications: 3 emergency paramedic counts as 2 paramedic, which counts as 1 first aider.
CHAIN = {1: None, 2: 1, 3: 2}


def test_confirmed_helpers_count_where_they_are_assigned():
    # 2 first aiders (10) and 1 paramedic (20) needed; the second paramedic fills 10.
    reports = [(1, 10, 10, CONFIRMED), (2, 20, 20, CONFIRMED), (2, 20, 10, CONFIRMED)]
    assert _counts([(10, 1, 2), (20, 2, 1)], reports, CHAIN) == {10: (2, 0), 20: (1, 0)}


def test_pending_helpers_take_the_places_left():
    positions = [(10, 1, 2), (20, 2, 1)]
    reports = [
        (2, 20, None, PENDING),
        (2, 20, 20, CONFIRMED),
        (1, 10, None, PENDING),
        (2, 20, None, PENDING),
    ]
    assert _counts(positions, reports, CHAIN) == {10: (0, 2), 20: (1, 1)}
    # First aiders never fill places for paramedics; they overstaff their own position.
    reports = [(1, 10, None, PENDING)] * 3
    assert _counts(positions, reports, CHAIN) == {10: (0, 3), 20: (0, 0)}


def test_pending_helpers_count_with_their_own_qualification():
    # Emergency paramedics (3) reported for the paramedic's position (20).
    reports = [(3, 20, None, PENDING)] * 2
    assert _counts([(10, 1, 1), (20, 2, 1)], reports, CHAIN) == {10: (0, 1), 20: (0, 1)}
    # A position for emergency paramedics added later is theirs.
    positions = [(10, 1, 1), (20, 2, 1), (30, 3, 1)]
    assert _counts(positions, reports, CHAIN) == {10: (0, 0), 20: (0, 1), 30: (0, 1)}


def test_newly_confirmed_helpers_fill_the_closest_free_position():
    san, rs = _positions((10, 1, 1), (20, 2, 1))
    positions = [san, rs]
    assert staffing.assign(2, rs, positions, Counter(), CHAIN) is rs
    assert staffing.assign(2, rs, positions, Counter({20: 1}), CHAIN) is san
    assert staffing.assign(2, rs, positions, Counter({10: 1, 20: 1}), CHAIN) is rs  # overstaffed
    assert staffing.assign(1, san, positions, Counter({10: 1}), CHAIN) is san
    # An emergency paramedic, reported for the paramedic's position.
    assert staffing.assign(3, rs, positions, Counter(), CHAIN) is rs
    # Down the chain, the closest first.
    notsan = _positions((30, 3, 1))[0]
    assert staffing.fitting(3, [san, rs, notsan], CHAIN) == [notsan, rs, san]
    assert staffing.fitting(None, positions, CHAIN) == []


def test_circles_do_not_hang():
    assert staffing.chain(1, {1: 2, 2: 1}) == [1, 2]


def _confirm(client, report):
    client.post(reverse("report_confirm", args=[report.pk]))
    report.refresh_from_db()
    return report


def test_dispatcher_chooses_who_fills_which_position(
    client, dispatcher, unit, paramedic, first_aider
):
    """1 paramedic and 1 first aider needed, the unit reports 2 paramedics."""
    paramedic.includes = first_aider
    paramedic.save()
    duty = create_duty(pay_type=Duty.PayType.FLAT, pay_rate=Decimal("10"))
    duty.positions.all().delete()
    sanh = DutyPosition.objects.create(duty=duty, qualification=first_aider, count=1)
    rs = DutyPosition.objects.create(
        duty=duty, qualification=paramedic, count=1, pay_rate=Decimal("15")
    )
    rita = create_report(duty, unit, name="Rita", position=rs)
    ralf = create_report(duty, unit, name="Ralf", position=rs)
    client.force_login(dispatcher)
    assert _confirm(client, rita).assigned_position == rs
    assert _confirm(client, ralf).assigned_position == sanh
    assert (rita.pay_rate, ralf.pay_rate) == (Decimal("15.00"), Decimal("10.00"))
    page = client.get(duty.get_absolute_url()).text
    assert "<th>Assigned to</th>" in page
    assert page.count('<span class="badge text-bg-success">1/1</span>') == 2
    assert reverse("report_assign", args=[ralf.pk]) in page
    # Swap them.
    client.post(reverse("report_assign", args=[rita.pk]), {"position": sanh.pk})
    client.post(reverse("report_assign", args=[ralf.pk]), {"position": rs.pk})
    rita.refresh_from_db()
    ralf.refresh_from_db()
    assert (rita.assigned_position, ralf.assigned_position) == (sanh, rs)
    assert (rita.pay_rate, ralf.pay_rate) == (Decimal("10.00"), Decimal("15.00"))
    # Recorded in the history.
    page = client.get(duty.get_absolute_url()).text
    assert "Assigned to: <del>Paramedic (RS)</del>" in page


def test_assignments_only_where_the_helper_fits(
    client, dispatcher, member, unit, paramedic, first_aider
):
    paramedic.includes = first_aider
    paramedic.save()
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    sam = create_report(duty, unit, name="Sam", position=sanh, status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    # A first aider cannot fill a paramedic's place, so there is nothing to choose.
    assert reverse("report_assign", args=[sam.pk]) not in client.get(duty.get_absolute_url()).text
    client.post(reverse("report_assign", args=[sam.pk]), {"position": rs.pk})
    sam.refresh_from_db()
    assert sam.assigned_position is None
    # Only dispatchers, and only for confirmed helpers.
    rita = create_report(duty, unit, name="Rita", position=rs)
    client.post(reverse("report_assign", args=[rita.pk]), {"position": sanh.pk})
    rita.refresh_from_db()
    assert rita.assigned_position is None
    client.force_login(member)
    assert client.post(reverse("report_assign", args=[rita.pk])).status_code == 403


def test_billed_helpers_keep_their_position(client, dispatcher, unit, paramedic, first_aider):
    paramedic.includes = first_aider
    paramedic.save()
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    rita = create_report(
        duty, unit, name="Rita", position=rs, status=Report.Status.CONFIRMED, assigned_position=rs
    )
    services.create_billing(unit, [(rita, Decimal(4))], "", dispatcher)
    client.force_login(dispatcher)
    response = client.post(
        reverse("report_assign", args=[rita.pk]), {"position": sanh.pk}, follow=True
    )
    assert "This helper has already been billed." in response.text
    rita.refresh_from_db()
    assert rita.assigned_position == rs


def test_helpers_added_by_dispatchers_are_assigned(
    client, dispatcher, unit, paramedic, first_aider
):
    paramedic.includes = first_aider
    paramedic.save()
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)  # 2 × RS, 3 × SanH
    client.force_login(dispatcher)
    client.post(
        reverse("report_add", args=[duty.pk]),
        {
            "unit": unit.pk,
            **_report_data(*({"helper_qualification": paramedic.pk, "name": n} for n in "ABC")),
        },
    )
    assigned = sorted(r.assigned_position.label for r in Report.objects.all())
    assert assigned == ["First aider (SanH)", "Paramedic (RS)", "Paramedic (RS)"]


def test_choices_count_higher_qualifications(client, member, unit, paramedic, first_aider):
    paramedic.includes = first_aider
    paramedic.save()
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    for name, position in [("A", rs), ("B", rs), ("C", sanh)]:
        create_report(
            duty,
            unit,
            name=name,
            position=rs,
            status=Report.Status.CONFIRMED,
            assigned_position=position,
        )
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert ">Paramedic (RS) – all taken</option>" in page
    assert ">First aider (SanH) – 2 open</option>" in page


@pytest.fixture
def emergency_paramedic(paramedic, first_aider):
    """Counts as paramedic, which counts as first aider."""
    paramedic.includes = first_aider
    paramedic.save()
    return Qualification.objects.create(
        name="Emergency paramedic", abbreviation="NFS", sort_order=5, includes=paramedic
    )


def test_units_report_higher_qualifications(
    client, member, dispatcher, unit, paramedic, first_aider, emergency_paramedic, mailoutbox
):
    duty, rs, _sanh = _duty_with_positions(paramedic, first_aider)
    Qualification.objects.create(name="Doctor")  # counts as nothing asked for
    Qualification.objects.create(name="Retired", includes=paramedic, is_active=False)
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert ">Emergency paramedic (NFS) – counts as RS, SanH</option>" in page
    assert "Doctor" not in page and "Retired" not in page
    # Preselected is still the first position with open places.
    assert f'value="{paramedic.pk}" selected>Paramedic (RS) – 2 open</option>' in page
    client.post(
        reverse("report_create", args=[duty.pk]),
        _report_data({"helper_qualification": emergency_paramedic.pk, "name": "Nina"}),
    )
    nina = Report.objects.get()
    assert (nina.helper_qualification, nina.position) == (emergency_paramedic, rs)
    assert "- Nina (NFS)" in mailoutbox[0].body
    client.force_login(dispatcher)
    page = client.get(duty.get_absolute_url()).text
    assert "<td>Emergency paramedic (NFS)</td>" in page
    assert ">0/2</span>" in page  # pending for the paramedic's position


def test_higher_qualifications_fill_the_closest_free_position(
    client, dispatcher, unit, paramedic, first_aider, emergency_paramedic
):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)  # 2 × RS, 3 × SanH
    for name in "AB":
        create_report(duty, unit, name=name, position=rs, status=Report.Status.CONFIRMED)
    nina = create_report(
        duty, unit, name="Nina", position=rs, helper_qualification=emergency_paramedic
    )
    client.force_login(dispatcher)
    assert _confirm(client, nina).assigned_position == sanh
    # The dispatchers may move her to the paramedic's position.
    page = client.get(duty.get_absolute_url()).text
    assert f'<option value="{rs.pk}">Paramedic (RS)</option>' in page
    client.post(reverse("report_assign", args=[nina.pk]), {"position": rs.pk})
    nina.refresh_from_db()
    assert nina.assigned_position == rs


def test_single_position_offers_higher_qualifications(client, member, unit, paramedic, first_aider):
    paramedic.includes = first_aider
    paramedic.save()
    duty = create_duty(helpers_needed=3)
    sanh = duty.positions.get()
    sanh.qualification = first_aider
    sanh.save()
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert "Needed: 3 helpers with the qualification <strong>First aider (SanH)</strong>." in page
    assert f'value="{first_aider.pk}" selected>First aider (SanH) – 3 open</option>' in page
    assert ">Paramedic (RS) – counts as SanH</option>" in page
    client.post(
        reverse("report_create", args=[duty.pk]),
        _report_data({"helper_qualification": paramedic.pk, "name": "Rita"}),
    )
    rita = Report.objects.get()
    assert (rita.helper_qualification, rita.position) == (paramedic, sanh)


def test_catalogue_sets_what_a_qualification_counts_as(client, dispatcher, paramedic, first_aider):
    client.force_login(dispatcher)
    data = {
        "qualifications-TOTAL_FORMS": "2",
        "qualifications-INITIAL_FORMS": "2",
        "qualifications-0-id": paramedic.pk,
        "qualifications-0-name": "Paramedic",
        "qualifications-0-includes": first_aider.pk,
        "qualifications-0-sort_order": "10",
        "qualifications-0-is_active": "on",
        "qualifications-1-id": first_aider.pk,
        "qualifications-1-name": "First aider",
        "qualifications-1-sort_order": "20",
        "qualifications-1-is_active": "on",
    }
    page = client.get(reverse("qualification_list")).text
    assert "Also counts as" in page
    client.post(reverse("qualification_list"), data)
    paramedic.refresh_from_db()
    assert paramedic.includes == first_aider
    # Not in circles.
    data["qualifications-1-includes"] = paramedic.pk
    response = client.post(reverse("qualification_list"), data)
    assert "These qualifications count as each other: First aider, Paramedic" in response.text
    first_aider.refresh_from_db()
    assert first_aider.includes is None


# --- Rates per position ------------------------------------------------------------------------


def test_billing_uses_the_rate_of_the_position(dispatcher, unit, paramedic, first_aider):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    Duty.objects.filter(pk=duty.pk).update(
        starts_at=timezone.now() - timedelta(days=2),
        ends_at=timezone.now() - timedelta(days=2) + timedelta(hours=4),
    )
    rita = create_report(duty, unit, name="Rita", position=rs, status=Report.Status.CONFIRMED)
    sam = create_report(duty, unit, name="Sam", position=sanh, status=Report.Status.CONFIRMED)
    assert services.units_with_billable_reports() == [(unit, 2, Decimal("100.00"))]
    billing = services.create_billing(unit, [(rita, Decimal(4)), (sam, Decimal(4))], "", dispatcher)
    items = {item.helper_display: (item.rate, item.amount) for item in billing.items.all()}
    assert items == {
        "Rita (RS)": (Decimal("15.00"), Decimal("60.00")),
        "Sam (SanH)": (Decimal("10.00"), Decimal("40.00")),
    }


@pytest.mark.django_db(transaction=True)
def test_migrations_fill_in_qualifications_and_assignments():
    before = [("duties", "0006_qualification_includes")]
    after = [("duties", "0008_position_qualification_required")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    unit = old.get_model("accounts", "Unit").objects.create(name="Old unit")
    duty = old.get_model("duties", "Duty").objects.create(
        title="Old",
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(hours=1),
        helpers_needed=4,
    )
    position = old.get_model("duties", "DutyPosition").objects.create(duty=duty, count=4)
    reports = old.get_model("duties", "Report").objects
    confirmed = reports.create(
        duty=duty, unit=unit, name="Confirmed", position=position, status="confirmed"
    )
    pending = reports.create(duty=duty, unit=unit, name="Pending", position=position)
    executor = MigrationExecutor(connection)
    executor.migrate(after)
    new = executor.loader.project_state(after).apps
    position = new.get_model("duties", "DutyPosition").objects.get(pk=position.pk)
    assert position.qualification.name == "Helfer"
    reports = new.get_model("duties", "Report").objects
    assert reports.get(pk=confirmed.pk).assigned_position == position
    assert reports.get(pk=pending.pk).assigned_position is None
    # Helpers get the qualification of their position.
    executor = MigrationExecutor(connection)
    executor.migrate([("duties", "0009_report_helper_qualification")])
    for report in [confirmed, pending]:
        assert Report.objects.get(pk=report.pk).helper_qualification_id == position.qualification_id
    # On to the latest state for the tests after this one.
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_positions_table_in_german(client, dispatcher):
    dispatcher.language = "de"
    dispatcher.save()
    client.force_login(dispatcher)
    page = client.get(reverse("duty_create")).text
    assert '<th class="col-width-7">Anzahl</th>' in page


def test_confirmations_wait_for_each_other(client, monkeypatch, dispatcher, unit, paramedic):
    """Two confirmations at the same time never take the same free place."""
    duty, rs, _sanh = _duty_with_positions(paramedic, Qualification.objects.create(name="X"))
    first = create_report(duty, unit, name="A", position=rs)
    second = create_report(duty, unit, name="B", position=rs)
    calls = []
    assign = Duty.assign_position
    monkeypatch.setattr(Duty, "lock_staffing", lambda self: calls.append(("lock", self.pk)))
    monkeypatch.setattr(
        Duty,
        "assign_position",
        lambda self, report: calls.append(("assign", report.pk)) or assign(self, report),
    )
    client.force_login(dispatcher)
    client.post(reverse("report_confirm_all", args=[duty.pk]))
    assert calls == [("lock", duty.pk), ("assign", first.pk), ("assign", second.pk)]
    calls.clear()
    third = create_report(duty, unit, name="C", position=rs)
    client.post(reverse("report_confirm", args=[third.pk]))
    assert calls == [("lock", duty.pk), ("assign", third.pk)]


def test_lock_staffing_locks_the_duty(db):
    duty = create_duty()
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        duty.lock_staffing()
    (query,) = queries
    assert "duties_duty" in query["sql"]
    if connection.features.has_select_for_update:
        assert "FOR UPDATE" in query["sql"]


@pytest.mark.django_db(transaction=True)
def test_migration_merges_positions_listed_twice():
    """0007 gave positions for any qualification "Helfer", also next to a Helfer position."""
    before = [("duties", "0006_qualification_includes")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    helper = old.get_model("duties", "Qualification").objects.create(name="Helfer", sort_order=10)
    unit = old.get_model("accounts", "Unit").objects.create(name="Old unit")
    duty = old.get_model("duties", "Duty").objects.create(
        title="Old",
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(hours=1),
        helpers_needed=5,
    )
    positions = old.get_model("duties", "DutyPosition").objects
    helpers = positions.create(duty=duty, qualification=helper, count=2)
    anyone = positions.create(duty=duty, count=3)
    reports = old.get_model("duties", "Report").objects
    confirmed = reports.create(
        duty=duty, unit=unit, name="Confirmed", position=anyone, status="confirmed"
    )
    pending = reports.create(duty=duty, unit=unit, name="Pending", position=helpers)
    executor = MigrationExecutor(connection)
    executor.migrate([("duties", "0010_merge_duplicate_positions")])
    (position,) = DutyPosition.objects.filter(duty_id=duty.pk)
    assert (position.pk, position.qualification_id, position.count) == (helpers.pk, helper.pk, 5)
    confirmed = Report.objects.get(pk=confirmed.pk)
    assert (confirmed.position_id, confirmed.assigned_position_id) == (helpers.pk, helpers.pk)
    assert Report.objects.get(pk=pending.pk).position_id == helpers.pk
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
