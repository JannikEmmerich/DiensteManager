"""Emails in the background: the queue, retries, the daily summary and the worker."""

import signal
import threading
from datetime import datetime, timedelta
from smtplib import SMTPException
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from billing.models import BankAccount
from duties.models import Duty, Report
from lifecycle import retention
from mailings.models import Mailing
from outbox import worker
from outbox.models import OutgoingEmail

from .conftest import create_account, create_duty, create_report, post_billing

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def queue(settings):
    settings.EMAIL_QUEUE = True


@pytest.fixture
def daily_member(unit):
    return create_account(
        "daily@example.org",
        Role.MEMBER,
        unit,
        first_name="Dora",
        email_delivery=User.EmailDelivery.DAILY,
    )


def _release(client, dispatcher, **fields):
    duty = create_duty(status=Duty.Status.DRAFT, **fields)
    client.force_login(dispatcher)
    client.post(reverse("duty_release", args=[duty.pk]))
    return duty


def _fail_for(monkeypatch, *addresses):
    """Lets sending fail for these addresses (all addresses if none are given)."""
    original = mail.backends.locmem.EmailBackend.send_messages

    def send_messages(self, messages):
        if any(not addresses or set(m.to) & set(addresses) for m in messages):
            raise SMTPException("no connection")
        return original(self, messages)

    monkeypatch.setattr(mail.backends.locmem.EmailBackend, "send_messages", send_messages)


# --- The queue -------------------------------------------------------------------------------


def test_notifications_wait_for_the_worker(
    client, queue, dispatcher, unit_lead, member, mailoutbox
):
    _release(client, dispatcher, title="Marathon")
    assert mailoutbox == []
    assert OutgoingEmail.objects.filter(status=OutgoingEmail.Status.QUEUED).count() == 2

    call_command("run_worker", "--once")
    assert sorted(m.to[0] for m in mailoutbox) == ["lead@example.org", "member@example.org"]
    assert "Marathon" in mailoutbox[0].subject
    assert mailoutbox[0].body.startswith("Hello")
    for email in OutgoingEmail.objects.all():
        assert (email.status, email.attempts, email.last_error) == ("sent", 1, "")
        assert email.sent_at is not None


def test_billing_pdf_travels_with_the_email(client, queue, dispatcher, unit, unit_lead, mailoutbox):
    BankAccount.objects.create(unit=unit, account_holder="DRK", iban="DE89370400440532013000")
    duty = create_duty(starts_in=timedelta(days=-3), pay_type=Duty.PayType.FLAT, pay_rate=40)
    report = create_report(duty, unit, status=Report.Status.CONFIRMED)
    client.force_login(dispatcher)
    post_billing(client, unit, {f"include_{report.pk}": "on"})
    assert mailoutbox == []
    worker.send_due()
    ((filename, content, mimetype),) = mailoutbox[0].attachments
    assert filename.endswith(".pdf") and mimetype == "application/pdf"
    assert content.startswith(b"%PDF")


def test_mailings_are_queued_with_reply_to(client, queue, dispatcher, unit_lead, mailoutbox):
    client.force_login(dispatcher)
    response = client.post(
        reverse("mailing_create"),
        {
            "subject": "Briefing",
            "body": "We meet at 7 pm.",
            "audience": Mailing.Audience.UNIT_LEADS,
            "all_units": "True",
        },
        follow=True,
    )
    assert "The email to 1 person is being sent." in response.text
    assert mailoutbox == []
    worker.send_due()
    (message,) = mailoutbox
    assert message.reply_to == ["dispatcher@example.org"]
    assert "via DiensteManager" in message.from_email
    assert message.message()["Reply-To"] == "dispatcher@example.org"


# --- Retries ---------------------------------------------------------------------------------


def test_failed_emails_are_tried_again_and_then_given_up(
    client, queue, admin, dispatcher, member, mailoutbox, monkeypatch
):
    _release(client, dispatcher)
    email = OutgoingEmail.objects.get()
    _fail_for(monkeypatch)
    now = timezone.now()
    delays = [1, 5, 15, 60, 240]
    for attempt, minutes in enumerate(delays, start=1):
        assert worker.send_due(now) == 1
        email.refresh_from_db()
        assert (email.status, email.attempts) == ("queued", attempt)
        assert email.next_attempt_at == now + timedelta(minutes=minutes)
        assert email.last_error == "SMTPException: no connection"
        assert worker.send_due(now) == 0  # not due yet
        now = email.next_attempt_at
    assert worker.send_due(now) == 1
    email.refresh_from_db()
    assert (email.status, email.attempts) == ("failed", 6)

    # An administrator sends it again once the mail server works.
    monkeypatch.undo()
    client.force_login(admin)
    client.post(
        reverse("admin:outbox_outgoingemail_changelist"),
        {"action": "send_again", "_selected_action": [email.pk]},
    )
    email.refresh_from_db()
    assert (email.status, email.attempts, email.last_error) == ("queued", 0, "")
    worker.send_due()
    assert len(mailoutbox) == 1


