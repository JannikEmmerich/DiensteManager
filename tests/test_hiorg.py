"""Sign-in with HiOrg-Server (OpenID Connect); the HiOrg server is simulated."""

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.urls import reverse

from accounts.adapters import ALLOWED_SIGNIN_CLAIMS
from accounts.invitations import SESSION_KEY
from accounts.models import Invitation, Role, User

from .conftest import PASSWORD, create_invitation, hiorg_userinfo


def _stored_claims(account):
    claims = set()
    for part in account.extra_data.values():
        claims |= set(part)
    return claims


def test_without_invitation_or_link_no_account(client, db, hiorg_login):
    response, _ = hiorg_login(client)
    assert "No account found" in response.text
    assert not User.objects.exists()
    assert not SocialAccount.objects.exists()
    assert "_auth_user_id" not in client.session


def test_invitation_creates_account_with_invited_address(client, unit_lead, unit, hiorg_login):
    invitation, token = create_invitation(
        "new@example.org", Role.MEMBER, unit, unit_lead, language="de"
    )
    client.get(reverse("accept_invitation", args=[token]))

    response, _ = hiorg_login(client, hiorg_userinfo(sub="sub-42", email="private@hiorg.example"))

    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    user = User.objects.get(email="new@example.org")
    assert (user.role, user.unit, user.language) == (Role.MEMBER, unit, "de")
    assert (user.first_name, user.last_name) == ("Alex", "Example")
    assert not user.has_usable_password()
    addresses = EmailAddress.objects.filter(user=user)
    assert [(a.email, a.verified, a.primary) for a in addresses] == [
        ("new@example.org", True, True)
    ]
    account = SocialAccount.objects.get(user=user)
    assert (account.provider, account.uid) == ("hiorg", "sub-42")
    invitation.refresh_from_db()
    assert invitation.status == Invitation.Status.ACCEPTED
    assert invitation.account == user
    assert int(client.session["_auth_user_id"]) == user.pk
    assert SESSION_KEY not in client.session


def test_revoked_invitation_in_session_does_not_help(client, unit, hiorg_login):
    invitation, token = create_invitation("new@example.org", Role.MEMBER, unit)
    client.get(reverse("accept_invitation", args=[token]))
    Invitation.objects.filter(pk=invitation.pk).update(revoked_at="2026-01-01T00:00:00Z")
    response, _ = hiorg_login(client)
    assert "No account found" in response.text
    assert not User.objects.filter(email="new@example.org").exists()


def test_only_allowed_hiorg_claims_are_stored(client, unit, hiorg_login):
    _, token = create_invitation("new@example.org", Role.MEMBER, unit)
    client.get(reverse("accept_invitation", args=[token]))
    hiorg_login(client, hiorg_userinfo(sub="sub-7"))
    account = SocialAccount.objects.get(uid="sub-7")
    assert _stored_claims(account) <= ALLOWED_SIGNIN_CLAIMS
    assert "birthdate" not in str(account.extra_data)

    # Later sign-ins also update the stored data in a minimised way only.
    client.logout()
    hiorg_login(client, hiorg_userinfo(sub="sub-7", nickname="Al", birthdate="1990-01-01"))
    account.refresh_from_db()
    assert _stored_claims(account) <= ALLOWED_SIGNIN_CLAIMS


def test_sign_in_with_linked_account(client, member, hiorg_login):
    SocialAccount.objects.create(user=member, provider="hiorg", uid="sub-1")
    response, server = hiorg_login(client, hiorg_userinfo(sub="sub-1"))
    assert response["Location"] == reverse("home")
    assert int(client.session["_auth_user_id"]) == member.pk
    assert User.objects.count() == 1

    # HiOrg only accepts client_secret_post: secret in the form body, no basic auth.
    token_call = next(c for c in server.calls if c["url"].endswith("/oauth/v1/token"))
    assert token_call["data"]["client_secret"] == "test-client-secret"
    assert token_call.get("auth") is None


def test_no_automatic_linking_via_matching_email(client, member, hiorg_login):
    hiorg_login(client, hiorg_userinfo(sub="sub-other", email=member.email, email_verified=True))
    assert "_auth_user_id" not in client.session
    assert not SocialAccount.objects.exists()


def _sign_in_with_password_and_confirm(client, user):
    """Sign-in via the form plus re-entering the password (needed for sensitive actions)."""
    client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})
    client.post(reverse("account_reauthenticate"), {"password": PASSWORD})


def test_link_hiorg_account_while_signed_in(client, member, hiorg_login):
    _sign_in_with_password_and_confirm(client, member)
    response, _ = hiorg_login(client, hiorg_userinfo(sub="sub-new"), process="connect")
    assert response.status_code == 302
    assert SocialAccount.objects.get(uid="sub-new").user == member
    # The (unverified) HiOrg address is not added to the account.
    assert list(EmailAddress.objects.filter(user=member).values_list("email", flat=True)) == [
        member.email
    ]


def test_linking_requires_password_confirmation(client, member, hiorg_login):
    client.post(reverse("account_login"), {"login": member.email, "password": PASSWORD})
    response, _ = hiorg_login(client, hiorg_userinfo(sub="sub-new"), process="connect")
    assert response["Location"].startswith(reverse("account_reauthenticate"))
    assert not SocialAccount.objects.exists()


def test_hiorg_account_of_someone_else_cannot_be_linked(client, member, unit_lead, hiorg_login):
    SocialAccount.objects.create(user=unit_lead, provider="hiorg", uid="sub-lead")
    _sign_in_with_password_and_confirm(client, member)
    hiorg_login(client, hiorg_userinfo(sub="sub-lead"), process="connect")
    assert SocialAccount.objects.get(uid="sub-lead").user == unit_lead
    assert not SocialAccount.objects.filter(user=member).exists()


def _hiorg_only_account(client, unit, hiorg_login):
    _, token = create_invitation("new@example.org", Role.MEMBER, unit)
    client.get(reverse("accept_invitation", args=[token]))
    hiorg_login(client, hiorg_userinfo(sub="sub-hiorg-only"))
    return User.objects.get(email="new@example.org")


def test_hiorg_account_can_set_password(client, unit, hiorg_login):
    user = _hiorg_only_account(client, unit, hiorg_login)
    assert client.get(reverse("account_set_password")).status_code == 200
    client.post(reverse("account_set_password"), {"password1": PASSWORD, "password2": PASSWORD})
    user.refresh_from_db()
    assert user.check_password(PASSWORD)

    # Afterwards, signing in with email address and password works as well.
    client.logout()
    client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})
    assert int(client.session["_auth_user_id"]) == user.pk


def test_unlinking_without_password_is_refused(client, unit, hiorg_login):
    user = _hiorg_only_account(client, unit, hiorg_login)
    account = SocialAccount.objects.get(user=user)
    client.post(reverse("socialaccount_connections"), {"account": account.pk})
    assert SocialAccount.objects.filter(pk=account.pk).exists()


def test_profile_page_shows_hiorg_status(client, unit, hiorg_login):
    _hiorg_only_account(client, unit, hiorg_login)
    response = client.get(reverse("profile"))
    assert response.status_code == 200
    assert "Linked with:" in response.text
    assert "No password set yet" in response.text
