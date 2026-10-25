"""Central permission checks: who may invite whom, view which unit, block whom?

- Admins (superusers): everything; the only ones who may invite or block dispatchers.
- Dispatchers (internal): manage all units, invite and block unit leads and members.
- Unit leads: only their own unit; invite and block unit leads and members there.
- Members: no management permissions.

Accounts of the roles in MFA_REQUIRED_ROLES (default: dispatchers and admins) must use
two-factor authentication.
"""

from django.conf import settings

from .models import Invitation, Role, Unit


def is_dispatcher(user):
    """Internal view of all units. Superusers count as dispatchers."""
    return user.is_authenticated and (user.is_superuser or user.role == Role.DISPATCHER)


def must_use_mfa(user):
    """Two-factor authentication is mandatory for the roles in MFA_REQUIRED_ROLES."""
    if not user.is_authenticated:
        return False
    role = Role.DISPATCHER if is_dispatcher(user) else user.role
    return role in settings.MFA_REQUIRED_ROLES


def is_unit_lead(user):
    return user.is_authenticated and user.role == Role.UNIT_LEAD and user.unit_id is not None


def invitable_roles(user):
    if not user.is_authenticated:
        return []
    if user.is_superuser:
        return [Role.DISPATCHER, Role.UNIT_LEAD, Role.MEMBER]
    if is_dispatcher(user) or is_unit_lead(user):
        return [Role.UNIT_LEAD, Role.MEMBER]
    return []


def can_invite(user):
    return bool(invitable_roles(user))


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
