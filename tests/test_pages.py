"""Smoke test: every page renders without template errors."""

import re
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


# --- Content Security Policy ----------------------------------------------------------------

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\ssrc=)(?![^>]*type=\"application/json\")[^>]*>")
EVENT_HANDLER = re.compile(r"<[^>]+\son[a-z]+=", re.IGNORECASE)
STYLE_ATTRIBUTE = re.compile(r"<[^>]+\sstyle=", re.IGNORECASE)
STYLE_WITHOUT_NONCE = re.compile(r"<style(?![^>]*\snonce=)[^>]*>")


def _assert_csp_compatible(response, url):
    assert "script-src 'self'" in response["Content-Security-Policy"], url
    html = response.text
    for pattern in (INLINE_SCRIPT, EVENT_HANDLER, STYLE_ATTRIBUTE, STYLE_WITHOUT_NONCE):
        match = pattern.search(html)
        assert match is None, (url, match and match.group(0))


def test_pages_work_with_the_content_security_policy(client, admin, unit_lead, unit):
    duty = create_duty(
        starts_in=timedelta(days=-2), pay_type=Duty.PayType.HOURLY, pay_rate=Decimal("12")
    )
    report = create_report(duty, unit, status=Report.Status.CONFIRMED)
    billing = services.create_billing(unit, [(report, Decimal(4))], "", admin)
    open_duty = create_duty()
    create_report(open_duty, unit, name="Open", reported_by=unit_lead)
    confirmed = create_report(open_duty, unit, name="Other", status=Report.Status.CONFIRMED)
    pages = {
        admin: [
            reverse("home"),
            reverse("duty_create"),
            reverse("duty_update", args=[open_duty.pk]),
            open_duty.get_absolute_url(),
            create_duty(status=Duty.Status.DRAFT).get_absolute_url(),
            reverse("billing_create", args=[unit.pk]),
            billing.get_absolute_url(),
            reverse("unit_pay", args=[unit.pk]),
            reverse("mailing_create"),
            reverse("invitation_list"),
            reverse("mfa_index"),
            reverse("account_reauthenticate"),
            reverse("admin:index"),
            reverse("admin:accounts_user_changelist"),
            reverse("admin:accounts_user_change", args=[unit_lead.pk]),
        ],
        unit_lead: [
            open_duty.get_absolute_url(),
            reverse("report_create", args=[open_duty.pk]),
            reverse("report_request_withdrawal", args=[confirmed.pk]),
        ],
        None: [reverse("account_login"), reverse("account_reset_password")],
    }
    for user, urls in pages.items():
        client.logout()
        if user:
            client.force_login(user)
        for url in urls:
            response = client.get(url)
            assert response.status_code == 200, url
            _assert_csp_compatible(response, url)


def test_confirmation_questions_are_escaped(client, unit_lead, unit):
    duty = create_duty()
    create_report(duty, unit, name='Ann "A" <b>', reported_by=unit_lead)
    client.force_login(unit_lead)
    page = client.get(duty.get_absolute_url()).text
    assert 'data-confirm="Withdraw the report for Ann &quot;A&quot; &lt;b&gt;?"' in page
