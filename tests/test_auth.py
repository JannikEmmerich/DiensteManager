import pytest
from allauth.account.adapter import get_adapter
from allauth.account.models import EmailAddress
from django.urls import reverse

from accounts.models import Role, User

from .conftest import PASSWORD, create_invitation


def test_home_requires_sign_in(client, db):
    response = client.get(reverse("home"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


def test_sign_in_with_email_and_password(client, member):
    response = client.post(
        reverse("account_login"), {"login": "MEMBER@example.org", "password": PASSWORD}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert client.get(reverse("home")).status_code == 200


def test_sign_in_with_wrong_password_fails(client, member):
    response = client.post(reverse("account_login"), {"login": member.email, "password": "wrong"})
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_public_sign_up_is_closed(client, db):
    response = client.get(reverse("account_signup"))
    assert "Access is by invitation only." in response.text
    client.post(
        reverse("account_signup"),
        {"email": "new@example.org", "password1": PASSWORD, "password2": PASSWORD},
    )
    assert not User.objects.filter(email="new@example.org").exists()


@pytest.mark.parametrize(
    "url",
    [
        reverse("account_login"),
        reverse("account_reset_password"),
        reverse("account_confirm_email", args=["invalid-key"]),
        reverse("account_reset_password_from_key", args=["x", "y"]),
        reverse("openid_connect_callback", kwargs={"provider_id": "hiorg"})
        + "?code=x&state=unknown",
    ],
)
def test_public_pages_without_sign_in(client, db, url):
    response = client.get(url)
    redirected_to_sign_in = response.status_code == 302 and response["Location"].startswith(
        reverse("account_login")
    )
    assert not redirected_to_sign_in


def test_invitation_link_without_sign_in(client, unit):
    _, token = create_invitation("new@example.org", Role.MEMBER, unit)
    assert client.get(reverse("accept_invitation", args=[token])).status_code == 200


def test_admin_uses_allauth_sign_in(client, db):
    response = client.get(reverse("admin:login"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


def test_admin_closed_for_regular_accounts(client, dispatcher):
    client.force_login(dispatcher)
    assert client.get(reverse("admin:index")).status_code in (302, 403)


def test_createsuperuser_stores_verified_address(client, admin):
    address = EmailAddress.objects.get(user=admin)
    assert address.verified and address.primary
    response = client.post(reverse("account_login"), {"login": admin.email, "password": PASSWORD})
    assert response["Location"] == reverse("home")


def test_admin_pages_load(client, admin, member):
    client.force_login(admin)
    for url in [
        reverse("admin:accounts_user_changelist"),
        reverse("admin:accounts_user_add"),
        reverse("admin:accounts_user_change", args=[member.pk]),
        reverse("admin:accounts_unit_changelist"),
        reverse("admin:accounts_invitation_changelist"),
    ]:
        assert client.get(url).status_code == 200, url


def test_health_check_without_sign_in_and_any_host(client, db):
    response = client.get("/healthz", HTTP_HOST="internal-name:8000")
    assert response.status_code == 200
    assert response.text == "ok"


def test_blocked_account_is_signed_out(client, member):
    client.force_login(member)
    member.is_active = False
    member.save()
    response = client.get(reverse("home"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


def test_behind_a_cloudflare_tunnel(client, settings, db, rf):
    """compose.tunnel.yaml: HTTPS ends at Cloudflare, which passes protocol and client IP."""
    settings.SECURE_SSL_REDIRECT = True
    settings.ALLAUTH_TRUSTED_CLIENT_IP_HEADER = "CF-Connecting-IP"
    headers = {"HTTP_X_FORWARDED_PROTO": "https", "HTTP_CF_CONNECTING_IP": "203.0.113.7"}
    assert client.get(reverse("account_login"), **headers).status_code == 200
    # Plain HTTP (no proxy in between) is sent to HTTPS.
    assert client.get(reverse("account_login")).status_code == 301
    # The sign-in rate limits count per visitor, not per tunnel.
    assert get_adapter().get_client_ip(rf.get("/", **headers)) == "203.0.113.7"
