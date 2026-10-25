"""Staffing needs per qualification: positions of duties, reports for a position, rates."""

import importlib
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.apps import apps
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


def test_duty_with_positions(client, dispatcher, paramedic):
    client.force_login(dispatcher)
    client.post(
        reverse("duty_create"),
        _duty_data(
            {"qualification": paramedic.pk, "count": "2", "pay_rate": "15"},
            {"qualification": "", "count": "3"},
        ),
    )
    duty = Duty.objects.get()
    assert duty.helpers_needed == 5
    assert [(p.label, p.count, p.rate) for p in duty.positions.all()] == [
        ("Paramedic (RS)", 2, Decimal("15.00")),
        ("any qualification", 3, Decimal("10.00")),
    ]


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


def test_changed_positions_are_recorded_and_announced(
    client, dispatcher, unit, unit_lead, paramedic, first_aider, mailoutbox
):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, position=sanh)
    client.force_login(dispatcher)
    data = _duty_data(**positions_data(duty=duty))
    data["title"] = duty.title
    data["starts_at"] = timezone.localtime(duty.starts_at).strftime("%Y-%m-%dT%H:%M")
    data["ends_at"] = timezone.localtime(duty.ends_at).strftime("%Y-%m-%dT%H:%M")
    data["positions-1-count"] = "4"
    client.post(reverse("duty_update", args=[duty.pk]), data)
    (entry,) = history.entries(history.scope_for_duty(duty.pk))
    change = next(item for item in entry.changes if item["field"] == "positions")
    assert change["old"] == ["2 × Paramedic (RS)", "3 × First aider (SanH)"]
    assert change["new"] == ["2 × Paramedic (RS)", "4 × First aider (SanH)"]
    assert "(positions)" in mailoutbox[0].body
    assert "4 × First aider (SanH)" in mailoutbox[0].body
    page = client.get(duty.get_absolute_url()).text
    assert "Positions: <del>2 × Paramedic (RS), 3 × First aider (SanH)</del>" in page


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
            {"position": rs.pk, "name": "Rita"},
            {"position": sanh.pk, "name": "Sam"},
            {"position": rs.pk},  # an empty row with the preselected position
        ),
    )
    assert {(r.name, r.position) for r in Report.objects.all()} == {("Rita", rs), ("Sam", sanh)}


def test_choices_show_the_open_places(client, member, unit, other_unit, paramedic, first_aider):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    create_report(duty, unit, name="A", position=rs, status=Report.Status.CONFIRMED)
    create_report(duty, other_unit, name="B", position=rs)
    create_report(duty, unit, name="C", position=sanh, status=Report.Status.DECLINED)
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert ">Paramedic (RS) – all taken</option>" in page
    # Preselected: the first qualification with open places.
    assert f'value="{sanh.pk}" selected>First aider (SanH) – 3 open</option>' in page
    assert "<th>Qualification</th>" in page


def test_single_qualification_is_named(client, member, paramedic):
    duty = create_duty(helpers_needed=4)
    duty.positions.update(qualification=paramedic)
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert "Needed: 4 helpers with the qualification <strong>Paramedic (RS)</strong>." in page
    assert 'name="form-0-position"' not in page


def test_reports_show_the_qualification(
    client, dispatcher, unit, unit_lead, paramedic, first_aider, mailoutbox
):
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    any_position = DutyPosition.objects.create(duty=duty, count=1)
    client.force_login(dispatcher)
    client.post(
        reverse("report_add", args=[duty.pk]),
        {
            "unit": unit.pk,
            **_report_data(
                {"position": rs.pk, "name": "Rita"}, {"position": any_position.pk, "name": "Ann"}
            ),
        },
    )
    assert "- Rita (RS)\n- Ann\n" in mailoutbox[0].body
    # Older reports have the qualification as free text.
    create_report(duty, unit, name="Olga", qualification="Doctor")
    page = client.get(duty.get_absolute_url()).text
    assert "<td>Paramedic (RS)</td>" in page
    assert "<td>any qualification</td>" in page
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


def test_position_must_belong_to_the_duty(client, member, paramedic, first_aider):
    duty, rs, _sanh = _duty_with_positions(paramedic, first_aider)
    other, other_rs, _ = _duty_with_positions(paramedic, first_aider)
    client.force_login(member)
    response = client.post(
        reverse("report_create", args=[duty.pk]),
        _report_data({"position": other_rs.pk, "name": "Rita"}),
    )
    assert response.status_code == 200
    assert not Report.objects.exists()


def test_single_position_needs_no_choice(client, member):
    duty = create_duty()
    client.force_login(member)
    assert 'name="form-0-position"' not in client.get(reverse("report_create", args=[duty.pk])).text
    client.post(reverse("report_create", args=[duty.pk]), _report_data({"name": "Mia"}))
    assert Report.objects.get().position == duty.positions.get()


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


