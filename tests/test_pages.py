"""Smoke test: every page renders without template errors."""

import pytest
from django.urls import reverse

from duties.models import Duty

from .conftest import create_duty, create_report


@pytest.mark.parametrize("name", ["account_login", "account_reset_password", "account_signup"])
def test_public_pages_render(client, db, name):
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.parametrize(
    "name",
    [
        "home",
        "profile",
        "account_email",
        "account_change_password",
        "account_reauthenticate",
        "account_logout",
        "socialaccount_connections",
        "mfa_index",
        "mfa_activate_totp",
        "invitation_list",
        "invitation_create",
        "unit_list",
        "unit_create",
        "duty_list",
        "duty_create",
    ],
)
def test_pages_for_dispatchers_render(client, dispatcher, name):
    client.force_login(dispatcher)
    assert client.get(reverse(name)).status_code in (200, 302)


def test_unit_pages_render(client, dispatcher, unit_lead, member, unit):
    client.force_login(dispatcher)
    for url in [
        reverse("unit_detail", args=[unit.pk]),
        reverse("unit_update", args=[unit.pk]),
        reverse("invitation_create") + f"?unit={unit.pk}",
    ]:
        assert client.get(url).status_code == 200, url


def test_duty_pages_render(client, dispatcher, unit_lead, unit):
    draft = create_duty(status=Duty.Status.DRAFT)
    duty = create_duty()
    create_report(duty, unit, reported_by=unit_lead)
    client.force_login(dispatcher)
    for url in [
        reverse("home"),
        reverse("duty_list") + "?show=past",
        draft.get_absolute_url(),
        duty.get_absolute_url(),
        reverse("duty_update", args=[duty.pk]),
    ]:
        assert client.get(url).status_code == 200, url
    client.force_login(unit_lead)
    for url in [
        reverse("home"),
        reverse("duty_list"),
        duty.get_absolute_url(),
        reverse("report_create", args=[duty.pk]),
    ]:
        assert client.get(url).status_code == 200, url


def test_home_shows_role(client, unit_lead):
    client.force_login(unit_lead)
    response = client.get(reverse("home"))
    assert "Unit lead" in response.text
    assert "My unit" in response.text


def test_unknown_page_renders_404(client, member):
    client.force_login(member)
    assert client.get("/does-not-exist/").status_code == 404
