"""Who may see and do what with duties and reports?

- Dispatchers (and admins): create, edit, release, cancel and delete duties; see all
  reports and confirm or decline them.
- Unit leads and members: see released duties offered to their unit, and only their own
  unit's reports. They report helpers until the duty starts. Members withdraw the
  reports they made, unit leads every report of their unit.
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


def can_withdraw_report(user, report):
    if is_dispatcher(user) or not is_unit_account(user) or report.unit_id != user.unit_id:
        return False
    if user.role != Role.UNIT_LEAD and report.reported_by_id != user.pk:
        return False
    return report.can_be_withdrawn


def can_decide_report(user, report):
    return is_dispatcher(user) and report.can_be_decided
