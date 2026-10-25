"""Two-factor authentication (TOTP) works with the customised templates, and is
mandatory for the roles in MFA_REQUIRED_ROLES."""

import time

import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.forms import DeactivateTOTPForm
from allauth.mfa.totp.internal.auth import (
    TOTP,
    format_hotp_value,
    generate_totp_secret,
    hotp_value,
)
from django.urls import reverse

from .conftest import PASSWORD


def _code(secret):
    return format_hotp_value(hotp_value(secret, int(time.time()) // 30))


def test_sign_in_with_two_factor_code(client, member):
    secret = generate_totp_secret()
    TOTP.activate(member, secret)
    response = client.post(reverse("account_login"), {"login": member.email, "password": PASSWORD})
    assert response.status_code == 302
    assert response["Location"] == reverse("mfa_authenticate")
    page = client.get(reverse("mfa_authenticate"))
    assert page.status_code == 200
    assert 'name="code"' in page.text
    response = client.post(reverse("mfa_authenticate"), {"code": _code(secret)})
    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert int(client.session["_auth_user_id"]) == member.pk


def test_set_up_totp_and_view_recovery_codes(client, member):
    client.post(reverse("account_login"), {"login": member.email, "password": PASSWORD})
    client.post(reverse("account_reauthenticate"), {"password": PASSWORD})
    page = client.get(reverse("mfa_activate_totp"))
    assert page.status_code == 200
    assert "<img" in page.text  # QR code
    assert 'name="code"' in page.text
    secret = client.session.get("mfa.totp.secret")
    response = client.post(reverse("mfa_activate_totp"), {"code": _code(secret)})
    assert response.status_code == 302
    assert Authenticator.objects.filter(user=member, type="totp").exists()
    page = client.get(reverse("mfa_view_recovery_codes"))
    assert page.status_code == 200
    assert "<textarea" in page.text
    assert client.get(reverse("mfa_index")).status_code == 200


# --- Mandatory for some roles ------------------------------------------------------------------


@pytest.fixture
def mfa_for_dispatchers(settings):
    settings.MFA_REQUIRED_ROLES = ["dispatcher"]


def _enable_totp(user):
    return Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={})


@pytest.mark.parametrize("account", ["dispatcher", "admin"])
def test_dispatchers_must_set_up_totp(client, request, mfa_for_dispatchers, account):
    client.force_login(request.getfixturevalue(account))
    for url in [reverse("home"), reverse("duty_list"), reverse("admin:index")]:
        assert client.get(url)["Location"] == reverse("mfa_activate_totp"), url
    page = client.get(reverse("home"), follow=True).text
    assert "Your account must be protected with two-factor authentication." in page


def test_setup_sign_out_and_language_stay_open(client, mfa_for_dispatchers, dispatcher):
    client.force_login(dispatcher)
    for name in ["mfa_index", "account_reauthenticate", "account_logout"]:
        assert client.get(reverse(name)).status_code == 200, name
    assert client.post(reverse("set_language"), {"language": "de"})["Location"] == "/"


def test_mandatory_setup(client, mfa_for_dispatchers, dispatcher):
    client.post(reverse("account_login"), {"login": dispatcher.email, "password": PASSWORD})
    response = client.get(reverse("home"), follow=True)
    # Activating needs the password again.
    assert response.redirect_chain[-1][0].startswith(reverse("account_reauthenticate"))
    response = client.post(response.redirect_chain[-1][0], {"password": PASSWORD}, follow=True)
    assert response.request["PATH_INFO"] == reverse("mfa_activate_totp")
    secret = client.session.get("mfa.totp.secret")
    client.post(reverse("mfa_activate_totp"), {"code": _code(secret)})
    assert Authenticator.objects.filter(user=dispatcher, type="totp").exists()
    assert client.get(reverse("home")).status_code == 200


def test_with_totp_everything_is_open(client, mfa_for_dispatchers, dispatcher):
    _enable_totp(dispatcher)
    client.force_login(dispatcher)
    assert client.get(reverse("home")).status_code == 200


def test_other_roles_only_if_configured(client, settings, unit_lead, member):
    settings.MFA_REQUIRED_ROLES = ["dispatcher"]
    client.force_login(unit_lead)
    assert client.get(reverse("home")).status_code == 200
    settings.MFA_REQUIRED_ROLES = ["dispatcher", "unit_lead"]
    assert client.get(reverse("home"))["Location"] == reverse("mfa_activate_totp")
    client.force_login(member)
    assert client.get(reverse("home")).status_code == 200


def test_nobody_is_forced_without_roles(client, settings, dispatcher):
    settings.MFA_REQUIRED_ROLES = []
    client.force_login(dispatcher)
    assert client.get(reverse("home")).status_code == 200


def test_mandatory_totp_cannot_be_switched_off(mfa_for_dispatchers, dispatcher, member):
    form = DeactivateTOTPForm(data={}, authenticator=_enable_totp(dispatcher))
    assert not form.is_valid()
    assert form.non_field_errors() == ["You cannot deactivate two-factor authentication."]
    assert DeactivateTOTPForm(data={}, authenticator=_enable_totp(member)).is_valid()
