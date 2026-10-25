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
