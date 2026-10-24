import re
from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.invitations import SESSION_KEY
from accounts.models import Invitation, Role, User

from .conftest import PASSWORD, create_account, create_invitation


def _invite(client, **data):
    data.setdefault("language", "en")
    return client.post(reverse("invitation_create"), data)


def _token_from_mail(message):
    return re.search(r"/invitation/([^/\s]+)/", message.body).group(1)


# --- Who may invite? ----------------------------------------------------------------------


def test_member_may_not_invite(client, member):
    client.force_login(member)
    assert client.get(reverse("invitation_create")).status_code == 403
    assert client.get(reverse("invitation_list")).status_code == 403


def test_dispatcher_invites_member_and_mail_is_sent(client, dispatcher, unit):
    client.force_login(dispatcher)
    response = _invite(client, email="New@Example.org", role=Role.MEMBER, unit=unit.pk)
    assert response.status_code == 302
    invitation = Invitation.objects.get()
    assert invitation.email == "new@example.org"
    assert invitation.unit == unit
    assert invitation.invited_by == dispatcher
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["new@example.org"]
    token = _token_from_mail(mail.outbox[0])
    assert token not in invitation.token_hash  # only the hash is stored
    assert Invitation.objects.pending().with_token(token).get() == invitation


def test_unit_lead_always_invites_into_own_unit(client, unit_lead, unit, other_unit):
    client.force_login(unit_lead)
    _invite(client, email="new@example.org", role=Role.MEMBER, unit=other_unit.pk)
    assert Invitation.objects.get().unit == unit


def test_unit_lead_ignores_unit_from_query_string(client, unit_lead, unit, other_unit):
    client.force_login(unit_lead)
    response = client.get(reverse("invitation_create") + f"?unit={other_unit.pk}")
    assert response.context["form"]["unit"].value() == unit.pk


def test_unit_lead_may_invite_another_unit_lead(client, unit_lead, unit):
    client.force_login(unit_lead)
    _invite(client, email="new@example.org", role=Role.UNIT_LEAD)
    assert Invitation.objects.get().role == Role.UNIT_LEAD


@pytest.mark.parametrize("inviter", ["unit_lead", "dispatcher"])
def test_only_admins_invite_dispatchers(client, request, inviter, unit):
    client.force_login(request.getfixturevalue(inviter))
    response = _invite(client, email="new@example.org", role=Role.DISPATCHER)
    assert response.status_code == 200
    assert not Invitation.objects.exists()


def test_admin_invites_dispatcher_without_unit(client, admin):
    client.force_login(admin)
    _invite(client, email="new@example.org", role=Role.DISPATCHER, unit="")
    assert Invitation.objects.get().role == Role.DISPATCHER


def test_external_role_requires_unit(client, dispatcher):
    client.force_login(dispatcher)
    response = _invite(client, email="new@example.org", role=Role.MEMBER, unit="")
    assert "Please select a unit." in response.text
    assert not Invitation.objects.exists()


def test_no_invitation_for_existing_account_or_twice(client, dispatcher, member, unit):
    client.force_login(dispatcher)
    response = _invite(client, email=member.email.upper(), role=Role.MEMBER, unit=unit.pk)
    assert "An account with this email address already exists." in response.text
    _invite(client, email="new@example.org", role=Role.MEMBER, unit=unit.pk)
    response = _invite(client, email="new@example.org", role=Role.MEMBER, unit=unit.pk)
    assert "There is already a pending invitation for this email address." in response.text
    assert Invitation.objects.count() == 1


# --- Resend / revoke ----------------------------------------------------------------------


def test_unit_lead_cannot_manage_other_units_invitations(client, unit_lead, other_unit):
    invitation, _ = create_invitation("x@example.org", Role.MEMBER, other_unit)
    client.force_login(unit_lead)
    for name in ("invitation_revoke", "invitation_resend"):
        assert client.post(reverse(name, args=[invitation.pk])).status_code == 404
    invitation.refresh_from_db()
    assert invitation.status == Invitation.Status.PENDING


def test_dispatcher_cannot_manage_dispatcher_invitations(client, dispatcher, admin):
    invitation, _ = create_invitation("x@example.org", Role.DISPATCHER, invited_by=admin)
    client.force_login(dispatcher)
    assert client.post(reverse("invitation_revoke", args=[invitation.pk])).status_code == 403


def test_revoke_requires_post(client, unit_lead, unit):
    invitation, _ = create_invitation("x@example.org", Role.MEMBER, unit)
    client.force_login(unit_lead)
    assert client.get(reverse("invitation_revoke", args=[invitation.pk])).status_code == 405


def test_unit_lead_revokes_own_invitation(client, unit_lead, unit):
    invitation, token = create_invitation("x@example.org", Role.MEMBER, unit)
    client.force_login(unit_lead)
    client.post(reverse("invitation_revoke", args=[invitation.pk]))
    invitation.refresh_from_db()
    assert invitation.status == Invitation.Status.REVOKED
    client.logout()
    assert client.get(reverse("accept_invitation", args=[token])).status_code == 404


def test_resend_invalidates_old_link(client, unit_lead, unit):
    invitation, old_token = create_invitation("x@example.org", Role.MEMBER, unit)
    client.force_login(unit_lead)
    client.post(reverse("invitation_resend", args=[invitation.pk]))
    new_token = _token_from_mail(mail.outbox[-1])
    client.logout()
    assert client.get(reverse("accept_invitation", args=[old_token])).status_code == 404
    assert client.get(reverse("accept_invitation", args=[new_token])).status_code == 200


