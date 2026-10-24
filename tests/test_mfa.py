"""Two-factor authentication (TOTP) works with the customised templates."""

import time

from allauth.mfa.models import Authenticator
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
