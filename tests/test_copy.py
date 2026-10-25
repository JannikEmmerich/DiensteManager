"""Copying a duty: the new duty form is prefilled, the copy is a draft without reports."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from duties.models import Duty, DutyPosition, Qualification

from .conftest import create_duty, create_report, positions_data


@pytest.fixture
def paramedic(db):
    return Qualification.objects.create(name="Paramedic", abbreviation="RS", sort_order=10)


@pytest.fixture
def first_aider(db):
    return Qualification.objects.create(name="First aider", abbreviation="SanH", sort_order=20)


@pytest.fixture
def source(paramedic, first_aider, unit):
    duty = create_duty(
        title="Home match",
        description="Gate 3",
        units=[unit],
        pay_type="hourly",
        pay_rate=Decimal("12.50"),
    )
    duty.positions.all().delete()
    DutyPosition.objects.create(duty=duty, qualification=paramedic, count=2)
    DutyPosition.objects.create(
        duty=duty, qualification=first_aider, count=4, pay_rate=Decimal("9.00")
    )
    return duty


def test_copy_prefills_the_form_without_dates(
    client, dispatcher, source, paramedic, first_aider, unit
):
    client.force_login(dispatcher)
    response = client.get(reverse("duty_create"), {"copy": source.pk})
    form, formset = response.context["form"], response.context["formset"]
    assert form["title"].value() == "Home match"
    assert form["description"].value() == "Gate 3"
    assert form["pay_type"].value() == "hourly"
    assert form["pay_rate"].value() == Decimal("12.50")
    assert form["starts_at"].value() is None
    assert form["ends_at"].value() is None
    assert form["units"].value() == [unit.pk]
    assert [(f["count"].value(), f["qualification"].value()) for f in formset] == [
        (2, paramedic.pk),
        (4, first_aider.pk),
    ]
    assert formset.forms[1]["pay_rate"].value() == Decimal("9.00")
    assert "there are none yet" not in response.text  # the qualifications exist


def test_saving_a_copy_creates_a_draft_without_reports(
    client, dispatcher, source, paramedic, first_aider, unit
):
    create_report(source, unit)
    client.force_login(dispatcher)
    starts_at = timezone.localtime() + timedelta(days=10)
    data = {
        "title": "Home match",
        "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": (starts_at + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
        "pay_type": "hourly",
        "pay_rate": "12.50",
        "all_units": "False",
        "units": [unit.pk],
        **positions_data(
            {"qualification": paramedic.pk, "count": 2},
            {"qualification": first_aider.pk, "count": 4},
        ),
    }
    client.post(reverse("duty_create") + f"?copy={source.pk}", data)
    copy = Duty.objects.exclude(pk=source.pk).get()
    assert copy.status == Duty.Status.DRAFT
    assert copy.helpers_needed == 6
    assert not copy.reports.exists()
    assert source.reports.count() == 1


def test_copy_skips_inactive_qualifications(client, dispatcher, source, first_aider):
    first_aider.is_active = False
    first_aider.save()
    client.force_login(dispatcher)
    response = client.get(reverse("duty_create"), {"copy": source.pk})
    assert len(response.context["formset"].forms) == 1
    assert "no longer active" in str(list(response.context["messages"])[0])


def test_copy_with_unknown_id_shows_the_empty_form(client, dispatcher):
    client.force_login(dispatcher)
    for value in ("abc", "999"):
        response = client.get(reverse("duty_create"), {"copy": value})
        assert response.status_code == 200
        assert response.context["form"]["title"].value() is None


def test_copy_button_is_only_for_dispatchers(client, dispatcher, member):
    duty = create_duty()
    url = reverse("duty_detail", args=[duty.pk])
    client.force_login(dispatcher)
    assert f"?copy={duty.pk}" in client.get(url).content.decode()
    client.force_login(member)
    assert "?copy=" not in client.get(url).content.decode()
    assert client.get(reverse("duty_create"), {"copy": duty.pk}).status_code == 403
