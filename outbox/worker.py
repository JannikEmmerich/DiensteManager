"""What the worker (``manage.py run_worker``) does with the stored emails.

- ``send_due`` sends the queued emails that are due. An email that cannot be sent is
  tried again after 1, 5, 15, 60 and 240 minutes, then it counts as failed (see the
  admin, where it can be sent again).
- ``send_digests`` bundles the notifications that wait for a daily summary into one
  email per account, once a day after ``EMAIL_DIGEST_HOUR``.

Emails are claimed for a while before they are sent, so that a second worker does not
send them too; if the worker stops while sending, they are sent again afterwards.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import ngettext

from .models import OutgoingEmail
from .queue import to_message

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
RETRY_DELAYS = [timedelta(minutes=minutes) for minutes in (1, 5, 15, 60, 240)]
# How long a worker may take to send a batch before the emails are free again.
CLAIM_DURATION = timedelta(minutes=15)


# --- Sending ---------------------------------------------------------------------------


def _claim(now):
    """Reserves up to BATCH_SIZE due emails for this worker."""
    with transaction.atomic():
        ids = list(
            OutgoingEmail.objects.select_for_update(skip_locked=True)
            .filter(status=OutgoingEmail.Status.QUEUED, digest=False, next_attempt_at__lte=now)
            .order_by("next_attempt_at", "pk")
            .values_list("pk", flat=True)[:BATCH_SIZE]
        )
        OutgoingEmail.objects.filter(pk__in=ids).update(next_attempt_at=now + CLAIM_DURATION)
    return list(OutgoingEmail.objects.filter(pk__in=ids).prefetch_related("attachments"))


def _failed(email, error, now):
    email.attempts += 1
    email.last_error = f"{error.__class__.__name__}: {error}"[:2000]
    if email.attempts > len(RETRY_DELAYS):
        email.status = OutgoingEmail.Status.FAILED
        logger.error("Giving up on email %s to %s: %s", email.pk, email.to, email.last_error)
    else:
        email.next_attempt_at = now + RETRY_DELAYS[email.attempts - 1]
        logger.warning("Could not send email %s, trying again: %s", email.pk, email.last_error)
    email.save(update_fields=["attempts", "last_error", "status", "next_attempt_at"])


def send_due(now=None):
    """Sends a batch of due emails; returns how many were due."""
    now = now or timezone.now()
    emails = _claim(now)
    if not emails:
        return 0
    connection = mail.mailers.default
    try:
        connection.open()
    except Exception as error:
        for email in emails:
            _failed(email, error, now)
        return len(emails)
    try:
        for email in emails:
            try:
                connection.send_messages([to_message(email)])
            except Exception as error:
                # Whatever goes wrong with one email must not hold up the others.
                _failed(email, error, now)
            else:
                email.attempts += 1
                email.status = OutgoingEmail.Status.SENT
                email.sent_at = timezone.now()
                email.last_error = ""
                email.save(update_fields=["attempts", "status", "sent_at", "last_error"])
    finally:
        connection.close()
    return len(emails)


# --- Daily summary -----------------------------------------------------------------------


def digest_time(now):
    """The latest summary time (EMAIL_DIGEST_HOUR local time) up to ``now``."""
    local = timezone.localtime(now)
    today = local.replace(hour=settings.EMAIL_DIGEST_HOUR, minute=0, second=0, microsecond=0)
    return today if local >= today else today - timedelta(days=1)


def _summary(recipient, items):
    context = {
        "recipient": recipient,
        "items": [
            {
                "title": item.subject.removeprefix(settings.EMAIL_SUBJECT_PREFIX),
                # Without greeting and footer, see duties/email/_greeting.txt.
                "body": item.body.strip(),
            }
            for item in items
        ],
        "SITE_NAME": settings.SITE_NAME,
        "profile_url": settings.SITE_URL + reverse("profile") if settings.SITE_URL else "",
    }
    with translation.override(recipient.language or settings.LANGUAGE_CODE):
        subject = ngettext(
            "Summary: %(count)d notification", "Summary: %(count)d notifications", len(items)
        ) % {"count": len(items)}
        body = render_to_string("outbox/email/digest_message.txt", context)
    return OutgoingEmail.objects.create(
        recipient=recipient,
        to=[recipient.email],
        subject=settings.EMAIL_SUBJECT_PREFIX + subject,
        body=body,
    )


def send_digests(now=None):
    """Bundles the notifications waiting up to the latest summary time; returns the
    number of summaries. The summaries are sent by send_due like any other email."""
    now = now or timezone.now()
    cutoff = digest_time(now)
    waiting = OutgoingEmail.objects.filter(
        status=OutgoingEmail.Status.QUEUED, digest=True, created_at__lt=cutoff
    )
    count = 0
    for recipient_id in set(waiting.values_list("recipient_id", flat=True)):
        with transaction.atomic():
            items = list(
                waiting.filter(recipient_id=recipient_id)
                .select_for_update(skip_locked=True)
                .order_by("created_at", "pk")
            )
            if not items:
                continue
            recipient = items[0].recipient
            ids = [item.pk for item in items]
            if recipient is None or not recipient.is_active:
                OutgoingEmail.objects.filter(pk__in=ids).update(
                    status=OutgoingEmail.Status.FAILED,
                    last_error="The account has been deactivated or deleted.",
                )
                continue
            summary = _summary(recipient, items)
            OutgoingEmail.objects.filter(pk__in=ids).update(
                status=OutgoingEmail.Status.DIGESTED, included_in=summary, sent_at=now
            )
            count += 1
    return count
