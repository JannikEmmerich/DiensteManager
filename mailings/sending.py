"""Sending an email of a dispatcher to the accounts of the units.

Every recipient gets their own email (nobody sees the other addresses). It comes from
the portal's address with the dispatcher's name, and Reply-To is the dispatcher's
address. The footer is in the language of the recipient's account.
"""

from email.utils import formataddr, parseaddr

from django.conf import settings
from django.core import mail
from django.template.loader import render_to_string
from django.utils import translation

from outbox import queue


def _one_line(text):
    return " ".join(str(text).split())


def _from_email(sender):
    """E.g. "Dana Dispatcher via DiensteManager <noreply@example.org>"."""
    _name, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    return formataddr((_one_line(f"{sender} via {settings.SITE_NAME}"), address))


def _reply_to(sender):
    return formataddr((_one_line(sender.get_full_name()), sender.email))


def _message(request, mailing, recipient, recipient_count, is_copy=False):
    sender = mailing.sent_by
    context = {
        "mailing": mailing,
        "sender": sender,
        "recipient": recipient,
        "recipient_count": recipient_count,
        "is_copy": is_copy,
        "SITE_NAME": settings.SITE_NAME,
    }
    if mailing.duty:
        context["duty_url"] = request.build_absolute_uri(mailing.duty.get_absolute_url())
    with translation.override(recipient.language or settings.LANGUAGE_CODE):
        body = render_to_string("mailings/email/mailing_message.txt", context)
    message = mail.EmailMessage(
        subject=settings.EMAIL_SUBJECT_PREFIX + _one_line(mailing.subject),
        body=body,
        from_email=_from_email(sender),
        to=[recipient.email],
        reply_to=[_reply_to(sender)],
    )
    return queue.Email(message, recipient=recipient)


def send(request, mailing, recipients, copy_to_sender=False):
    """Sends the mailing, or queues it (``EMAIL_QUEUE``; never in a daily summary).

    Raises SMTPException or OSError if sending right away fails.
    """
    emails = [_message(request, mailing, recipient, len(recipients)) for recipient in recipients]
    if copy_to_sender:
        emails.append(_message(request, mailing, mailing.sent_by, len(recipients), is_copy=True))
    queue.send(emails)
