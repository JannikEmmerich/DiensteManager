from django import template
from django.template.defaultfilters import floatformat
from django.utils import formats, timezone
from django.utils.translation import gettext as _

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


@register.filter
def money(value):
    """An amount in euros in the current language, e.g. "1,234.50 €" or "1.234,50 €"."""
    if value is None or value == "":
        return ""
    return f"{formats.number_format(value, 2, force_grouping=True)} €"


@register.filter
def hours(value):
    """Hours with up to two decimals, e.g. "8" or "7.5"."""
    if value is None or value == "":
        return ""
    return floatformat(value, -2)


@register.filter
def pay(duty, rate=None):
    """The duty pay of a duty, e.g. "12.00 € per helper hour"; empty without pay.

    ``rate`` replaces the rate of the duty, e.g. by that of a position or a report.
    """
    rate = duty.pay_rate if rate in (None, "") else rate
    if duty.pay_type == duty.PayType.HOURLY:
        return _("%(amount)s per helper hour") % {"amount": money(rate)}
    if duty.pay_type == duty.PayType.FLAT:
        return _("%(amount)s flat rate per helper") % {"amount": money(rate)}
    return ""
