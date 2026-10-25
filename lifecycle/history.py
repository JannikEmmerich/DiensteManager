"""Recording the change history (``HistoryEntry``) where duties, reports, billings and
bank accounts change. Values are stored as plain JSON (dates in ISO format, related
objects as text) and formatted for display by ``templatetags/history_tags.py``.
"""

from datetime import date, datetime
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import models

from .models import HistoryEntry

Action = HistoryEntry.Action


def scope_for_duty(duty_id):
    return f"duty:{duty_id}"


def scope_for_billing(billing_id):
    return f"billing:{billing_id}"


def scope_for_unit(unit_id):
    return f"unit:{unit_id}"


def plain(value):
    """A JSON-compatible version of a field value."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, models.Model):
        return str(value)
    if isinstance(value, models.QuerySet | list | tuple | set):
        return sorted(str(item) for item in value)
    return str(value)


def change(field, old, new):
    return {"field": field, "old": plain(old), "new": plain(new)}


def form_changes(form, fields=None):
    """The fields a model form has changed, with their old and new values."""
    return [
        change(name, form.initial.get(name), form.cleaned_data.get(name))
        for name in form.changed_data
        if fields is None or name in fields
    ]


def record(obj, action, user, scope, changes=()):
    """Adds a history entry; ``user`` may be anonymous or None (e.g. for scripts)."""
    signed_in = user is not None and user.is_authenticated
    return HistoryEntry.objects.create(
        scope=scope,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=obj.pk,
        object_repr=str(obj)[:200],
        action=action,
        changes=list(changes),
        user=user if signed_in else None,
        user_repr=str(user)[:200] if signed_in else "",
    )


def entries(scope):
    return HistoryEntry.objects.filter(scope=scope).select_related("content_type")
