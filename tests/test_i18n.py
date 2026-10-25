"""German and English UI, language preference per account.

Expected German texts are looked up from the translation catalogue (never hard-coded),
and every check also asserts that a real translation exists.
"""

import gettext as gnu_gettext
import re
import shutil
import subprocess
from pathlib import Path

import allauth
import pytest
from django.conf import settings
from django.core import mail
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext, trans_real

from accounts.models import Role
from duties.models import Duty

from .conftest import create_duty

# Our texts, and allauth's texts with "du" instead of "Sie".
PO_FILES = [
    Path(settings.BASE_DIR) / folder / "de" / "LC_MESSAGES" / "django.po"
    for folder in ("locale", "locale_allauth")
]
COOKIE = settings.LANGUAGE_COOKIE_NAME
FORMAL = re.compile(r"\b(Sie|Ihr|Ihre|Ihren|Ihrem|Ihrer|Ihres|Ihnen)\b")


def german(text):
    """The German translation of ``text``; fails if there is none."""
    with translation.override("de"):
        translated = gettext(text)
    assert translated != text, f"No German translation for {text!r}"
    return translated


@pytest.mark.parametrize("po_file", PO_FILES, ids=lambda path: path.parts[-4])
def test_german_catalogue_is_complete(po_file):
    msgfmt = shutil.which("msgfmt")
    if msgfmt is None:
        pytest.skip("msgfmt (GNU gettext) is not installed")
    result = subprocess.run(
        [msgfmt, "--check", "--statistics", "--output-file=/dev/null", str(po_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "untranslated" not in result.stderr, result.stderr
    assert "fuzzy" not in result.stderr, result.stderr


@pytest.mark.parametrize("po_file", PO_FILES, ids=lambda path: path.parts[-4])
def test_compiled_catalogue_is_up_to_date(po_file):
    hint = "Run: python manage.py compilemessages --ignore=.venv"
    mo_file = po_file.with_suffix(".mo")
    assert mo_file.exists(), hint
    assert mo_file.stat().st_mtime >= po_file.stat().st_mtime, hint


def test_german_texts_say_du():
    """Our texts and allauth's (sign-in, two-factor authentication, its emails) use "du".

    allauth's own German texts say "Sie"; locale_allauth/ replaces them. If an update of
    allauth brings new ones, add them there.
    """
    catalogues = [Path(allauth.__file__).parent / "locale" / "de" / "LC_MESSAGES" / "django.mo"]
    catalogues += [po_file.with_suffix(".mo") for po_file in PO_FILES]
    msgids = set()
    for catalogue in catalogues:
        with catalogue.open("rb") as file:
            msgids.update(gnu_gettext.GNUTranslations(file)._catalog)
    german_texts = trans_real.translation("de")._catalog
    formal = {
        msgid: german_texts[msgid]
        for msgid in msgids
        if msgid and FORMAL.search(german_texts[msgid])
    }
    assert formal == {}


def test_browser_language_decides_for_visitors(client, db):
    text = "Access is by invitation only."
    page = client.get(reverse("account_login"), HTTP_ACCEPT_LANGUAGE="de")
    assert german(text) in page.text
    assert '<html lang="de">' in page.text
    page = client.get(reverse("account_login"), HTTP_ACCEPT_LANGUAGE="en")
    assert text in page.text


def test_visitors_can_switch_language(client, db):
    response = client.post(
        reverse("set_language"),
        {"language": "en", "next": reverse("account_login")},
        HTTP_ACCEPT_LANGUAGE="de",
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("account_login")
    assert response.cookies[COOKIE].value == "en"
    page = client.get(reverse("account_login"), HTTP_ACCEPT_LANGUAGE="de")
    assert "Access is by invitation only." in page.text


def test_language_switch_requires_post(client, db):
    assert client.get(reverse("set_language")).status_code == 405


def test_language_switch_is_stored_on_the_account(client, member):
    client.force_login(member)
    client.post(reverse("set_language"), {"language": "de", "next": "/"})
    member.refresh_from_db()
    assert member.language == "de"


def test_unknown_language_is_ignored(client, member):
    client.force_login(member)
    client.post(reverse("set_language"), {"language": "xx", "next": "/"})
    member.refresh_from_db()
    assert member.language == ""


def test_account_language_wins_over_browser_and_syncs_cookie(client, member):
    member.language = "de"
    member.save()
    client.force_login(member)
    response = client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="en")
    assert german("Overview") in response.text
    assert response.cookies[COOKIE].value == "de"

    member.language = "en"
    member.save()
    response = client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="de")
    assert "Overview" in response.text


def test_profile_confirms_in_the_newly_chosen_language(client, member):
    client.force_login(member)
    response = client.post(
        reverse("profile"),
        {"first_name": "Mia", "last_name": "Member", "language": "de"},
        HTTP_ACCEPT_LANGUAGE="en",
        follow=True,
    )
    assert german("Saved.") in response.text


def test_choosing_automatic_language_follows_browser_again(client, member):
    member.language = "de"
    member.save()
    client.force_login(member)
    response = client.post(
        reverse("profile"), {"first_name": "Mia", "last_name": "Member", "language": ""}
    )
    member.refresh_from_db()
    assert member.language == ""
    assert response.cookies[COOKIE].value == ""  # cookie deleted
    assert "Overview" in client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="en").text


def test_invitation_email_uses_invitation_language(client, dispatcher, unit):
    client.force_login(dispatcher)
    for email, language in [("a@example.org", "de"), ("b@example.org", "en")]:
        client.post(
            reverse("invitation_create"),
            {"email": email, "role": Role.MEMBER, "unit": unit.pk, "language": language},
        )
    german_mail, english_mail = mail.outbox
    assert german("Hello,") in german_mail.body
    assert german_mail.subject == german("Invitation to %(site)s") % {"site": settings.SITE_NAME}
    assert "Hello," in english_mail.body
    assert english_mail.subject == f"Invitation to {settings.SITE_NAME}"


def test_password_reset_email_uses_account_language(client, member):
    member.language = "de"
    member.save()
    client.post(
        reverse("account_reset_password"), {"email": member.email}, HTTP_ACCEPT_LANGUAGE="en"
    )
    assert len(mail.outbox) == 1
    assert german("Password Reset Email") in mail.outbox[0].subject


def test_notifications_use_the_language_of_each_recipient(client, dispatcher, unit_lead, member):
    unit_lead.language = "de"
    unit_lead.save()
    duty = create_duty(status=Duty.Status.DRAFT, title="Marathon")
    client.force_login(dispatcher)
    client.post(reverse("duty_release", args=[duty.pk]), HTTP_ACCEPT_LANGUAGE="en")
    by_recipient = {message.to[0]: message for message in mail.outbox}
    german_mail = by_recipient[unit_lead.email]
    assert german("A new duty has been released for your unit:") in german_mail.body
    assert german_mail.subject.startswith(
        "[DiensteManager] " + german("New duty: %(title)s (%(period)s)").split("%")[0]
    )
    # Without a language of its own, an account gets the default language (en in tests).
    assert "A new duty has been released for your unit:" in by_recipient[member.email].body


def test_dispatcher_email_footer_uses_the_recipient_language(client, dispatcher, unit_lead):
    unit_lead.language = "de"
    unit_lead.save()
    client.force_login(dispatcher)
    client.post(
        reverse("mailing_create"),
        {"subject": "Hallo", "body": "Text", "audience": "unit_leads", "all_units": "True"},
        HTTP_ACCEPT_LANGUAGE="en",
    )
    (message,) = mail.outbox
    expected = german("%(sender)s sent this email via %(site)s. Replies go directly to %(sender)s.")
    assert expected % {"sender": dispatcher, "site": settings.SITE_NAME} in message.body
