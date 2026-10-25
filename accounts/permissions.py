"""Central permission checks: who may invite whom, view which unit, block whom?

- Admins (superusers): everything; the only ones who may invite or block dispatchers,
  and who may change all data of other accounts (name, email, role, unit, …).
- Dispatchers (internal): manage all units, invite and block unit leads and members,
  switch accounts of all units between unit lead and member.
- Unit leads: only their own unit; invite and block unit leads and members there and
  switch them between unit lead and member.
- Members: no management permissions.
- Nobody changes or blocks their own account here.

Accounts of the roles in MFA_REQUIRED_ROLES (default: dispatchers and admins) must use
two-factor authentication.

In demo mode (DEMO_MODE), only admins change email addresses and passwords and use
two-factor authentication. Nobody invites; admins add accounts directly instead.
"""

from django.conf import settings

from .models import Invitation, Role, Unit


def is_dispatcher(user):
    """Internal view of all units. Superusers count as dispatchers."""
    return user.is_authenticated and (user.is_superuser or user.role == Role.DISPATCHER)


def demo_restricted(user):
    """In demo mode everyone but admins: no changes to the sign-in, no invitations."""
    return settings.DEMO_MODE and not (user.is_authenticated and user.is_superuser)


def must_use_mfa(user):
    """Two-factor authentication is mandatory for the roles in MFA_REQUIRED_ROLES."""
    if not user.is_authenticated or demo_restricted(user):
        return False
    role = Role.DISPATCHER if is_dispatcher(user) else user.role
    return role in settings.MFA_REQUIRED_ROLES


def is_unit_lead(user):
    return user.is_authenticated and user.role == Role.UNIT_LEAD and user.unit_id is not None


def invitable_roles(user):
    if not user.is_authenticated or settings.DEMO_MODE:
        return []
    if user.is_superuser:
        return [Role.DISPATCHER, Role.UNIT_LEAD, Role.MEMBER]
    if is_dispatcher(user) or is_unit_lead(user):
        return [Role.UNIT_LEAD, Role.MEMBER]
    return []


def can_invite(user):
    return bool(invitable_roles(user))


def can_add_accounts(user):
    """In demo mode, admins add accounts with a password instead of inviting."""
    return settings.DEMO_MODE and user.is_authenticated and user.is_superuser


def invitable_units(user):
    if is_dispatcher(user):
        return Unit.objects.all()
    if is_unit_lead(user):
        return Unit.objects.filter(pk=user.unit_id)
    return Unit.objects.none()


def visible_invitations(user):
    if is_dispatcher(user):
        return Invitation.objects.all()
    if is_unit_lead(user):
        return Invitation.objects.filter(unit_id=user.unit_id)
    return Invitation.objects.none()


def can_manage_invitation(user, invitation):
    """Resend or revoke."""
    if settings.DEMO_MODE:
        return False
    if invitation.role == Role.DISPATCHER:
        return user.is_authenticated and user.is_superuser
    if is_dispatcher(user):
        return True
    return is_unit_lead(user) and invitation.unit_id == user.unit_id


def can_view_unit(user, unit):
    if is_dispatcher(user):
        return True
    return is_unit_lead(user) and unit.pk == user.unit_id


def can_block_account(user, target):
    """Block or unblock another account."""
    if not user.is_authenticated or target.pk == user.pk or target.is_superuser:
        return False
    if user.is_superuser:
        return True
    if target.role == Role.DISPATCHER:
        return False
    if is_dispatcher(user):
        return True
    return is_unit_lead(user) and target.unit_id == user.unit_id


def can_change_role(user, target):
    """Switch another account of a unit between unit lead and member."""
    if not user.is_authenticated or target.pk == user.pk or target.unit_id is None:
        return False
    if target.is_superuser:
        return user.is_superuser
    if is_dispatcher(user):
        return True
    return is_unit_lead(user) and target.unit_id == user.unit_id


def can_edit_account(user, target):
    """Change all data of another account, including the role "dispatcher" and the unit."""
    return user.is_authenticated and user.is_superuser and target.pk != user.pk
