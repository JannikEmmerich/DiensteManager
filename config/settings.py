"""Django settings for DiensteManager.

Everything that depends on the environment is read from environment variables or
from a local ``.env`` file (template: ``.env.example``).
"""

from pathlib import Path

import environ
from django.contrib.messages import constants as message_constants
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
if (BASE_DIR / ".env").is_file():
    env.read_env(BASE_DIR / ".env")

DEBUG = env.bool("DJANGO_DEBUG", default=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
    SECRET_KEY = "insecure-key-for-local-development-only"

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]"] if DEBUG else []
)
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

SITE_NAME = env("SITE_NAME", default="DiensteManager")
ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")


# --- Applications ---------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django_htmx",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "allauth.mfa",
    "core",
    "accounts",
    "duties",
    "mailings",
    "billing",
]

MIDDLEWARE = [
    "core.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must come after AuthenticationMiddleware: applies the language stored on the account.
    "accounts.middleware.UserLanguageMiddleware",
    # Every page requires a login unless explicitly exempted with @login_not_required
    # (e.g. sign-in, password reset, invitation link, language switch).
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    },
]


# --- Database and cache -----------------------------------------------------------

DATABASES = {
    "default": env.db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DJANGO_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# With several worker processes use a shared cache (e.g. CACHE_URL=dbcache://django_cache),
# otherwise the sign-in rate limits only apply per process.
CACHES = {"default": env.cache_url("CACHE_URL", default="locmemcache://")}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Users and sign-in --------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "home"

# django-allauth: accounts are only created through invitations; sign-in by email address.
ACCOUNT_ADAPTER = "accounts.adapters.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_CHANGE_EMAIL = True
ACCOUNT_EMAIL_NOTIFICATIONS = True
# Never send emails to addresses without an account (password reset).
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False
ACCOUNT_REAUTHENTICATION_REQUIRED = True
ACCOUNT_LOGOUT_REDIRECT_URL = "account_login"
ACCOUNT_EMAIL_SUBJECT_PREFIX = f"[{SITE_NAME}] "
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"

# Sign-in with HiOrg-Server: new accounts only with an invitation, no automatic
# linking via matching email addresses, HiOrg tokens are not stored.
SOCIALACCOUNT_ADAPTER = "accounts.adapters.SocialAccountAdapter"
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_STORE_TOKENS = False

HIORG_CLIENT_ID = env("HIORG_CLIENT_ID", default="")
HIORG_CLIENT_SECRET = env("HIORG_CLIENT_SECRET", default="")
HIORG_OV = env("HIORG_OV", default="")

SOCIALACCOUNT_PROVIDERS = {}
if HIORG_CLIENT_ID:
    _hiorg_settings = {
        # Discovery: https://api.hiorg-server.de/.well-known/openid-configuration
        "server_url": "https://api.hiorg-server.de",
        # HiOrg only accepts client_secret_post at its token endpoint.
        "token_auth_method": "client_secret_post",
        "scope": ["openid", "profile"],
    }
    if HIORG_OV:
        _hiorg_settings["auth_params"] = {"ov": HIORG_OV}
    SOCIALACCOUNT_PROVIDERS["openid_connect"] = {
        "APPS": [
            {
                "provider_id": "hiorg",
                "name": "HiOrg-Server",
                "client_id": HIORG_CLIENT_ID,
                "secret": HIORG_CLIENT_SECRET,
                "settings": _hiorg_settings,
            },
        ],
    }

MFA_TOTP_ISSUER = SITE_NAME

# Number of trusted reverse proxies in front of the app (Caddy: 1), so that allauth
# uses the real client IP for its rate limits.
ALLAUTH_TRUSTED_PROXY_COUNT = env.int("ALLAUTH_TRUSTED_PROXY_COUNT", default=0)

INVITATION_VALIDITY_DAYS = env.int("INVITATION_VALIDITY_DAYS", default=14)


# --- Email ---------------------------------------------------------------------------

# Without EMAIL_HOST, emails are only printed to the console (locally or in
# `docker compose logs web`), e.g. invitation links while trying things out.
_smtp_host = env("EMAIL_HOST", default="")
if _smtp_host:
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": {
                "host": _smtp_host,
                "port": env.int("EMAIL_PORT", default=587),
                "username": env("EMAIL_HOST_USER", default=""),
                "password": env("EMAIL_HOST_PASSWORD", default=""),
                "use_tls": env.bool("EMAIL_USE_TLS", default=True),
                "use_ssl": env.bool("EMAIL_USE_SSL", default=False),
                "timeout": 10,
            },
        },
    }
else:
    MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=f"{SITE_NAME} <noreply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Notifications (duties/notifications.py) use the same subject prefix as allauth.
EMAIL_SUBJECT_PREFIX = ACCOUNT_EMAIL_SUBJECT_PREFIX


# --- Languages, time zone, static files -------------------------------------------------

# The code and the translation keys are English; the German texts live in
# locale/de/LC_MESSAGES/django.po. German is the default for visitors whose
# browser asks for neither language.
LANGUAGE_CODE = "de"
LANGUAGES = [
    ("de", _("German")),
    ("en", _("English")),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
LANGUAGE_COOKIE_HTTPONLY = True
LANGUAGE_COOKIE_SECURE = not DEBUG
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MESSAGE_TAGS = {message_constants.ERROR: "danger"}


# --- Security in production (behind the HTTPS reverse proxy) ------------------------------

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 365)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
