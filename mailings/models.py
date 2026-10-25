from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from accounts.models import Role, Unit, User
from duties.models import Duty


class Mailing(models.Model):
    """An email that a dispatcher sent to accounts of the units through the portal.

    It goes out from the portal's address with the dispatcher as Reply-To, so that
    answers reach the dispatcher directly.
    """

    class Audience(models.TextChoices):
        UNIT_LEADS = "unit_leads", _("Unit leads")
        ALL = "all", _("All accounts of the units (unit leads and members)")

    subject = models.CharField(_("subject"), max_length=200)
    body = models.TextField(_("message"))
    audience = models.CharField(
        _("recipients"), max_length=20, choices=Audience.choices, default=Audience.UNIT_LEADS
    )
    all_units = models.BooleanField(_("to all units"), default=True)
    units = models.ManyToManyField(
        Unit, verbose_name=_("units"), blank=True, related_name="mailings"
    )
    duty = models.ForeignKey(
        Duty,
        verbose_name=_("duty"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mailings",
        help_text=_("The email links to this duty."),
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("sent by"),
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    sent_at = models.DateTimeField(_("sent at"), auto_now_add=True)
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("recipients"),
        blank=True,
        related_name="received_mailings",
    )

    class Meta:
        verbose_name = _("email")
        verbose_name_plural = _("emails")
        ordering = ["-sent_at"]

    def __str__(self):
        return self.subject

    def get_absolute_url(self):
        return reverse("mailing_detail", args=[self.pk])


def unit_accounts(audience, units):
    """Active accounts of the units: only the unit leads, or everyone."""
    roles = Q(role=Role.UNIT_LEAD)
    if audience == Mailing.Audience.ALL:
        roles |= Q(role=Role.MEMBER)
    return User.objects.filter(roles, is_active=True, unit__in=units)
