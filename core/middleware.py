from contextlib import suppress

from allauth.account.adapter import get_adapter
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import DatabaseError, connection
from django.http import HttpResponse


class HealthCheckMiddleware:
    """Answers ``/healthz`` for the Docker health check.

    Placed first in ``MIDDLEWARE`` so that host validation, the HTTPS redirect and the
    login requirement do not apply to internal probes.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != "/healthz":
            return self.get_response(request)
        try:
            connection.ensure_connection()
        except DatabaseError:
            return HttpResponse("database unavailable", status=503, content_type="text/plain")
        return HttpResponse("ok", content_type="text/plain")


class ClientIpMiddleware:
    """Puts the visitor's IP address into ``REMOTE_ADDR`` instead of the proxy's.

    The address is the one the trusted reverse proxy passes, which the sign-in rate
    limits use as well (``ALLAUTH_TRUSTED_CLIENT_IP_HEADER``, e.g. CF-Connecting-IP, or
    X-Forwarded-For with ``ALLAUTH_TRUSTED_PROXY_COUNT``). ``request.META`` is the WSGI
    environ from which gunicorn writes its access log, so the log shows the visitor too.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # No valid address from the proxy (e.g. a request past it): keep the own one.
        with suppress(PermissionDenied, ImproperlyConfigured):
            request.META["REMOTE_ADDR"] = get_adapter(request).get_client_ip(request)
        return self.get_response(request)
