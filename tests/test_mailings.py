from email.utils import getaddresses
from smtplib import SMTPException

import pytest
from django.core import mail
from django.urls import reverse

from accounts.models import Role
from duties.models import Duty, Report
from mailings.models import Mailing

from .conftest import create_account, create_duty, create_report


def _send(client, **data):
    values = {
        "subject": "Briefing tonight",
        "body": "We meet at 7 pm.\nPlease be on time.",
        "audience": Mailing.Audience.UNIT_LEADS,
        "all_units": "True",
    }
    values.update(data)
    return client.post(reverse("mailing_create"), values)


def _recipients(outbox):
    return sorted(address for message in outbox for address in message.to)


@pytest.fixture
def named_dispatcher(dispatcher):
    dispatcher.first_name = "Dana"
    dispatcher.last_name = "Dispatcher"
    dispatcher.save()
    return dispatcher


def test_email_to_all_unit_leads(
    client, named_dispatcher, unit_lead, member, other_unit_lead, mailoutbox
):
    client.force_login(named_dispatcher)
    response = _send(client)
    mailing = Mailing.objects.get()
    assert response.status_code == 302
    assert response["Location"] == mailing.get_absolute_url()
    assert _recipients(mailoutbox) == ["lead-south@example.org", "lead@example.org"]
    assert set(mailing.recipients.all()) == {unit_lead, other_unit_lead}
    assert mailing.sent_by == named_dispatcher

    message = mailoutbox[0]
    assert message.subject == "[DiensteManager] Briefing tonight"
    assert message.reply_to == ["Dana Dispatcher <dispatcher@example.org>"]
    assert getaddresses([message.from_email]) == [
        ("Dana Dispatcher via DiensteManager", "noreply@localhost")
    ]
    assert message.body.startswith("We meet at 7 pm.\nPlease be on time.\n")
    assert "Replies go directly to Dana Dispatcher." in message.body
    assert "You receive it as Unit lead of the unit Unit" in message.body
    # Reply-To survives into the actual message headers.
    assert message.message()["Reply-To"] == "Dana Dispatcher <dispatcher@example.org>"


def test_email_to_everyone_of_selected_units(
    client, dispatcher, unit, unit_lead, member, other_unit_lead, mailoutbox
):
    create_account("blocked@example.org", Role.MEMBER, unit, is_active=False)
    # Notification settings do not apply to emails written by dispatchers.
    create_account("quiet@example.org", Role.MEMBER, unit, notify_new_duties=False)
    client.force_login(dispatcher)
    _send(client, audience=Mailing.Audience.ALL, all_units="False", units=[unit.pk])
    assert _recipients(mailoutbox) == [
        "lead@example.org",
        "member@example.org",
        "quiet@example.org",
    ]


def test_copy_to_the_sender(client, dispatcher, unit_lead, mailoutbox):
    client.force_login(dispatcher)
    _send(client, send_copy="on")
    assert _recipients(mailoutbox) == ["dispatcher@example.org", "lead@example.org"]
    copy = next(m for m in mailoutbox if m.to == ["dispatcher@example.org"])
    assert copy.body.startswith("This is a copy of your email to 1 person.")
    assert list(Mailing.objects.get().recipients.all()) == [unit_lead]


def test_reply_to_without_a_name(client, dispatcher, unit_lead, mailoutbox):
    client.force_login(dispatcher)
    _send(client)
    assert mailoutbox[0].reply_to == ["dispatcher@example.org"]
    assert "dispatcher@example.org via DiensteManager" in mailoutbox[0].from_email


def test_names_with_umlauts_and_special_characters(client, dispatcher, unit_lead, mailoutbox):
    dispatcher.first_name = "Jörg"
    dispatcher.last_name = "Müller, Dispo"
    dispatcher.save()
    client.force_login(dispatcher)
    _send(client)
    message = mailoutbox[0].message()
    assert getaddresses([str(message["Reply-To"])]) == [("Jörg Müller, Dispo", dispatcher.email)]


def test_line_breaks_never_reach_the_headers(client, dispatcher, unit_lead, mailoutbox):
    dispatcher.first_name = "Dana\nBcc: someone@example.org"
    dispatcher.save()
    client.force_login(dispatcher)
    _send(client, subject="Line one\nLine two")
    message = mailoutbox[0].message()
    assert message["Subject"] == "[DiensteManager] Line one Line two"
    assert "Bcc" not in message
    assert "\n" not in str(message["Reply-To"])


