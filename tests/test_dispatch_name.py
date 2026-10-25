"""The dispatchers' name comes from DISPATCH_NAME, the same in every language."""

import pytest
from django.urls import reverse
from django.utils import translation

from accounts.forms import InvitationForm
from accounts.models import Role
from duties.models import Report

from .conftest import create_duty, create_report


@pytest.fixture
def einsatzleitung(settings):
    settings.DISPATCH_NAME = "Einsatzleitung"


def test_default_name(client, member):
    client.force_login(member)
    duty = create_duty()
    assert (
        "The Disposition confirms each report"
        in client.get(reverse("report_create", args=[duty.pk])).text
    )
    assert str(Role.DISPATCHER.label) == "Disposition (internal)"


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "The Einsatzleitung confirms each report"),
        ("de", "Die Einsatzleitung bestätigt jede Meldung"),
    ],
)
def test_pages_use_the_name(client, member, einsatzleitung, language, text):
    member.language = language
    member.save()
    client.force_login(member)
    page = client.get(reverse("report_create", args=[create_duty().pk])).text
    assert text in page
    assert "Disposition" not in page


def test_roles_and_forms_use_the_name(admin, einsatzleitung):
    with translation.override("en"):
        assert str(Role.DISPATCHER.label) == "Einsatzleitung (internal)"
        choices = dict(InvitationForm(inviter=admin).fields["role"].choices)
        assert choices[Role.DISPATCHER] == "Einsatzleitung (internal)"


def test_emails_use_the_name(client, dispatcher, unit, unit_lead, mailoutbox, einsatzleitung):
    unit_lead.language = "de"
    unit_lead.save()
    report = create_report(create_duty(), unit, name="Mia", status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    client.post(reverse("report_take_back", args=[report.pk]))
    assert "die Einsatzleitung hat die Meldung für Mia zurückgenommen." in mailoutbox[0].body
