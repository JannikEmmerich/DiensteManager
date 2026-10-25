from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class OutgoingEmail(models.Model):
    """An email that the worker (``manage.py run_worker``) sends in the background.

    Emails are stored ready to send. If sending fails, the worker tries again later.
    Emails for accounts that want a daily summary (``digest``) wait for it.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENT = "sent", _("Sent")
        DIGESTED = "digested", _("Sent in a summary")
        FAILED = "failed", _("Failed")

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("recipient"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    to = models.JSONField(_("to"), default=list)
    from_email = models.CharField(_("from"), max_length=320, blank=True)
    reply_to = models.JSONField(_("reply to"), default=list, blank=True)
    subject = models.TextField(_("subject"))
    body = models.TextField(_("message"))
    digest = models.BooleanField(_("for the daily summary"), default=False)
    included_in = models.ForeignKey(
        "self",
        verbose_name=_("summary"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="items",
    )
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.QUEUED
    )
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    last_error = models.TextField(_("last error"), blank=True)
    created_at = models.DateTimeField(_("created at"), default=timezone.now)
    next_attempt_at = models.DateTimeField(_("next attempt"), default=timezone.now)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("outgoing email")
        verbose_name_plural = _("outgoing emails")
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{', '.join(self.to)}: {self.subject}"


class OutgoingAttachment(models.Model):
    email = models.ForeignKey(
        OutgoingEmail,
        verbose_name=_("email"),
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    filename = models.CharField(_("file name"), max_length=255)
    content = models.BinaryField(_("content"))
    mimetype = models.CharField(_("type"), max_length=100)

    class Meta:
        verbose_name = _("attachment")
        verbose_name_plural = _("attachments")

    def __str__(self):
        return self.filename
