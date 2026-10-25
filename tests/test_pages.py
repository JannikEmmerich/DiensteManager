"""Smoke test: every page renders without template errors."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from billing import services
from billing.models import BankAccount
from duties.models import Duty, Report
from lifecycle import history
from mailings.models import Mailing

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
        "mailing_list",
        "mailing_create",
        "billing_list",
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


def test_mailing_pages_render(client, dispatcher, unit_lead, unit):
    duty = create_duty()
    client.force_login(dispatcher)
    client.post(
        reverse("mailing_create"),
        {"subject": "Hello", "body": "Text", "audience": "unit_leads", "all_units": "True"},
    )
    for url in [
        reverse("mailing_list"),
        Mailing.objects.get().get_absolute_url(),
        reverse("mailing_create") + f"?duty={duty.pk}",
        reverse("mailing_create") + f"?unit={unit.pk}",
        reverse("unit_detail", args=[unit.pk]),
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


def test_billing_pages_render(client, dispatcher, unit_lead, unit):
    duty = create_duty(
        starts_in=timedelta(days=-2), pay_type=Duty.PayType.HOURLY, pay_rate=Decimal("12")
    )
    report = create_report(duty, unit, status=Report.Status.CONFIRMED)
    BankAccount.objects.create(unit=unit, account_holder="Holder", iban="DE89370400440532013000")
    client.force_login(dispatcher)
    for url in [
        reverse("billing_list"),
        reverse("billing_create", args=[unit.pk]),
        reverse("report_add", args=[duty.pk]),
    ]:
        assert client.get(url).status_code == 200, url
    billing = services.create_billing(unit, [(report, Decimal(4))], "", dispatcher)
    history.record(
        billing, history.Action.CREATED, dispatcher, history.scope_for_billing(billing.pk)
    )
    history.record(duty, history.Action.RELEASED, dispatcher, history.scope_for_duty(duty.pk))
    assert client.get(duty.get_absolute_url()).status_code == 200
    for user in (dispatcher, unit_lead):
        client.force_login(user)
        for url in [
            reverse("unit_pay", args=[unit.pk]),
            reverse("bank_account_edit", args=[unit.pk]),
            billing.get_absolute_url(),
            reverse("home"),
        ]:
            assert client.get(url).status_code == 200, (user, url)