def _counts(positions, reports, includes):
    """Runs the staffing count on plain positions (pk, qualification, count)."""
    positions = [
        SimpleNamespace(pk=pk, qualification_id=qualification, count=count)
        for pk, qualification, count in positions
    ]
    staffing.count(positions, reports, includes)
    return {p.pk: (p.confirmed_count, p.pending_count) for p in positions}


CONFIRMED, PENDING = Report.Status.CONFIRMED, Report.Status.REPORTED
# Qualifications: 3 emergency paramedic counts as 2 paramedic, which counts as 1 first aider.
CHAIN = {1: None, 2: 1, 3: 2}


def test_paramedics_fill_places_for_first_aiders():
    # 2 first aiders and 1 paramedic needed; 1 first aider and 2 paramedics confirmed.
    positions = [(10, 1, 2), (20, 2, 1)]
    reports = [(10, CONFIRMED), (20, CONFIRMED), (20, CONFIRMED)]
    assert _counts(positions, reports, CHAIN) == {10: (2, 0), 20: (1, 0)}
    # Without "also counts as", the second paramedic overstaffs their own position.
    assert _counts(positions, reports, {1: None, 2: None}) == {10: (1, 0), 20: (2, 0)}


def test_counting_follows_the_chain_but_not_backwards():
    positions = [(10, 1, 1), (20, 2, 1), (30, 3, 1)]
    # Emergency paramedics fill the places of paramedics and first aiders.
    assert _counts(positions, [(30, CONFIRMED)] * 3, CHAIN) == {
        10: (1, 0),
        20: (1, 0),
        30: (1, 0),
    }
    # First aiders never fill places for paramedics.
    assert _counts(positions, [(10, CONFIRMED), (10, CONFIRMED)], CHAIN) == {
        10: (2, 0),
        20: (0, 0),
        30: (0, 0),
    }


def test_pending_helpers_take_the_places_left():
    positions = [(10, 1, 2), (20, 2, 1)]
    reports = [(20, PENDING), (20, CONFIRMED), (10, PENDING), (20, PENDING)]
    assert _counts(positions, reports, CHAIN) == {10: (0, 2), 20: (1, 1)}


def test_anyone_fills_places_for_any_qualification():
    positions = [(10, 1, 1), (40, None, 2)]
    reports = [(10, CONFIRMED), (10, CONFIRMED), (40, CONFIRMED), (None, CONFIRMED)]
    assert _counts(positions, reports, CHAIN) == {10: (1, 0), 40: (2, 0)}
    # Helpers for any qualification only fill those places.
    assert _counts([(10, 1, 1)], [(None, CONFIRMED)], CHAIN) == {10: (0, 0)}


def test_circles_do_not_hang():
    assert staffing.chain(1, {1: 2, 2: 1}) == [1, 2]


def test_duty_page_counts_higher_qualifications(client, member, unit, paramedic, first_aider):
    paramedic.includes = first_aider
    paramedic.save()
    duty = create_duty(helpers_needed=3)
    duty.positions.all().delete()
    sanh = DutyPosition.objects.create(duty=duty, qualification=first_aider, count=2)
    rs = DutyPosition.objects.create(duty=duty, qualification=paramedic, count=1)
    create_report(duty, unit, name="A", position=sanh, status=Report.Status.CONFIRMED)
    create_report(duty, unit, name="B", position=rs, status=Report.Status.CONFIRMED)
    create_report(duty, unit, name="C", position=rs, status=Report.Status.CONFIRMED)
    client.force_login(member)
    page = client.get(duty.get_absolute_url()).text
    assert '<span class="badge text-bg-success">2/2</span>' in page
    assert '<span class="badge text-bg-success">1/1</span>' in page
    assert "text-bg-warning" not in page
    # The helpers keep the qualification they were reported with.
    assert page.count("<td>Paramedic (RS)</td>") == 2


def test_choices_count_higher_qualifications(client, member, unit, paramedic, first_aider):
    paramedic.includes = first_aider
    paramedic.save()
    duty, rs, sanh = _duty_with_positions(paramedic, first_aider)
    for name in ["A", "B", "C"]:
        create_report(duty, unit, name=name, position=rs, status=Report.Status.CONFIRMED)
    client.force_login(member)
    page = client.get(reverse("report_create", args=[duty.pk])).text
    assert ">Paramedic (RS) – all taken</option>" in page
    assert ">First aider (SanH) – 2 open</option>" in page


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


def test_existing_duties_get_a_position(db, unit):
    migration = importlib.import_module("duties.migrations.0004_qualifications_and_positions")
    duty = Duty.objects.create(
        title="Old",
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(hours=1),
        helpers_needed=4,
    )
    report = Report.objects.create(duty=duty, unit=unit, name="Old helper")
    migration.positions_for_existing_duties(apps, None)
    position = duty.positions.get()
    assert (position.qualification, position.count) == (None, 4)
    report.refresh_from_db()
    assert report.position == position


def test_positions_table_in_german(client, dispatcher):
    dispatcher.language = "de"
    dispatcher.save()
    client.force_login(dispatcher)
    page = client.get(reverse("duty_create")).text
    assert '<th class="col-width-7">Anzahl</th>' in page
