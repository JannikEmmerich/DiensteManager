from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.providers.base import AuthProcess
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.utils import translation
from django.utils.translation import gettext as _

from . import invitations
from .models import User

# Only these claims from the HiOrg sign-in are stored (data minimisation).
# HiOrg additionally returns e.g. date of birth, gender, postal address and phone number.
ALLOWED_SIGNIN_CLAIMS = frozenset(
    {"sub", "name", "given_name", "family_name", "preferred_username", "email"}
)


def _minimise(extra_data):
    def keep_allowed(data):
        return {k: v for k, v in data.items() if k in ALLOWED_SIGNIN_CLAIMS}

    if not isinstance(extra_data, dict):
        return {}
    if "userinfo" in extra_data or "id_token" in extra_data:
        return {
            part: keep_allowed(extra_data[part])
            for part in ("userinfo", "id_token")
            if isinstance(extra_data.get(part), dict)
        }
    return keep_allowed(extra_data)


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        # No public sign-up form: accounts are only created through invitations.
        return False

    def send_mail(self, template_prefix, email, context):
        # Emails to an existing account (password reset, notifications, ...) use the
        # language stored on that account instead of the language of the request.
        user = context.get("user")
        if not isinstance(user, User):
            user = User.objects.filter(email__iexact=email).first()
        if user is not None and user.language:
            with translation.override(user.language):
                return super().send_mail(template_prefix, email, context)
        return super().send_mail(template_prefix, email, context)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Sign-in with HiOrg-Server.

    - Linked HiOrg account: regular sign-in.
    - Unknown HiOrg account: an account is only created if a pending invitation is
      remembered in the session, and it gets the invited (not the HiOrg) email address.
    - Linking with an existing account is only possible while signed in ("My account").
    """

    def populate_user(self, request, sociallogin, data):
        # Runs before anything is saved, including on every later sign-in (allauth then
        # updates the stored data of the linked account).
        sociallogin.account.extra_data = _minimise(sociallogin.account.extra_data)
        return super().populate_user(request, sociallogin, data)

    def pre_social_login(self, request, sociallogin):
        super().pre_social_login(request, sociallogin)
        if sociallogin.is_existing or sociallogin.state.get("process") != AuthProcess.LOGIN:
            return
        invitation = invitations.pending_invitation_from_session(request)
        if invitation is not None:
            # The HiOrg address is unverified; the invited address is authoritative.
            sociallogin.user.email = invitation.email
            sociallogin.email_addresses = [
                EmailAddress(email=invitation.email, primary=True, verified=True)
            ]

    def is_open_for_signup(self, request, sociallogin):
        return invitations.pending_invitation_from_session(request) is not None

    def save_user(self, request, sociallogin, form=None):
        invitation = invitations.pending_invitation_from_session(request)
        try:
            if invitation is None:
                raise invitations.InvitationInvalid
            with transaction.atomic():
                invitation = invitations.lock_and_validate(invitation.pk)
                user = sociallogin.user
                invitations.apply_to_user(invitation, user)
                sociallogin.email_addresses = [
                    EmailAddress(email=invitation.email, primary=True, verified=True)
                ]
                super().save_user(request, sociallogin, form)
                invitations.mark_accepted(invitation, user)
        except invitations.InvitationInvalid:
            messages.error(request, _("The invitation is no longer valid."))
            raise ImmediateHttpResponse(redirect("account_login")) from None
        invitations.forget_from_session(request)
        return user
