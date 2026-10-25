import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from allauth.account.models import EmailAddress
from django.urls import reverse
from django.utils import timezone

from accounts.models import Invitation, Role, Unit, User
from duties.models import Duty, DutyPosition, Report

PASSWORD = "a-secure-Password-42"

HIORG_DISCOVERY = {
    "issuer": "https://api.hiorg-server.de",
    "authorization_endpoint": "https://api.hiorg-server.de/oauth/v1/authorize",
    "token_endpoint": "https://api.hiorg-server.de/oauth/v1/token",
    "userinfo_endpoint": "https://api.hiorg-server.de/oauth/v1/userinfo",
    "jwks_uri": "https://api.hiorg-server.de/.well-known/oauth/openid/jwks",
    "scopes_supported": ["openid", "profile", "email"],
    "response_types_supported": ["code"],
    "token_endpoint_auth_methods_supported": ["client_secret_post"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "subject_types_supported": ["public"],
    "id_token_signing_alg_values_supported": ["RS256"],
}


def hiorg_userinfo(sub="hiorg-sub-1", email="alex.example@hiorg.example", **extra):
    """Response of HiOrg's userinfo endpoint, including data we do not want to store."""
    data = {
        "sub": sub,
        "name": "Alex Example",
        "given_name": "Alex",
        "family_name": "Example",
        "preferred_username": "aexample",
        "email": email,
        "email_verified": False,
        "gender": "male",
        "birthdate": "1985-08-12",
        "phone_number": "0171-12345679",
        "phone_number_verified": False,
        "address": {"formatted": "1 Example Street\n12345 Example City"},
        "updated_at": 1572214287,
    }
    data.update(extra)
    return data


def create_account(email, role, unit=None, password=PASSWORD, **fields):
    user = User.objects.create_user(email=email, password=password, role=role, unit=unit, **fields)
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
    return user


def create_invitation(email, role, unit=None, invited_by=None, language="en"):
    invitation = Invitation(
        email=email, role=role, unit=unit, invited_by=invited_by, language=language
    )
    token = invitation.new_token()
    invitation.save()
    return invitation, token


def create_duty(status=Duty.Status.RELEASED, units=(), starts_in=timedelta(days=7), **fields):
    """A duty that starts in a week and lasts four hours; offered to all units by default."""
    starts_at = timezone.now() + starts_in
    values = {
        "title": "Marathon",
        "location": "Market square",
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(hours=4),
        "helpers_needed": 3,
        "status": status,
        "all_units": not units,
    }
    values.update(fields)
    duty = Duty.objects.create(**values)
    duty.units.set(units)
    # Like every duty created in the portal: at least one position.
    DutyPosition.objects.create(duty=duty, count=duty.helpers_needed)
    return duty


def positions_data(*rows, duty=None):
    """POST data of the positions formset: the existing positions of ``duty`` plus ``rows``."""
    existing = list(duty.positions.all()) if duty else []
    data = {
        "positions-TOTAL_FORMS": str(len(existing) + len(rows)),
        "positions-INITIAL_FORMS": str(len(existing)),
        "positions-MIN_NUM_FORMS": "1",
        "positions-MAX_NUM_FORMS": "1000",
    }
    for index, position in enumerate(existing):
        data[f"positions-{index}-id"] = position.pk
        data[f"positions-{index}-qualification"] = position.qualification_id or ""
        data[f"positions-{index}-count"] = position.count
        data[f"positions-{index}-pay_rate"] = position.pay_rate or ""
    for index, row in enumerate(rows, start=len(existing)):
        for field, value in row.items():
            data[f"positions-{index}-{field}"] = value
    return data


def create_report(duty, unit, name="Hanna Helper", reported_by=None, **fields):
    return Report.objects.create(duty=duty, unit=unit, name=name, reported_by=reported_by, **fields)


@pytest.fixture
def unit(db):
    return Unit.objects.create(name="Unit North")


@pytest.fixture
def other_unit(db):
    return Unit.objects.create(name="Unit South")


@pytest.fixture
def admin(db):
    return User.objects.create_superuser(email="admin@example.org", password=PASSWORD)


@pytest.fixture
def dispatcher(db):
    return create_account("dispatcher@example.org", Role.DISPATCHER)


@pytest.fixture
def unit_lead(unit):
    return create_account("lead@example.org", Role.UNIT_LEAD, unit)


@pytest.fixture
def member(unit):
    return create_account("member@example.org", Role.MEMBER, unit)


@pytest.fixture
def other_unit_lead(other_unit):
    return create_account("lead-south@example.org", Role.UNIT_LEAD, other_unit)


def _json_response(status, data):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data).encode()
    response.headers["Content-Type"] = "application/json"
    response.encoding = "utf-8"
    return response


class FakeHiOrgServer:
    """Replaces every HTTP call to HiOrg-Server with canned responses."""

    def __init__(self, userinfo):
        self.userinfo = userinfo
        self.calls = []

    def request(self, session, method, url, *args, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if url == "https://api.hiorg-server.de/.well-known/openid-configuration":
            return _json_response(200, HIORG_DISCOVERY)
        if url == HIORG_DISCOVERY["token_endpoint"] and method.upper() == "POST":
            return _json_response(
                200,
                {"access_token": "test-access-token", "token_type": "Bearer", "expires_in": 3600},
            )
        if url == HIORG_DISCOVERY["userinfo_endpoint"]:
            return _json_response(200, self.userinfo)
        raise AssertionError(f"Unexpected HTTP call: {method} {url}")


@pytest.fixture
def hiorg_login(monkeypatch):
    """Runs the complete sign-in with HiOrg-Server (redirect + callback).

    Returns the callback response and the fake server (with all recorded calls).
    """

    def login(client, userinfo=None, process="login"):
        server = FakeHiOrgServer(userinfo or hiorg_userinfo())
        monkeypatch.setattr(
            requests.Session,
            "request",
            lambda session, method, url, *a, **kw: server.request(session, method, url, *a, **kw),
        )
        start = client.post(
            reverse("openid_connect_login", kwargs={"provider_id": "hiorg"}),
            {"process": process},
        )
        assert start.status_code == 302, start
        redirect_url = urlparse(start["Location"])
        assert redirect_url.netloc == "api.hiorg-server.de"
        state = parse_qs(redirect_url.query)["state"][0]
        response = client.get(
            reverse("openid_connect_callback", kwargs={"provider_id": "hiorg"}),
            {"code": "test-code", "state": state},
        )
        return response, server

    return login
