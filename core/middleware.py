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
