"""Data lifecycle: what is anonymised or deleted after which time.

- Reports (helper name, qualification, note) of duties that ended more than
  ``RETENTION_REPORTS_DAYS`` ago are anonymised; the counts stay for the statistics.
  Helpers that can still be billed are kept until they are billed (the billing keeps
  its own copy of the name).
- History entries older than ``RETENTION_HISTORY_DAYS`` are deleted, those of
  anonymised reports right away.
- Emails from dispatchers older than ``RETENTION_MAILINGS_DAYS`` are deleted.
- Accepted, revoked and expired invitations are deleted ``RETENTION_INVITATIONS_DAYS``
  after that happened.
- Paid and cancelled billings are deleted after ``RETENTION_BILLINGS_YEARS`` full
  calendar years (retention periods for accounting records); open ones never.
- Sent and failed emails of the email queue are deleted ``RETENTION_OUTBOX_DAYS`` after
  they were written; queued ones never.

A period of 0 keeps the data forever.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Invitation
from billing.models import Billing
from duties.models import Duty, Report
from mailings.models import Mailing
from outbox.models import OutgoingEmail

from . import history
from .models import HistoryEntry


def _billable():
    """Reports that can still be billed (same rule as billing.services.billable_reports)."""
    return (
        Q(status=Report.Status.CONFIRMED, duty__status=Duty.Status.RELEASED)
        & ~Q(duty__pay_type=Duty.PayType.NONE)
        & ~Q(billing_items__billing__status__in=[Billing.Status.ISSUED, Billing.Status.PAID])
    )


def anonymise_reports(now):
    days = settings.RETENTION_REPORTS_DAYS
    if not days:
        return 0
    reports = Report.objects.filter(
        anonymized_at__isnull=True, duty__ends_at__lt=now - timedelta(days=days)
    ).exclude(pk__in=Report.objects.filter(_billable()).values("pk"))
    ids = list(reports.values_list("pk", flat=True))
    if ids:
        HistoryEntry.objects.filter(
            content_type=ContentType.objects.get_for_model(Report), object_id__in=ids
        ).delete()
        Report.objects.filter(pk__in=ids).update(
            name="", qualification="", note="", anonymized_at=now
        )
    return len(ids)


def delete_history(now):
    days = settings.RETENTION_HISTORY_DAYS
    if not days:
        return 0
    return HistoryEntry.objects.filter(created_at__lt=now - timedelta(days=days)).delete()[0]


def delete_mailings(now):
    days = settings.RETENTION_MAILINGS_DAYS
    if not days:
        return 0
    old = Mailing.objects.filter(sent_at__lt=now - timedelta(days=days))
    count = old.count()
    old.delete()
    return count


def delete_invitations(now):
    days = settings.RETENTION_INVITATIONS_DAYS
    if not days:
        return 0
    cutoff = now - timedelta(days=days)
    old = Invitation.objects.filter(
        Q(accepted_at__lt=cutoff)
        | Q(revoked_at__lt=cutoff)
        | Q(accepted_at__isnull=True, revoked_at__isnull=True, expires_at__lt=cutoff)
    )
    count = old.count()
    old.delete()
    return count


def delete_billings(now):
    years = settings.RETENTION_BILLINGS_YEARS
    if not years:
        return 0
    old = Billing.objects.filter(
        status__in=[Billing.Status.PAID, Billing.Status.CANCELLED],
        year__lt=timezone.localdate(now).year - years,
    )
    ids = list(old.values_list("pk", flat=True))
    HistoryEntry.objects.filter(scope__in=[history.scope_for_billing(pk) for pk in ids]).delete()
    Billing.objects.filter(pk__in=ids).delete()
    return len(ids)


def delete_outgoing_emails(now):
    days = settings.RETENTION_OUTBOX_DAYS
    if not days:
        return 0
    _total, per_model = (
        OutgoingEmail.objects.exclude(status=OutgoingEmail.Status.QUEUED)
        .filter(created_at__lt=now - timedelta(days=days))
        .delete()
    )
    return per_model.get(OutgoingEmail._meta.label, 0)


def apply(now=None, dry_run=False):
    """Applies all retention periods; returns what was done (or would be, for a dry run)."""
    now = now or timezone.now()
    with transaction.atomic():
        result = {
            "reports_anonymised": anonymise_reports(now),
            "history_deleted": delete_history(now),
            "mailings_deleted": delete_mailings(now),
            "invitations_deleted": delete_invitations(now),
            "billings_deleted": delete_billings(now),
            "outgoing_emails_deleted": delete_outgoing_emails(now),
        }
        if dry_run:
            transaction.set_rollback(True)
    return result
