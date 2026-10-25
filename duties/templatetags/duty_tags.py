from django import template
from django.utils import formats, timezone

register = template.Library()


def _date(value):
    return formats.date_format(value, "D") + ", " + formats.date_format(value, "SHORT_DATE_FORMAT")


@register.filter
def period(duty):
    """Start and end of a duty in the current language, e.g. "Sat, 11/14/2026, 8 a.m. – 4 p.m."."""
    start = timezone.localtime(duty.starts_at)
    end = timezone.localtime(duty.ends_at)
    start_time = formats.time_format(start, "TIME_FORMAT")
    end_time = formats.time_format(end, "TIME_FORMAT")
    if start.date() == end.date():
        return f"{_date(start)}, {start_time} – {end_time}"
    return f"{_date(start)}, {start_time} – {_date(end)}, {end_time}"