@pytest.mark.parametrize("user", ["unit_lead", "member"])
def test_only_dispatchers_send_emails(client, request, user, mailoutbox):
    client.force_login(request.getfixturevalue(user))
    assert client.get(reverse("mailing_create")).status_code == 403
    assert _send(client).status_code == 403
    assert client.get(reverse("mailing_list")).status_code == 403
    assert mailoutbox == []


def test_selected_units_need_a_unit(client, dispatcher, unit_lead, mailoutbox):
    client.force_login(dispatcher)
    response = _send(client, all_units="False")
    assert "Please select at least one unit." in response.text
    assert mailoutbox == []


def test_no_email_without_recipients(client, dispatcher, unit, member, mailoutbox):
    client.force_login(dispatcher)
    response = _send(client, all_units="False", units=[unit.pk])  # unit leads only
    assert "Nobody in the selected units would receive this email." in response.text
    assert not Mailing.objects.exists()


def test_sending_error_keeps_nothing(client, dispatcher, unit_lead, monkeypatch):
    def fail(self, messages):
        raise SMTPException("no connection")

    monkeypatch.setattr(mail.backends.locmem.EmailBackend, "send_messages", fail)
    client.force_login(dispatcher)
    response = _send(client)
    assert response.status_code == 200
    assert "The email could not be sent." in response.text
    assert not Mailing.objects.exists()


def test_email_about_a_duty_is_prefilled(client, dispatcher, unit, other_unit, unit_lead):
    duty = create_duty(title="Marathon")
    create_report(duty, other_unit)
    create_report(duty, unit, status=Report.Status.WITHDRAWN)
    client.force_login(dispatcher)
    response = client.get(reverse("mailing_create") + f"?duty={duty.pk}")
    form = response.context["form"]
    assert form["subject"].value() == "Marathon"
    assert form["all_units"].value() == "False"
    assert [u.pk for u in form.initial["units"]] == [other_unit.pk]
    assert "About the duty <strong>Marathon</strong>" in response.text


def test_email_about_a_duty_links_to_it(client, dispatcher, unit_lead, mailoutbox):
    duty = create_duty(title="Marathon")
    client.force_login(dispatcher)
    _send(client, duty=duty.pk)
    assert Mailing.objects.get().duty == duty
    assert f"http://testserver{duty.get_absolute_url()}" in mailoutbox[0].body
    assert "Duty: Marathon, " in mailoutbox[0].body


def test_drafts_cannot_be_linked(client, dispatcher, unit_lead):
    draft = create_duty(status=Duty.Status.DRAFT)
    client.force_login(dispatcher)
    response = client.get(reverse("mailing_create") + f"?duty={draft.pk}")
    assert response.context["duty"] is None
    _send(client, duty=draft.pk)
    assert not Mailing.objects.exists()


def test_email_to_a_unit_is_prefilled(client, dispatcher, unit):
    client.force_login(dispatcher)
    response = client.get(reverse("mailing_create") + f"?unit={unit.pk}")
    assert response.context["form"]["units"].value() == [unit.pk]


def test_recipient_count(client, dispatcher, unit, unit_lead, member, other_unit_lead):
    client.force_login(dispatcher)
    url = reverse("mailing_recipients")
    response = client.get(url, {"audience": "unit_leads", "all_units": "True"})
    assert "<strong>2</strong> people" in response.text
    response = client.get(url, {"audience": "all", "all_units": "False", "units": [unit.pk]})
    assert "<strong>2</strong> people" in response.text
    response = client.get(url, {"audience": "unit_leads", "all_units": "False"})
    assert "<strong>0</strong> people" in response.text


def test_sent_emails_are_listed(client, dispatcher, unit_lead, mailoutbox):
    client.force_login(dispatcher)
    _send(client, subject="Shift plan")
    mailing = Mailing.objects.get()
    assert "Shift plan" in client.get(reverse("mailing_list")).text
    page = client.get(mailing.get_absolute_url()).text
    assert "lead@example.org" in page
    assert "Please be on time." in page
