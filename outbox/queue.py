"""Sending emails now or in the background.

With ``EMAIL_QUEUE`` switched on (Docker Compose), emails are stored in the database
and the worker (``manage.py run_worker``) sends them: the page does not wait for the
mail server, an email that cannot be sent is tried again later, and notifications for
accounts that want a daily summary wait for it. Without ``EMAIL_QUEUE`` (e.g. locally
with runserver), emails are sent right away and sending errors reach the caller.
"""

from dataclasses import dataclass

from django.conf import settings
from django.core import mail
from django.db import transaction

from accounts.models import User

from .models import OutgoingAttachment, OutgoingEmail


@dataclass
class Email:
    message: mail.EmailMessage
    # The account the email is for, if any.
    recipient: User | None = None
    # Waits for the recipient's daily summary (see wants_digest).
    digest: bool = False


def wants_digest(recipient):
    """Whether notifications that can wait go into the recipient's daily summary."""
    return settings.EMAIL_QUEUE and recipient.email_delivery == User.EmailDelivery.DAILY


def send(emails):
    """Sends the emails, or stores them for the worker if ``EMAIL_QUEUE`` is on.

    Raises SMTPException or OSError if sending right away fails.
    """
    if not settings.EMAIL_QUEUE:
        mail.mailers.default.send_messages([email.message for email in emails])
        return
    with transaction.atomic():
        for email in emails:
            _store(email)


def _store(email):
    message = email.message
    outgoing = OutgoingEmail.objects.create(
        recipient=email.recipient,
        to=list(message.to),
        from_email=message.from_email or "",
        reply_to=list(message.reply_to),
        subject=message.subject,
        body=message.body,
        digest=email.digest,
    )
    for filename, content, mimetype in message.attachments:
        OutgoingAttachment.objects.create(
            email=outgoing,
            filename=filename,
            content=content.encode() if isinstance(content, str) else content,
            mimetype=mimetype,
        )
    return outgoing


def to_message(outgoing):
    """The stored email as an EmailMessage, ready to send."""
    return mail.EmailMessage(
        subject=outgoing.subject,
        body=outgoing.body,
        from_email=outgoing.from_email or None,
        to=outgoing.to,
        reply_to=outgoing.reply_to,
        attachments=[
            (attachment.filename, bytes(attachment.content), attachment.mimetype)
            for attachment in outgoing.attachments.all()
        ],
    )
