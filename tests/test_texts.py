"""Names and other one-line texts end up in emails to other people: no line breaks, and
no links in names (core/validators.py)."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.models import Role, User
from billing.models import BankAccount
from core.validators import validate_name, validate_single_line
from duties.models import Report

from .conftest import PASSWORD, create_duty, create_invitation, create_report

NO_LINE_BREAKS = "Line breaks are not allowed here."
NO_LINK = "A name must not contain a link."


@pytest.mark.parametrize(
    "value", ["", "Anna-Lena O’Brien", "Dr. med. Öztürk (KV Musterstadt)", "李雷"]
)
def test_names(value):
    validate_name(value)


@pytest.mark.parametrize(
    "value", ["Max\nMuster", "Max\r", "Max\tMuster", "Max\x0bMuster", "Max\x85", "Max Muster"]
)
def test_no_line_breaks(value):
    for validate in (validate_single_line, validate_name):
        with pytest.raises(ValidationError):
            validate(value)


@pytest.mark.parametrize("value", ["see https://evil.example", "WWW.evil.example", "ftp://x"])
def test_no_links_in_names(value):
    validate_single_line(value)  # fine in a note
    with pytest.raises(ValidationError):
        validate_name(value)


def _profile(**values):
    return {"first_name": "Lea", "last_name": "Lead", "language": "", **values}


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("Security\n\nYour account is locked.\n\nLea", NO_LINE_BREAKS),
        ("Confirm at https://evil.example now, Lea", NO_LINK),
    ],
)
def test_account_names_carry_no_message(client, unit_lead, name, error):
    """The name opens the invitation email ("… has invited you"), which goes to anyone."""
    client.force_login(unit_lead)
    for field in ("first_name", "last_name"):
        response = client.post(reverse("profile"), _profile(**{field: name}))
        assert error in response.text
    unit_lead.refresh_from_db()
    assert name not in (unit_lead.first_name, unit_lead.last_name)


def test_invitation_names_the_inviter_on_one_line(client, unit_lead, mailoutbox):
    """Also with a name stored before names were checked."""
    User.objects.filter(pk=unit_lead.pk).update(first_name="Lea\n\nAct now:\n\n", last_name="Lead")
    client.force_login(unit_lead)
    client.post(
        reverse("invitation_create"),
        {"email": "new@example.org", "role": Role.MEMBER, "language": "en"},
    )
    (message,) = mailoutbox
    assert "\nLea Act now: Lead has invited you to DiensteManager.\n" in message.body


def test_names_of_new_accounts(client, unit):
    _invitation, token = create_invitation("new@example.org", Role.MEMBER, unit)
    response = client.post(
        reverse("accept_invitation", args=[token]),
        {
            "first_name": "Erin, see www.evil.example",
            "last_name": "Example\nMore",
            "password1": PASSWORD,
            "password2": PASSWORD,
        },
    )
    assert NO_LINK in response.text and NO_LINE_BREAKS in response.text
    assert not User.objects.filter(email="new@example.org").exists()


@pytest.mark.parametrize(
    ("row", "error"),
    [
        ({"name": "Hanna\n\nConfirm here: evil.example"}, NO_LINE_BREAKS),
        ({"name": "Hanna https://evil.example"}, NO_LINK),
        ({"name": "Hanna", "note": "late\n\nConfirm here: evil.example"}, NO_LINE_BREAKS),
    ],
)
def test_reports_stay_on_their_line(client, member, mailoutbox, row, error):
    """Helpers are listed in emails to the dispatchers, one line each."""
    duty = create_duty()
    client.force_login(member)
    data = {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0"}
    data.update({f"form-0-{field}": value for field, value in row.items()})
    response = client.post(reverse("report_create", args=[duty.pk]), data)
    assert error in response.text
    assert not Report.objects.exists() and mailoutbox == []


def test_reason_for_a_withdrawal_is_one_line(client, member, unit, mailoutbox):
    report = create_report(create_duty(), unit, reported_by=member, status=Report.Status.CONFIRMED)
    client.force_login(member)
    response = client.post(
        reverse("report_request_withdrawal", args=[report.pk]),
        {"withdrawal_note": "ill\n\nOn the page of the duty: evil.example"},
    )
    assert NO_LINE_BREAKS in response.text
    report.refresh_from_db()
    assert report.withdrawal_requested_at is None and mailoutbox == []


def test_bank_details_are_one_line(client, unit_lead, unit, mailoutbox):
    """Changed bank details are reported by email, to protect against fraud."""
    client.force_login(unit_lead)
    response = client.post(
        reverse("bank_account_edit", args=[unit.pk]),
        {
            "account_holder": "DRK Unit North\nIBAN: DE00 (checked by phone)",
            "iban": "DE89370400440532013000",
            "bank_name": "Bank\nmore",
        },
    )
    assert response.text.count(NO_LINE_BREAKS) == 2
    assert not BankAccount.objects.exists() and mailoutbox == []
