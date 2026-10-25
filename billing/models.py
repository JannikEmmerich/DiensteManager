from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Unit
from duties.models import Duty, Report

from .validators import format_iban, validate_bic, validate_iban


class BankAccount(models.Model):
    """Bank details of a unit, to which the duty pay is transferred."""

    unit = models.OneToOneField(
        Unit, verbose_name=_("unit"), on_delete=models.CASCADE, related_name="bank_account"
    )
    account_holder = models.CharField(_("account holder"), max_length=150)
    iban = models.CharField(_("IBAN"), max_length=34, validators=[validate_iban])
    bic = models.CharField(_("BIC"), max_length=11, blank=True, validators=[validate_bic])
    bank_name = models.CharField(_("bank"), max_length=150, blank=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("updated by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = _("bank account")
        verbose_name_plural = _("bank accounts")

    def __str__(self):
        # Without the IBAN: the text ends up in the change history.
        return f"{self.unit}: {self.account_holder}"

    @property
    def iban_display(self):
        return format_iban(self.iban)


class BillingQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status=Billing.Status.CANCELLED)


class Billing(models.Model):
    """A statement of the duty pay that a unit gets for its confirmed helpers.

    The items and the bank details are copies taken when the statement is created, so
    that later changes (e.g. of a duty's rate) do not change an issued statement.
    """

    class Status(models.TextChoices):
        ISSUED = "issued", _("Open")
        PAID = "paid", _("Paid")
        CANCELLED = "cancelled", _("Cancelled")

    unit = models.ForeignKey(
        Unit, verbose_name=_("unit"), on_delete=models.PROTECT, related_name="billings"
    )
    year = models.PositiveSmallIntegerField(_("year"), editable=False)
    sequence = models.PositiveIntegerField(_("sequence number"), editable=False)
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.ISSUED
    )
    note = models.TextField(_("note"), blank=True, help_text=_("Printed on the statement."))
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2, default=0)
    account_holder = models.CharField(_("account holder"), max_length=150, blank=True)
    iban = models.CharField(_("IBAN"), max_length=34, blank=True)
    bic = models.CharField(_("BIC"), max_length=11, blank=True)
    bank_name = models.CharField(_("bank"), max_length=150, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("created by"),
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    created_at = models.DateTimeField(_("created at"), default=timezone.now, editable=False)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)

    objects = BillingQuerySet.as_manager()

    class Meta:
        verbose_name = _("billing")
        verbose_name_plural = _("billings")
        ordering = ["-year", "-sequence"]
        constraints = [
            models.UniqueConstraint(fields=["year", "sequence"], name="billing_unique_number"),
        ]

    def __str__(self):
        return self.number

    def get_absolute_url(self):
        return reverse("billing_detail", args=[self.pk])

    @property
    def number(self):
        return f"{self.year}-{self.sequence:03d}"

    @property
    def iban_display(self):
        return format_iban(self.iban)

    @property
    def period(self):
        """Date of the first and of the last duty."""
        dates = [item.duty_date for item in self.items.all()]
        return (min(dates), max(dates)) if dates else (None, None)


class BillingItem(models.Model):
    """One helper at one duty, as billed."""

    billing = models.ForeignKey(
        Billing, verbose_name=_("billing"), on_delete=models.CASCADE, related_name="items"
    )
    # Links to the source; "billed" means: in a billing that is not cancelled.
    report = models.ForeignKey(
        Report,
        verbose_name=_("report"),
        on_delete=models.SET_NULL,
        null=True,
        related_name="billing_items",
    )
    duty = models.ForeignKey(
        Duty, verbose_name=_("duty"), on_delete=models.SET_NULL, null=True, related_name="+"
    )
    duty_title = models.CharField(_("duty"), max_length=200)
    duty_date = models.DateField(_("date"))
    helper_name = models.CharField(_("helper"), max_length=150)
    # Of the position the helper was reported for (abbreviation if there is one).
    qualification = models.CharField(_("qualification"), max_length=100, blank=True)
    pay_type = models.CharField(_("duty pay"), max_length=10, choices=Duty.PayType.choices)
    rate = models.DecimalField(_("rate in euros"), max_digits=8, decimal_places=2)
    hours = models.DecimalField(_("hours"), max_digits=5, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(_("amount"), max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = _("billing item")
        verbose_name_plural = _("billing items")
        ordering = ["duty_date", "duty_title", "helper_name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(pay_type="hourly", hours__gt=0)
                | Q(pay_type="flat", hours__isnull=True),
                name="billing_item_hours_match_pay_type",
            ),
        ]

    def __str__(self):
        return f"{self.helper_name}, {self.duty_title}"

    @property
    def helper_display(self):
        return (
            f"{self.helper_name} ({self.qualification})" if self.qualification else self.helper_name
        )
