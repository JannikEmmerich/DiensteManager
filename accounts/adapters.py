from contextlib import suppress

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.mfa.adapter import DefaultMFAAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.providers.base import AuthProcess
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.utils import translation
from django.utils.translation import gettext as _

from . import invitations, permissions
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


# Where Cloudflare locates the visitor's IP address: city, region and country code.
LOCATION_HEADERS = ("CF-IPCity", "CF-Region", "CF-IPCountry")
# Country codes that are no country: unknown, Tor.
NO_COUNTRY = {"XX", "T1"}


def client_location(request):
    """E.g. "Karlsruhe, Baden-Wurttemberg, DE"; empty unless the app trusts Cloudflare's
    headers (``ALLAUTH_TRUSTED_CLIENT_IP_HEADER`` = CF-Connecting-IP), since anyone could
    send them otherwise."""
    header = settings.ALLAUTH_TRUSTED_CLIENT_IP_HEADER or ""
    if request is None or header.lower() != "cf-connecting-ip":
        return ""
    parts = []
    for name in LOCATION_HEADERS:
        value = request.headers.get(name, "")
        with suppress(UnicodeError):
            # Header values arrive decoded as Latin-1; Cloudflare sends UTF-8.
            value = value.encode("latin-1").decode()
        value = value.strip()
        if value and value not in NO_COUNTRY and value not in parts:
            parts.append(value)
    return ", ".join(parts)


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

    def send_notification_mail(self, template_prefix, user, context=None, email=None):
        # Security emails (e.g. password changed) say where the change came from: the IP
        # address and, behind Cloudflare, its location (not the browser, see the template).
        context = {"location": client_location(self.request), **(context or {})}
        return super().send_notification_mail(template_prefix, user, context, email)


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
        process = sociallogin.state.get("process")
        if process == AuthProcess.CONNECT and permissions.demo_restricted(request.user):
            # Whatever the way into linking: in demo mode, only admins link HiOrg.
            messages.error(request, _("In demo mode, only an admin can do this."))
            raise ImmediateHttpResponse(redirect("profile"))
        if sociallogin.is_existing or process != AuthProcess.LOGIN:
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


class MFAAdapter(DefaultMFAAdapter):
    def can_delete_authenticator(self, authenticator):
        # Accounts that must use two-factor authentication cannot switch it off.
        return not permissions.must_use_mfa(authenticator.user)

    def is_mfa_enabled(self, user, types=None):
        # In demo mode, only admins sign in with a second factor.
        if permissions.demo_restricted(user):
            return False
        return super().is_mfa_enabled(user, types=types)
