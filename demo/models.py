from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class DemoEmail(models.Model):
    """An email that demo mode stores instead of sending it (see demo.backends).

    Everyone sees the emails sent to their address under "Inbox", admins all of them.
    """

    from_email = models.CharField(_("from"), max_length=320, blank=True)
    to = models.JSONField(_("to"), default=list)
    cc = models.JSONField(_("cc"), default=list, blank=True)
    bcc = models.JSONField(_("bcc"), default=list, blank=True)
    reply_to = models.JSONField(_("reply to"), default=list, blank=True)
    # The addresses of all recipients in lower case, each between commas, so that the
    # emails of an address are found on every database (",anna@example.org,").
    recipients = models.TextField(_("recipients"))
    subject = models.TextField(_("subject"))
    body = models.TextField(_("message"))
    created_at = models.DateTimeField(_("sent at"), default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _("demo email")
        verbose_name_plural = _("demo emails")
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{', '.join(self.to)}: {self.subject}"


class DemoAttachment(models.Model):
    email = models.ForeignKey(
        DemoEmail, verbose_name=_("email"), on_delete=models.CASCADE, related_name="attachments"
    )
    filename = models.CharField(_("file name"), max_length=255)
    content = models.BinaryField(_("content"))
    mimetype = models.CharField(_("type"), max_length=100)

    class Meta:
        verbose_name = _("attachment")
        verbose_name_plural = _("attachments")

    def __str__(self):
        return self.filename
