from allauth.mfa.models import Authenticator
from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import translation
from django.utils.translation import gettext as _

from . import permissions

# Authenticators that count as two-factor authentication (recovery codes alone do not).
MFA_TYPES = [Authenticator.Type.TOTP, Authenticator.Type.WEBAUTHN]


def set_language_cookie(response, language):
    """Sets the language cookie exactly like Django's ``set_language`` view does."""
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )


def _account_language(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ""
    return user.language if user.language in dict(settings.LANGUAGES) else ""


class UserLanguageMiddleware:
    """Applies the language stored on the signed-in account.

    ``LocaleMiddleware`` picks the language from the cookie or the browser; this
    middleware overrides it when the account has a language set. The cookie is kept in
    sync, so the choice still applies after signing out.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = _account_language(request)
        if language:
            translation.activate(language)
            request.LANGUAGE_CODE = language
        response = self.get_response(request)
        # Evaluated again: the view may have signed someone in or changed the setting.
        language = _account_language(request)
        if language and request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME) != language:
            set_language_cookie(response, language)
        return response


# What an account without two-factor authentication may still open: the setup itself
# (activating needs the password again), signing out and switching the language.
MFA_SETUP_VIEWS = {
    "mfa_index",
    "mfa_activate_totp",
    "account_reauthenticate",
    "account_logout",
    "set_language",
}


# What only admins may open in demo mode: changing the email address or the password,
# resetting the password (its email only reaches the inbox) and linking HiOrg (also
# refused when it starts from the HiOrg sign-in, see SocialAccountAdapter).
DEMO_ADMIN_ONLY_VIEWS = {
    "admin:password_change",
    "admin:password_change_done",
    "account_email",
    "account_change_password",
    "account_set_password",
    "account_reset_password",
    "account_reset_password_done",
    "account_reset_password_from_key",
    "account_reset_password_from_key_done",
    "socialaccount_connections",
}
# Two-factor authentication is off for everyone else, but admins still sign in with it.
MFA_SIGN_IN_VIEWS = {"mfa_authenticate", "mfa_reauthenticate", "mfa_trust"}


class DemoModeMiddleware:
    """In demo mode, only admins change their sign-in (see ``DEMO_MODE``)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        name = request.resolver_match.view_name or ""
        admin_only = name in DEMO_ADMIN_ONLY_VIEWS or (
            name.startswith("mfa_") and name not in MFA_SIGN_IN_VIEWS
        )
        if not admin_only or not permissions.demo_restricted(request.user):
            return None
        messages.error(request, _("In demo mode, only an admin can do this."))
        return redirect("profile" if request.user.is_authenticated else "account_login")


class MfaRequiredMiddleware:
    """Sends accounts that must use two-factor authentication to its setup first.

    Applies to the roles in ``MFA_REQUIRED_ROLES``, e.g. dispatchers, who see the bank
    details of all units and create the billings.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if (
            request.resolver_match.url_name in MFA_SETUP_VIEWS
            or not permissions.must_use_mfa(request.user)
            or is_mfa_enabled(request.user, types=MFA_TYPES)
        ):
            return None
        messages.warning(
            request,
            _(
                "Your account must be protected with two-factor authentication. "
                "Please set it up now."
            ),
        )
        return redirect("mfa_activate_totp")
