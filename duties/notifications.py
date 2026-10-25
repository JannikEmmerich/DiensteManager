"""Email notifications about duties and reports.

Every recipient gets their own email in the language of their account (accounts without
a language get the default language). Who receives what:

- duty released, or offered to additional units: the active accounts of those units
  with "new duties" switched on;
- helpers reported or withdrawn: the dispatchers with "reports" switched on;
- reports confirmed or declined, duty changed or cancelled: the unit leads of the unit
  concerned and whoever reported the helpers, if they have "reports" switched on.

The person who triggered a notification never receives it. Sending errors are logged
and shown as a warning; they never undo the change itself.
"""

import logging
from collections import defaultdict
from smtplib import SMTPException

from django.conf import settings
from django.contrib import messages
from django.core import mail
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation
from django.utils.text import format_lazy
from django.utils.translation import gettext as _

from accounts.models import Role, User

from .models import Report

logger = logging.getLogger(__name__)

# Changes to a released duty that the units with reports are told about.
RELEVANT_CHANGES = ("title", "starts_at", "ends_at", "location")


# --- Recipients ----------------------------------------------------------------------


def _unit_accounts(unit_ids):
    """Accounts of the units that want to hear about new duties."""
    return User.objects.filter(
        is_active=True,
        notify_new_duties=True,
        role__in=[Role.UNIT_LEAD, Role.MEMBER],
        unit_id__in=unit_ids,
    )


def _dispatchers():
    return User.objects.filter(
        Q(role=Role.DISPATCHER) | Q(is_superuser=True), is_active=True, notify_reports=True
    )


def _unit_contacts(unit_id, reports):
    """Unit leads of the unit plus the accounts that made the reports."""
    reporter_ids = {report.reported_by_id for report in reports if report.reported_by_id}
    return User.objects.filter(
        Q(role=Role.UNIT_LEAD) | Q(pk__in=reporter_ids),
        unit_id=unit_id,
        is_active=True,
        notify_reports=True,
    )


def _active_reports_by_unit(duty):
    by_unit = defaultdict(list)
    for report in duty.reports.filter(status__in=Report.ACTIVE).select_related("unit"):
        by_unit[report.unit_id].append(report)
    return by_unit


# --- Rendering and sending -----------------------------------------------------------------


def _message(request, recipient, template, context):
    context = {
        **context,
        "recipient": recipient,
        "SITE_NAME": settings.SITE_NAME,
        "profile_url": request.build_absolute_uri(reverse("profile")),
    }
    if "duty" in context:
        context["duty_url"] = request.build_absolute_uri(context["duty"].get_absolute_url())
    with translation.override(recipient.language or settings.LANGUAGE_CODE):
        subject = render_to_string(f"duties/email/{template}_subject.txt", context)
        body = render_to_string(f"duties/email/{template}_message.txt", context)
    return mail.EmailMessage(
        subject=settings.EMAIL_SUBJECT_PREFIX + " ".join(subject.split()),
        body=body,
        to=[recipient.email],
    )


def _send(request, recipients, template, context):
    """Sends one email per recipient (except the person who triggered it)."""
    recipients = list(recipients.exclude(pk=request.user.pk))
    if not recipients:
        return
    emails = [_message(request, recipient, template, context) for recipient in recipients]
    try:
        mail.mailers.default.send_messages(emails)
    except (SMTPException, OSError):
        logger.exception("Could not send %s notifications (%s)", len(emails), template)
        if not getattr(request, "_notification_failed", False):
            request._notification_failed = True
            messages.warning(
                request,
                _("The change has been saved, but the notification emails could not be sent."),
            )


# --- Notifications -----------------------------------------------------------------------


def duty_released(request, duty, unit_ids=None):
    """A duty has been released, or offered to additional units (``unit_ids``)."""
    if unit_ids is None:
        unit_ids = duty.offered_unit_ids()
    _send(request, _unit_accounts(unit_ids), "duty_released", {"duty": duty})


def helpers_reported(request, duty, unit, reports):
    _send(
        request,
        _dispatchers(),
        "helpers_reported",
        {"duty": duty, "unit": unit, "reports": reports},
    )


def report_withdrawn(request, report):
    _send(
        request,
        _dispatchers(),
        "report_withdrawn",
        {"duty": report.duty, "unit": report.unit, "report": report},
    )


def reports_decided(request, duty, reports):
    """Reports have been confirmed or declined; one email per unit and recipient."""
    by_unit = defaultdict(list)
    for report in reports:
        by_unit[report.unit_id].append(report)
    for unit_id, unit_reports in by_unit.items():
        _send(
            request,
            _unit_contacts(unit_id, unit_reports),
            "reports_decided",
            {"duty": duty, "unit": unit_reports[0].unit, "reports": unit_reports},
        )


def duty_changed(request, duty, changed_fields):
    """Time, place or title of a released duty have changed."""
    # Stays lazy so that it is translated into each recipient's language.
    changes = format_lazy(
        ", ".join("{}" for _name in changed_fields),
        *(duty._meta.get_field(name).verbose_name for name in changed_fields),
    )
    for unit_id, reports in _active_reports_by_unit(duty).items():
        _send(
            request,
            _unit_contacts(unit_id, reports),
            "duty_changed",
            {"duty": duty, "changes": changes, "reports": reports},
        )


def duty_cancelled(request, duty):
    for unit_id, reports in _active_reports_by_unit(duty).items():
        _send(
            request,
            _unit_contacts(unit_id, reports),
            "duty_cancelled",
            {"duty": duty, "reports": reports},
        )
