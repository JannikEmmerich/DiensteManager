"""Sending and redeeming invitations.

An account is only ever created from a pending invitation: either with a password
(``create_account_with_password``) or through the sign-in with HiOrg-Server (see
``adapters.SocialAccountAdapter``). For the latter, the invitation is remembered in the
session when the invitation page is opened.
"""

from allauth.account.models import EmailAddress
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation

from .models import Invitation, User

SESSION_KEY = "pending_invitation_token_hash"


class InvitationInvalid(Exception):
    """The invitation is no longer pending, or its address has been taken meanwhile."""


def send_invitation(request, invitation):
    """Creates a new link (older links stop working), saves and sends the invitation."""
    token = invitation.new_token()
    invitation.save()
    context = {
        "invitation": invitation,
        "link": request.build_absolute_uri(reverse("accept_invitation", args=[token])),
    }
    with translation.override(invitation.language or translation.get_language()):
        subject = render_to_string("accounts/email/invitation_subject.txt", context, request)
        body = render_to_string("accounts/email/invitation_message.txt", context, request)
    send_mail(
        subject=" ".join(subject.split()),
        message=body,
        from_email=None,
        recipient_list=[invitation.email],
    )


def pending_invitation(token):
    return Invitation.objects.pending().with_token(token).select_related("unit").first()


def remember_in_session(request, invitation):
    request.session[SESSION_KEY] = invitation.token_hash


def forget_from_session(request):
    request.session.pop(SESSION_KEY, None)


def pending_invitation_from_session(request):
    token_hash = request.session.get(SESSION_KEY)
    if not token_hash:
        return None
    return Invitation.objects.pending().filter(token_hash=token_hash).first()


def lock_and_validate(invitation_pk):
    """Locks the invitation for the current transaction and validates it again.

    Prevents an invitation from being redeemed twice (e.g. from two browser tabs).
    """
    # No select_related here: PostgreSQL does not allow FOR UPDATE on the nullable side
    # of an outer join (the unit is empty for internal invitations).
    invitation = Invitation.objects.select_for_update().get(pk=invitation_pk)
    if not invitation.is_pending or User.objects.filter(email__iexact=invitation.email).exists():
        raise InvitationInvalid
    return invitation


def apply_to_user(invitation, user):
    """Transfers address, role, unit and language of the invitation to the new account."""
    user.email = invitation.email
    user.role = invitation.role
    user.unit = invitation.unit
    user.language = invitation.language


def mark_accepted(invitation, user):
    invitation.accepted_at = timezone.now()
    invitation.account = user
    invitation.save(update_fields=["accepted_at", "account"])


def create_account_with_password(invitation_pk, *, first_name, last_name, password):
    with transaction.atomic():
        invitation = lock_and_validate(invitation_pk)
        user = User(first_name=first_name, last_name=last_name)
        apply_to_user(invitation, user)
        user.set_password(password)
        user.save()
        # The address counts as verified: the link was sent to exactly this address.
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
        mark_accepted(invitation, user)
    return user
