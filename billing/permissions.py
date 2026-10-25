"""Duty pay, bank details and billings are visible to the dispatchers and to the unit
leads of the unit concerned; only dispatchers create and change billings.
"""

from accounts.permissions import is_dispatcher, is_unit_lead

from .models import Billing


def can_view_unit_pay(user, unit):
    """See the duty pay overview and edit the bank details of the unit."""
    return is_dispatcher(user) or (is_unit_lead(user) and user.unit_id == unit.pk)


def visible_billings(user):
    if is_dispatcher(user):
        return Billing.objects.all()
    if is_unit_lead(user):
        return Billing.objects.filter(unit_id=user.unit_id)
    return Billing.objects.none()
