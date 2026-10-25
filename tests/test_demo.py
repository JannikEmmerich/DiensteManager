"""Demo mode: emails go to the inbox instead of out; only admins change email addresses
and passwords, use two-factor authentication and create accounts."""

from datetime import timedelta

import pytest
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from demo.models import DemoEmail
from lifecycle import retention
from outbox import queue, worker

from .conftest import PASSWORD, create_invitation


@pytest.fixture
def demo(settings):
    settings.DEMO_MODE = True
    settings.MAILERS = {"default": {"BACKEND": "demo.backends.DemoEmailBackend"}}


def _send(to, bcc=(), subject="Hello", body="See https://dienste.example.org/duties/1/"):
    message = mail.EmailMessage(subject, body, to=to, bcc=bcc)
    message.attach("statement.pdf", b"%PDF-1.4", "application/pdf")
    message.send()


# --- Inbox -------------------------------------------------------------------------------------


def test_emails_land_in_the_inbox(client, demo, member, dispatcher, admin):
    _send(["Hanna Member <Member@Example.org>"], subject="For the member")
    _send(["someone@example.org"], bcc=[dispatcher.email], subject="Blind copy")
    assert mail.outbox == []  # nothing sent
    client.force_login(member)
    page = client.get(reverse("demo_inbox")).text
    assert "For the member" in page and "Blind copy" not in page
    assert "statement.pdf" in page
    email = DemoEmail.objects.get(subject="For the member")
    page = client.get(reverse("demo_inbox_email", args=[email.pk])).text
    assert '<a href="https://dienste.example.org/duties/1/"' in page
    attachment = email.attachments.get()
    response = client.get(reverse("demo_inbox_attachment", args=[attachment.pk]))
    assert response.content == b"%PDF-1.4"
    assert response["Content-Disposition"] == 'attachment; filename="statement.pdf"'
    # Not the emails of others.
    other = DemoEmail.objects.get(subject="Blind copy")
    assert client.get(reverse("demo_inbox_email", args=[other.pk])).status_code == 404
    client.force_login(dispatcher)
    page = client.get(reverse("demo_inbox")).text
    assert "Blind copy" in page and "For the member" not in page
    assert client.get(reverse("demo_inbox_attachment", args=[attachment.pk])).status_code == 404


def test_admins_see_all_emails(client, demo, admin, member):
    _send([member.email], subject="For the member")
    client.force_login(admin)
    assert "For the member" not in client.get(reverse("demo_inbox")).text
    page = client.get(reverse("demo_inbox") + "?all=1").text
    assert "For the member" in page and member.email in page
    email = DemoEmail.objects.get()
    assert client.get(reverse("demo_inbox_email", args=[email.pk])).status_code == 200


def test_queued_emails_land_in_the_inbox_too(client, demo, settings, member):
    settings.EMAIL_QUEUE = True
    queue.send([queue.Email(mail.EmailMessage("Queued", "Body", to=[member.email]), member)])
    assert not DemoEmail.objects.exists()
    worker.send_due()
    assert DemoEmail.objects.get().subject == "Queued"


def test_inbox_only_in_demo_mode(client, member):
    client.force_login(member)
    assert client.get(reverse("demo_inbox")).status_code == 404
    assert "Inbox" not in client.get(reverse("home")).text


def test_inbox_and_banner_in_demo_mode(client, demo, member):
    assert "Demo: emails are not sent" in client.get(reverse("account_login")).text
    client.force_login(member)
    assert reverse("demo_inbox") in client.get(reverse("home")).text


def test_old_demo_emails_are_deleted(demo, settings, member):
    _send([member.email])
    DemoEmail.objects.update(created_at=timezone.now() - timedelta(days=31))
    _send([member.email], subject="Recent")
    assert retention.apply()["demo_emails_deleted"] == 1
    assert DemoEmail.objects.get().subject == "Recent"


# --- Sign-in -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_name",
    ["account_email", "account_change_password", "account_set_password", "mfa_index"],
)
def test_only_admins_change_their_sign_in(client, demo, member, admin, url_name):
    client.force_login(member)
    response = client.get(reverse(url_name), follow=True)
    assert response.redirect_chain == [(reverse("profile"), 302)]
    assert "In demo mode, only an admin can do this." in response.text
    client.force_login(admin)
    response = client.get(reverse(url_name))
    assert response.get("Location") != reverse("profile")


def test_profile_without_sign_in_settings(client, demo, member):
    client.force_login(member)
    page = client.get(reverse("profile")).text
    assert "only an admin can change email addresses and passwords" in page
    assert reverse("account_change_password") not in page
    assert reverse("mfa_index") not in page


def test_no_password_reset(client, db, demo):
    assert reverse("account_reset_password") not in client.get(reverse("account_login")).text
    response = client.get(reverse("account_reset_password"))
    assert response["Location"] == reverse("account_login")


def test_password_reset_outside_demo_mode(client, db):
    assert reverse("account_reset_password") in client.get(reverse("account_login")).text


def _sign_in(client, user):
    return client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})


def test_two_factor_authentication_only_for_admins(client, demo, settings, member, dispatcher):
    TOTP.activate(member, generate_totp_secret())
    assert _sign_in(client, member)["Location"] == reverse("home")
    # Dispatchers are not forced to set it up.
    settings.MFA_REQUIRED_ROLES = ["dispatcher"]
    client.force_login(dispatcher)
    assert client.get(reverse("home")).status_code == 200


def test_admins_keep_two_factor_authentication(client, demo, settings, admin):
    settings.MFA_REQUIRED_ROLES = ["dispatcher"]
    client.force_login(admin)
    assert client.get(reverse("home"))["Location"] == reverse("mfa_activate_totp")
    client.logout()
    TOTP.activate(admin, generate_totp_secret())
    assert _sign_in(client, admin)["Location"] == reverse("mfa_authenticate")


# --- New accounts ------------------------------------------------------------------------------


@pytest.mark.parametrize("account", ["dispatcher", "unit_lead"])
def test_only_admins_invite(client, demo, request, account):
    client.force_login(request.getfixturevalue(account))
    assert client.get(reverse("invitation_create")).status_code == 403
    assert reverse("invitation_list") not in client.get(reverse("home")).text


def test_admin_invites_and_the_link_is_in_the_inbox(client, demo, admin, unit):
    client.force_login(admin)
    client.post(
        reverse("invitation_create"),
        {"email": "new@example.org", "role": Role.MEMBER, "unit": unit.pk, "language": "en"},
    )
    email = DemoEmail.objects.get()
    assert email.to == ["new@example.org"]
    assert "/invitation/" in email.body


def test_only_invitations_of_admins_create_accounts(client, demo, admin, unit_lead, unit):
    _invitation, token = create_invitation("lead-invited@example.org", Role.MEMBER, unit, unit_lead)
    assert client.get(reverse("accept_invitation", args=[token])).status_code == 404
    _invitation, token = create_invitation("admin-invited@example.org", Role.MEMBER, unit, admin)
    response = client.post(
        reverse("accept_invitation", args=[token]),
        {
            "first_name": "Erin",
            "last_name": "Example",
            "password1": PASSWORD,
            "password2": PASSWORD,
        },
    )
    assert response["Location"] == reverse("home")
    assert User.objects.filter(email="admin-invited@example.org").exists()
