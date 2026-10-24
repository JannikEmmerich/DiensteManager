from django.conf import settings
from django.utils import translation


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
