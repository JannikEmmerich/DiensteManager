"""Who may see and do what with duties and reports?

- Dispatchers (and admins): create, edit, release, cancel and delete duties; see all
  reports and confirm or decline them.
- Unit leads and members: see released duties offered to their unit, and only their own
  unit's reports. They report helpers until the report deadline (or else the start).
  Members act on the reports they made, unit leads on every report of their unit: they
  withdraw reports that are not confirmed yet while reports are open, and otherwise ask
  the dispatchers to withdraw them, who take them back or keep them.
"""

from accounts.models import Role
from accounts.permissions import is_dispatcher

from .models import Duty, Report


def is_unit_account(user):
    return (
        user.is_authenticated
        and user.role in (Role.UNIT_LEAD, Role.MEMBER)
        and user.unit_id is not None
    )


def visible_duties(user):
    if is_dispatcher(user):
        return Duty.objects.all()
    if is_unit_account(user):
        return Duty.objects.offered_to(user.unit_id)
    return Duty.objects.none()


def visible_reports(user):
    if is_dispatcher(user):
        return Report.objects.all()
    if is_unit_account(user):
        return Report.objects.filter(unit_id=user.unit_id)
    return Report.objects.none()


def can_report(user, duty):
    return (
        not is_dispatcher(user)
        and is_unit_account(user)
        and duty.is_open_for_reports
        and duty.is_offered_to(user.unit_id)
    )


def _acts_for_report(user, report):
    """The unit account may act on the report: its unit leads, or the member who made it."""
    if is_dispatcher(user) or not is_unit_account(user) or report.unit_id != user.unit_id:
        return False
    return user.role == Role.UNIT_LEAD or report.reported_by_id == user.pk


def can_withdraw_report(user, report):
    return _acts_for_report(user, report) and report.can_be_withdrawn


def can_request_withdrawal(user, report):
    return _acts_for_report(user, report) and report.can_request_withdrawal


def can_decide_report(user, report):
    return is_dispatcher(user) and report.can_be_decided


def can_take_back_report(user, report):
    return is_dispatcher(user) and report.can_be_taken_back
