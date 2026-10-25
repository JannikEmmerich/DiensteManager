import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language_info
from django.utils.translation import gettext_lazy as _

from core.names import dispatch_text

from .managers import UserManager

# Each language is listed in its own name ("Deutsch", "English"), whatever the UI language.
LANGUAGE_CHOICES = [
    (code, get_language_info(code)["name_local"]) for code, _label in settings.LANGUAGES
]


class Role(models.TextChoices):
    DISPATCHER = "dispatcher", dispatch_text(_("%(DISPATCH_NAME)s (internal)"))
    UNIT_LEAD = "unit_lead", _("Unit lead")
    MEMBER = "member", _("Unit member")


def role_choices():
    # A callable, so that migrations do not store the name of the dispatchers.
    return Role.choices


# Internal accounts belong to no unit, external accounts to exactly one.
ROLE_MATCHES_UNIT = Q(role=Role.DISPATCHER, unit__isnull=True) | Q(
    role__in=[Role.UNIT_LEAD, Role.MEMBER], unit__isnull=False
)


class Unit(models.Model):
    """An external unit that can report staff for our duties."""

    name = models.CharField(_("name"), max_length=150, unique=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("unit")
        verbose_name_plural = _("units")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("unit_detail", args=[self.pk])


class User(AbstractUser):
    class EmailDelivery(models.TextChoices):
        IMMEDIATELY = "immediately", _("Immediately")
        DAILY = "daily", _("Once a day as a summary")

    username = None
    email = models.EmailField(_("email address"), unique=True)
    role = models.CharField(_("role"), max_length=20, choices=role_choices)
    unit = models.ForeignKey(
        Unit,
        verbose_name=_("unit"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="members",
    )
    language = models.CharField(
        _("language"),
        max_length=10,
        choices=LANGUAGE_CHOICES,
        blank=True,
        help_text=_("Leave empty to follow the browser setting."),
    )
    # Email notifications, see duties/notifications.py.
    notify_new_duties = models.BooleanField(_("new duties"), default=True)
    notify_reports = models.BooleanField(_("reports"), default=True)
    notify_billing = models.BooleanField(_("billings"), default=True)
    # Only with the email queue (outbox app); urgent notifications are always sent right away.
    email_delivery = models.CharField(
        _("delivery"),
        max_length=20,
        choices=EmailDelivery.choices,
        default=EmailDelivery.IMMEDIATELY,
    )

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["last_name", "first_name", "email"]
        constraints = [
            models.CheckConstraint(condition=ROLE_MATCHES_UNIT, name="user_role_matches_unit"),
        ]

    def __str__(self):
        return self.get_full_name() or self.email

    def save(self, *args, **kwargs):
        self.email = self.__class__.objects.normalize_email(self.email)
        super().save(*args, **kwargs)


class CalendarFeed(models.Model):
    """The personal calendar address (iCal) of an account, see duties/calendar.py.

    Calendar apps cannot sign in, so the random token in the address is all that protects
    the feed. It only shows title, time and place of duties, and stops working when the
    account is blocked; a new address replaces the old one.
    """

    user = models.OneToOneField(
        User, verbose_name=_("account"), on_delete=models.CASCADE, related_name="calendar_feed"
    )
    token = models.CharField(_("token"), max_length=64, unique=True)
    only_own_reports = models.BooleanField(
        _("only duties with reports of my unit"),
        default=False,
        help_text=_("Otherwise all duties offered to your unit."),
    )
    include_drafts = models.BooleanField(
        _("drafts too"), default=False, help_text=_("Otherwise only released duties.")
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("calendar subscription")
        verbose_name_plural = _("calendar subscriptions")

    def __str__(self):
        return str(self.user)

    def new_token(self):
        self.token = secrets.token_urlsafe(32)


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class InvitationQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )

    def with_token(self, token):
        return self.filter(token_hash=_hash_token(token))


class Invitation(models.Model):
    """Invitation by email link; accepting it creates an account.

    Only a hash of the token is stored; the link itself only exists in the email.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        EXPIRED = "expired", _("Expired")
        REVOKED = "revoked", _("Revoked")

    email = models.EmailField(_("email address"))
    role = models.CharField(_("role"), max_length=20, choices=role_choices)
    unit = models.ForeignKey(
        Unit,
        verbose_name=_("unit"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="invitations",
    )
    language = models.CharField(
        _("language"),
        max_length=10,
        choices=LANGUAGE_CHOICES,
        blank=True,
        help_text=_("Language of the invitation email and of the new account."),
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("invited by"),
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    expires_at = models.DateTimeField(_("expires at"))
    accepted_at = models.DateTimeField(_("accepted at"), null=True, blank=True)
    account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("created account"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitation",
    )
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)

    objects = InvitationQuerySet.as_manager()

    class Meta:
        verbose_name = _("invitation")
        verbose_name_plural = _("invitations")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=ROLE_MATCHES_UNIT, name="invitation_role_matches_unit"
            ),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = User.objects.normalize_email(self.email)
        super().save(*args, **kwargs)

    def new_token(self):
        """Creates a new link token and extends the validity.

        Older links stop working. Does not save; returns the plain-text token.
        """
        token = secrets.token_urlsafe(32)
        self.token_hash = _hash_token(token)
        self.expires_at = timezone.now() + timedelta(days=settings.INVITATION_VALIDITY_DAYS)
        return token

    @property
    def status(self):
        if self.accepted_at:
            return self.Status.ACCEPTED
        if self.revoked_at:
            return self.Status.REVOKED
        if self.expires_at <= timezone.now():
            return self.Status.EXPIRED
        return self.Status.PENDING

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    @property
    def can_be_resent(self):
        return self.status in (self.Status.PENDING, self.Status.EXPIRED)
