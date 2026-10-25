"""The email backend of demo mode: emails are stored for the inbox instead of sent."""

from email.utils import getaddresses

from django.core.mail.backends.base import BaseEmailBackend
from django.db import transaction

from .models import DemoAttachment, DemoEmail


def addresses(values):
    """The bare addresses in lower case, e.g. from "Anna <Anna@Example.org>"."""
    return [address.lower() for _name, address in getaddresses(values) if address]


class DemoEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        with transaction.atomic():
            for message in email_messages:
                self._store(message)
        return len(email_messages)

    def _store(self, message):
        recipients = addresses([*message.to, *message.cc, *message.bcc])
        email = DemoEmail.objects.create(
            from_email=message.from_email or "",
            to=list(message.to),
            cc=list(message.cc),
            bcc=list(message.bcc),
            reply_to=list(message.reply_to),
            recipients="".join(f",{address}" for address in recipients) + ",",
            subject=message.subject,
            body=message.body,
        )
        for attachment in message.attachments:
            if isinstance(attachment, tuple):
                filename, content, mimetype = attachment
            else:  # a MIME part
                filename = attachment.get_filename()
                content = attachment.get_payload(decode=True)
                mimetype = attachment.get_content_type()
            DemoAttachment.objects.create(
                email=email,
                filename=filename or "attachment",
                content=content.encode() if isinstance(content, str) else content,
                mimetype=mimetype or "application/octet-stream",
            )