def test_htmx_returns_only_the_row(client, unit_lead, unit):
    invitation, _ = create_invitation("x@example.org", Role.MEMBER, unit)
    client.force_login(unit_lead)
    response = client.post(
        reverse("invitation_revoke", args=[invitation.pk]), HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert response.text.lstrip().startswith("<tr>")
    assert 'hx-swap-oob="true"' in response.text
    assert "Revoked" in response.text  # status badge of the updated row


# --- Accepting an invitation --------------------------------------------------------------


def _accept(client, token, **data):
    values = {
        "first_name": "Erin",
        "last_name": "Example",
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    values.update(data)
    return client.post(reverse("accept_invitation", args=[token]), values)


def test_accept_invitation_with_password(client, unit_lead, unit):
    invitation, token = create_invitation(
        "new@example.org", Role.MEMBER, unit, unit_lead, language="de"
    )
    assert client.get(reverse("accept_invitation", args=[token])).status_code == 200
    response = _accept(client, token)
    assert response.status_code == 302
    assert response["Location"] == reverse("home")

    user = User.objects.get(email="new@example.org")
    assert (user.role, user.unit, user.first_name) == (Role.MEMBER, unit, "Erin")
    assert user.language == "de"  # taken over from the invitation
    assert user.check_password(PASSWORD)
    assert EmailAddress.objects.filter(
        user=user, email=user.email, verified=True, primary=True
    ).exists()
    invitation.refresh_from_db()
    assert invitation.status == Invitation.Status.ACCEPTED
    assert invitation.account == user
    assert int(client.session["_auth_user_id"]) == user.pk
    assert SESSION_KEY not in client.session


def test_invitation_can_only_be_used_once(client, unit):
    _, token = create_invitation("new@example.org", Role.MEMBER, unit)
    _accept(client, token)
    client.logout()
    assert client.get(reverse("accept_invitation", args=[token])).status_code == 404
    assert _accept(client, token).status_code == 404
    assert User.objects.filter(email="new@example.org").count() == 1


def test_expired_invitation(client, unit):
    invitation, token = create_invitation("new@example.org", Role.MEMBER, unit)
    Invitation.objects.filter(pk=invitation.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    assert client.get(reverse("accept_invitation", args=[token])).status_code == 404


def test_password_rules_apply(client, unit):
    _, token = create_invitation("new@example.org", Role.MEMBER, unit)
    response = _accept(client, token, password1="short", password2="short")
    assert response.status_code == 200
    response = _accept(client, token, password2="another-Password-99")
    assert response.status_code == 200
    assert not User.objects.filter(email="new@example.org").exists()


def test_signed_in_user_cannot_accept_invitation(client, member, unit):
    _, token = create_invitation("new@example.org", Role.MEMBER, unit)
    client.force_login(member)
    response = client.get(reverse("accept_invitation", args=[token]))
    assert "You are already signed in" in response.text
    _accept(client, token)
    assert not User.objects.filter(email="new@example.org").exists()


def test_address_taken_meanwhile(client, unit):
    _, token = create_invitation("new@example.org", Role.MEMBER, unit)
    client.get(reverse("accept_invitation", args=[token]))
    User.objects.create_user(email="new@example.org", role=Role.MEMBER, unit=unit)
    assert _accept(client, token).status_code == 404


# --- Blocking accounts ------------------------------------------------------------------


def _set_active(client, account, active):
    return client.post(
        reverse("set_account_active", args=[account.pk]), {"active": "1" if active else "0"}
    )


def test_unit_lead_blocks_member_of_own_unit(client, unit_lead, member):
    client.force_login(unit_lead)
    assert _set_active(client, member, active=False).status_code == 302
    member.refresh_from_db()
    assert not member.is_active
    _set_active(client, member, active=True)
    member.refresh_from_db()
    assert member.is_active


def test_unit_lead_blocks_neither_strangers_nor_itself(client, unit_lead, other_unit_lead):
    client.force_login(unit_lead)
    assert _set_active(client, other_unit_lead, active=False).status_code == 403
    assert _set_active(client, unit_lead, active=False).status_code == 403


def test_only_admins_block_dispatchers(client, dispatcher, admin):
    colleague = create_account("colleague@example.org", Role.DISPATCHER)
    client.force_login(dispatcher)
    assert _set_active(client, colleague, active=False).status_code == 403
    client.force_login(admin)
    assert _set_active(client, colleague, active=False).status_code == 302


# --- Units ---------------------------------------------------------------------------


def test_unit_visibility(client, unit_lead, member, unit, other_unit):
    client.force_login(unit_lead)
    assert client.get(reverse("unit_detail", args=[unit.pk])).status_code == 200
    assert client.get(reverse("unit_detail", args=[other_unit.pk])).status_code == 403
    assert client.get(reverse("unit_list")).status_code == 403
    client.force_login(member)
    assert client.get(reverse("unit_detail", args=[unit.pk])).status_code == 403


def test_dispatcher_creates_unit(client, dispatcher):
    client.force_login(dispatcher)
    response = client.post(reverse("unit_create"), {"name": "Unit West"})
    assert response.status_code == 302
    assert client.get(reverse("unit_list")).status_code == 200
