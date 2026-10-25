from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class HistoryEntry(models.Model):
    """One change of a duty, report, billing or bank account: who did what and when.

    ``scope`` groups the entries that are shown together, e.g. ``duty:12`` for a duty
    and its reports. ``changes`` lists the changed fields with the old and the new value.
    """

    class Action(models.TextChoices):
        CREATED = "created", _("created")
        CHANGED = "changed", _("changed")
        DELETED = "deleted", _("deleted")
        RELEASED = "released", _("released")
        CANCELLED = "cancelled", _("cancelled")
        REPORTED = "reported", _("reported")
        ADDED = "added", _("entered by the dispatchers")
        CONFIRMED = "confirmed", _("confirmed")
        DECLINED = "declined", _("declined")
        WITHDRAWN = "withdrawn", _("withdrawn")
        PAID = "paid", _("marked as paid")
        VOIDED = "voided", _("voided")  # a billing was cancelled

    scope = models.CharField(_("scope"), max_length=50, db_index=True)
    content_type = models.ForeignKey(
        ContentType, verbose_name=_("type"), on_delete=models.CASCADE, related_name="+"
    )
    object_id = models.PositiveBigIntegerField(_("object ID"))
    object_repr = models.CharField(_("object"), max_length=200)
    action = models.CharField(_("action"), max_length=20, choices=Action.choices)
    changes = models.JSONField(_("changes"), default=list, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("user"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Kept when the account is deleted later.
    user_repr = models.CharField(_("by"), max_length=200, blank=True)
    created_at = models.DateTimeField(_("time"), default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _("history entry")
        verbose_name_plural = _("history entries")
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.object_repr}: {self.get_action_display()}"

    @property
    def model(self):
        return self.content_type.model_class()
