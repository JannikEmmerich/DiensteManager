"""Settings for the test suite (pytest).

Pins values so that tests do not depend on a developer's local ``.env``.
The database still comes from ``DATABASE_URL`` (default: SQLite).
"""

import os

os.environ["DJANGO_DEBUG"] = "False"
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")
os.environ["HIORG_CLIENT_ID"] = "test-client-id"
os.environ["HIORG_CLIENT_SECRET"] = "test-client-secret"
os.environ["HIORG_OV"] = ""
os.environ["CACHE_URL"] = "locmemcache://"

from .settings import *  # noqa: E402, F403
from .settings import STORAGES  # noqa: E402

# Tests assert the English source texts; tests/test_i18n.py covers German explicitly.
LANGUAGE_CODE = "en"

# The test client speaks plain HTTP; the HTTPS redirect would bounce every request.
SECURE_SSL_REDIRECT = False

STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Serve static files straight from the source folders (no collectstatic needed).
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
