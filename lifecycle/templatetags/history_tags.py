from django import template
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.template.defaultfilters import truncatechars
from django.utils import formats, timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import capfirst
from django.utils.translation import gettext as _

register = template.Library()


def _display(field, value):
    if value is None or value == "" or value == []:
        return "–"
    if isinstance(value, list):
        return ", ".join(value)
    if field is None:
        return truncatechars(str(value), 120)
    if field.choices:
        return str(dict(field.flatchoices).get(value, value))
    if isinstance(field, models.BooleanField) or isinstance(value, bool):
        return _("yes") if value else _("no")
    if isinstance(field, models.DateTimeField):
        moment = parse_datetime(value)
        return formats.date_format(timezone.localtime(moment), "SHORT_DATETIME_FORMAT")
    if isinstance(field, models.DateField):
        return formats.date_format(parse_date(value), "SHORT_DATE_FORMAT")
    if isinstance(field, models.DecimalField):
        return formats.number_format(value, field.decimal_places)
    return truncatechars(str(value), 120)


@register.simple_tag
def history_changes(entry):
    """(label, old value, new value) for each change of a history entry, ready to show."""
    model = entry.model
    rows = []
    for item in entry.changes:
        try:
            field = model._meta.get_field(item["field"]) if model else None
        except FieldDoesNotExist:
            field = None
        if field is not None and field.is_relation and field.auto_created:
            # A reverse relation such as the positions of a duty: a list of texts.
            label = capfirst(field.related_model._meta.verbose_name_plural)
            field = None
        else:
            label = capfirst(field.verbose_name) if field is not None else item["field"]
        rows.append((label, _display(field, item["old"]), _display(field, item["new"])))
    return rows