def test_one_bad_email_does_not_hold_up_the_others(
    client, queue, dispatcher, unit_lead, member, mailoutbox, monkeypatch
):
    _release(client, dispatcher)
    _fail_for(monkeypatch, "member@example.org")
    worker.send_due()
    assert [m.to for m in mailoutbox] == [["lead@example.org"]]
    assert OutgoingEmail.objects.get(to=["member@example.org"]).status == "queued"


def test_unreachable_mail_server_counts_for_the_whole_batch(
    client, queue, dispatcher, unit_lead, member, monkeypatch
):
    def refuse(self):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(mail.backends.locmem.EmailBackend, "open", refuse, raising=False)
    _release(client, dispatcher)
    assert worker.send_due() == 2
    assert set(OutgoingEmail.objects.values_list("attempts", "status")) == {(1, "queued")}


def test_claimed_emails_are_not_sent_twice(client, queue, dispatcher, member, mailoutbox):
    _release(client, dispatcher)
    now = timezone.now()
    claimed = worker._claim(now)
    assert len(claimed) == 1
    # A second worker finds nothing until the claim runs out.
    assert worker.send_due(now) == 0
    assert worker.send_due(now + worker.CLAIM_DURATION) == 1


# --- Daily summary ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (
            datetime(2026, 10, 25, 10, 0, tzinfo=BERLIN),
            datetime(2026, 10, 24, 18, 0, tzinfo=BERLIN),
        ),
        (
            datetime(2026, 10, 25, 18, 0, tzinfo=BERLIN),
            datetime(2026, 10, 25, 18, 0, tzinfo=BERLIN),
        ),
        (
            datetime(2026, 10, 25, 23, 0, tzinfo=BERLIN),
            datetime(2026, 10, 25, 18, 0, tzinfo=BERLIN),
        ),
        # Daylight saving time begins on 29 March 2026: 18:00 CET (17:00 UTC) the day before.
        (
            datetime(2026, 3, 29, 10, 0, tzinfo=BERLIN),
            datetime(2026, 3, 28, 17, 0, tzinfo=ZoneInfo("UTC")),
        ),
    ],
)
def test_digest_time(now, expected):
    assert worker.digest_time(now) == expected


def test_daily_summary(client, queue, dispatcher, unit, unit_lead, daily_member, mailoutbox):
    _release(client, dispatcher, title="Marathon")
    cancelled = _release(client, dispatcher, title="Concert")
    create_report(cancelled, unit, reported_by=daily_member)
    client.post(reverse("duty_cancel", args=[cancelled.pk]))
    worker.send_due()
    # The unit lead gets everything right away; the member only the cancellation.
    received = [(m.to[0], m.subject) for m in mailoutbox]
    assert [address for address, _subject in received].count("lead@example.org") == 3
    assert [subject for address, subject in received if address == "daily@example.org"] == [
        "[DiensteManager] Duty cancelled: Concert"
    ]
    waiting = OutgoingEmail.objects.filter(status="queued", digest=True)
    assert waiting.count() == 2
    assert not waiting[0].body.startswith("Hello")
    assert "notification settings" not in waiting[0].body

    created = waiting.latest("created_at").created_at
    assert worker.send_digests(created) == 0
    mailoutbox.clear()
    assert worker.send_digests(created + timedelta(days=1)) == 1
    assert worker.send_digests(created + timedelta(days=1)) == 0
    worker.send_due()
    (summary,) = mailoutbox
    assert summary.to == ["daily@example.org"]
    assert summary.subject == "[DiensteManager] Summary: 2 notifications"
    assert summary.body.startswith("Hello Dora,\n")
    assert "Here are the 2 things that happened since the last summary:" in summary.body
    assert "== New duty: Marathon" in summary.body
    assert "== New duty: Concert" in summary.body
    assert summary.body.count("Hello") == 1
    assert "https://dienste.example.org/profile/" in summary.body
    stored = OutgoingEmail.objects.get(subject=summary.subject)
    assert set(stored.items.values_list("status", flat=True)) == {"digested"}


