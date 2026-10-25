from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, F, Q
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Unit

CENT = Decimal("0.01")


class DutyQuerySet(models.QuerySet):
    def upcoming(self):
        return self.filter(ends_at__gte=timezone.now())

    def past(self):
        return self.filter(ends_at__lt=timezone.now())

    def offered_to(self, unit_id):
        """Released or cancelled duties that are offered to the unit; never drafts."""
        selected = Duty.units.through.objects.filter(unit_id=unit_id).values("duty_id")
        return self.exclude(status=Duty.Status.DRAFT).filter(Q(all_units=True) | Q(pk__in=selected))

    def with_staffing(self, unit_id=None):
        """Adds the number of confirmed and pending reports.

        With ``unit_id``, additionally the number of active reports of that unit.
        """
        queryset = self.annotate(
            confirmed_count=Count("reports", filter=Q(reports__status=Report.Status.CONFIRMED)),
            pending_count=Count("reports", filter=Q(reports__status=Report.Status.REPORTED)),
        )
        if unit_id is not None:
            queryset = queryset.annotate(
                unit_report_count=Count(
                    "reports",
                    filter=Q(reports__unit_id=unit_id, reports__status__in=Report.ACTIVE),
                )
            )
        return queryset


class Duty(models.Model):
    """A duty for which external units report helpers.

    Entered manually by dispatchers (later also imported from HiOrg-Server). Units only
    see a duty after it has been released, and only if it is offered to them.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        RELEASED = "released", _("Released")
        CANCELLED = "cancelled", _("Cancelled")

    class Source(models.TextChoices):
        MANUAL = "manual", _("Entered manually")
        HIORG = "hiorg", _("HiOrg-Server")

    class PayType(models.TextChoices):
        NONE = "none", _("No duty pay")
        HOURLY = "hourly", _("Per helper hour")
        FLAT = "flat", _("Flat rate per helper")

    title = models.CharField(_("title"), max_length=200)
    location = models.CharField(_("location"), max_length=200, blank=True)
    starts_at = models.DateTimeField(_("start"))
    ends_at = models.DateTimeField(_("end"))
    helpers_needed = models.PositiveSmallIntegerField(
        _("helpers needed"), validators=[MinValueValidator(1)]
    )
    description = models.TextField(
        _("description"),
        blank=True,
        help_text=_("Visible to the units, e.g. meeting point, qualifications, equipment."),
    )
    # Duty pay ("Dienstgeld") that the units get for each confirmed helper.
    pay_type = models.CharField(
        _("duty pay"), max_length=10, choices=PayType.choices, default=PayType.NONE
    )
    pay_rate = models.DecimalField(
        _("rate in euros"),
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text=_("Per helper hour, or per helper for the whole duty."),
    )
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    all_units = models.BooleanField(_("offered to all units"), default=True)
    units = models.ManyToManyField(Unit, verbose_name=_("units"), blank=True, related_name="duties")
    # For the import from HiOrg-Server: where the duty comes from and its ID there.
    source = models.CharField(
        _("source"), max_length=20, choices=Source.choices, default=Source.MANUAL, editable=False
    )
    external_id = models.CharField(_("external ID"), max_length=100, blank=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("created by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    released_at = models.DateTimeField(_("released at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)

    objects = DutyQuerySet.as_manager()

    class Meta:
        verbose_name = _("duty")
        verbose_name_plural = _("duties")
        ordering = ["starts_at", "title"]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="duty_ends_after_start"
            ),
            models.CheckConstraint(condition=Q(helpers_needed__gte=1), name="duty_needs_helpers"),
            models.CheckConstraint(
                condition=Q(pay_type="none", pay_rate__isnull=True)
                | (~Q(pay_type="none") & Q(pay_rate__gt=0)),
                name="duty_pay_rate_matches_type",
            ),
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=~Q(external_id=""),
                name="duty_unique_external_id",
            ),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("duty_detail", args=[self.pk])

    @property
    def has_started(self):
        return self.starts_at <= timezone.now()

    @property
    def is_past(self):
        return self.ends_at < timezone.now()

    @property
    def is_open_for_reports(self):
        return self.status == self.Status.RELEASED and not self.has_started

    @property
    def duration_hours(self):
        """Length of the duty in hours, to two decimals."""
        hours = Decimal((self.ends_at - self.starts_at).total_seconds()) / 3600
        return hours.quantize(CENT, ROUND_HALF_UP)

    @property
    def has_pay(self):
        return self.pay_type != self.PayType.NONE

    def pay_for(self, hours=None):
        """Duty pay for one helper; hourly pay for ``hours`` (default: the whole duty)."""
        if self.pay_type == self.PayType.HOURLY:
            hours = self.duration_hours if hours is None else hours
            return (self.pay_rate * hours).quantize(CENT, ROUND_HALF_UP)
        if self.pay_type == self.PayType.FLAT:
            return self.pay_rate
        return Decimal(0)

    def offered_unit_ids(self):
        if self.all_units:
            return set(Unit.objects.values_list("pk", flat=True))
        return set(self.units.values_list("pk", flat=True))

    def is_offered_to(self, unit_id):
        if self.status == self.Status.DRAFT or unit_id is None:
            return False
        return self.all_units or self.units.filter(pk=unit_id).exists()


class Report(models.Model):
    """A helper whom a unit reports for a duty; the dispatchers confirm or decline."""

    class Status(models.TextChoices):
        REPORTED = "reported", _("Reported")
        CONFIRMED = "confirmed", _("Confirmed")
        DECLINED = "declined", _("Declined")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    # Reports that still count: the helper is expected (or may be) at the duty.
    ACTIVE = (Status.REPORTED, Status.CONFIRMED)

    duty = models.ForeignKey(
        Duty, verbose_name=_("duty"), on_delete=models.CASCADE, related_name="reports"
    )
    unit = models.ForeignKey(
        Unit, verbose_name=_("unit"), on_delete=models.CASCADE, related_name="reports"
    )
    name = models.CharField(_("name"), max_length=150)
    qualification = models.CharField(_("qualification"), max_length=100, blank=True)
    note = models.CharField(_("note"), max_length=500, blank=True)
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.REPORTED
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("reported by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(_("reported at"), auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("decided by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    withdrawn_at = models.DateTimeField(_("withdrawn at"), null=True, blank=True)

    class Meta:
        verbose_name = _("report")
        verbose_name_plural = _("reports")
        ordering = ["unit__name", "created_at"]
        constraints = [
            # A unit reports a person only once per duty; after withdrawing, it may
            # report them again.
            models.UniqueConstraint(
                Lower("name"),
                "duty",
                "unit",
                condition=~Q(status="withdrawn"),
                name="report_unique_name_per_duty_and_unit",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status in self.ACTIVE

    @property
    def can_be_withdrawn(self):
        return self.is_active and self.duty.is_open_for_reports

    @property
    def can_be_decided(self):
        return self.status != self.Status.WITHDRAWN and self.duty.status == Duty.Status.RELEASED