def test_summary_in_the_language_of_the_account(
    client, queue, dispatcher, daily_member, mailoutbox
):
    daily_member.language = "de"
    daily_member.save()
    _release(client, dispatcher, title="Marathon")
    worker.send_digests(timezone.now() + timedelta(days=1))
    worker.send_due()
    (summary,) = mailoutbox
    assert summary.subject == "[DiensteManager] Zusammenfassung: 1 Benachrichtigung"
    assert summary.body.startswith("Hallo Dora,\n")


def test_no_summary_for_deactivated_accounts(client, queue, dispatcher, daily_member, mailoutbox):
    _release(client, dispatcher)
    daily_member.is_active = False
    daily_member.save()
    assert worker.send_digests(timezone.now() + timedelta(days=1)) == 0
    worker.send_due()
    assert mailoutbox == []
    assert OutgoingEmail.objects.get().status == "failed"


def test_queued_emails_of_deactivated_accounts_are_not_sent(
    client, queue, dispatcher, member, mailoutbox
):
    _release(client, dispatcher)
    assert OutgoingEmail.objects.filter(recipient=member).exists()
    member.is_active = False
    member.save()
    worker.send_due()
    assert all(m.to != [member.email] for m in mailoutbox)
    assert OutgoingEmail.objects.get(recipient=member).status == "failed"


@pytest.mark.parametrize("hour", ["-1", "24"])
def test_digest_hour_is_checked(hour, monkeypatch):
    from runpy import run_path

    from django.core.exceptions import ImproperlyConfigured

    monkeypatch.setenv("EMAIL_DIGEST_HOUR", hour)
    with pytest.raises(ImproperlyConfigured):
        run_path("config/settings.py")


def test_without_queue_everything_is_sent_right_away(client, dispatcher, daily_member, mailoutbox):
    _release(client, dispatcher, title="Marathon")
    (message,) = mailoutbox
    assert message.body.startswith("Hello Dora,")
    assert not OutgoingEmail.objects.exists()


def test_profile_offers_the_summary_only_with_the_queue(client, settings, member):
    client.force_login(member)
    assert 'name="email_delivery"' not in client.get(reverse("profile")).text
    settings.EMAIL_QUEUE = True
    page = client.get(reverse("profile")).text
    assert 'name="email_delivery"' in page
    assert "The summary comes every day at 18:00." in page
    client.post(
        reverse("profile"),
        {
            "first_name": "Mia",
            "last_name": "Member",
            "language": "",
            "notify_new_duties": "on",
            "notify_reports": "on",
            "email_delivery": "daily",
        },
    )
    member.refresh_from_db()
    assert member.email_delivery == User.EmailDelivery.DAILY


# --- Worker and retention ----------------------------------------------------------------------


def test_worker_loop(client, queue, dispatcher, member, mailoutbox, monkeypatch, tmp_path, caplog):
    _release(client, dispatcher)
    calls = []
    monkeypatch.setattr(retention, "apply", lambda **kwargs: calls.append("retention") or {})

    def broken_digests():
        raise DatabaseError("connection lost")

    monkeypatch.setattr(worker, "send_digests", broken_digests)
    # Closing the connection would end the test's transaction (as for requests in tests).
    monkeypatch.setattr("outbox.management.commands.run_worker.close_old_connections", lambda: None)
    # One round only: waiting for the next round ends the worker.
    monkeypatch.setattr(threading.Event, "wait", lambda self, timeout=None: self.set())
    handlers = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    heartbeat = tmp_path / "alive"
    call_command("run_worker", "--heartbeat", str(heartbeat))
    assert calls == ["retention"]
    assert "Could not process the email queue" in caplog.text
    assert heartbeat.exists()
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) == handlers

    monkeypatch.undo()
    call_command("run_worker", "--once")
    assert len(mailoutbox) == 1


def test_retention_of_sent_emails(settings, member):
    settings.RETENTION_OUTBOX_DAYS = 30
    old = timezone.now() - timedelta(days=31)
    for status in ["queued", "sent", "digested", "failed"]:
        OutgoingEmail.objects.create(to=["a@example.org"], subject=status, body="", status=status)
    OutgoingEmail.objects.update(created_at=old)
    OutgoingEmail.objects.create(to=["a@example.org"], subject="recent", body="", status="sent")
    assert retention.apply()["outgoing_emails_deleted"] == 3
    assert sorted(OutgoingEmail.objects.values_list("subject", flat=True)) == ["queued", "recent"]


def test_admin_lists_emails(client, admin):
    OutgoingEmail.objects.create(to=["a@example.org"], subject="Hello", body="Text")
    client.force_login(admin)
    page = client.get(reverse("admin:outbox_outgoingemail_changelist"))
    assert page.status_code == 200
    assert "a@example.org" in page.text
