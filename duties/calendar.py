"""The calendar subscription (iCal, RFC 5545) of an account.

Calendar apps fetch the feed by its secret address without signing in. It lists the
duties the account may see – title, time, place and the link to the duty, no helper
names – from ``PAST_DAYS`` ago on. Cancelled duties stay in the feed with
``STATUS:CANCELLED`` so that calendar apps remove or strike them out.
"""

from datetime import UTC, timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.permissions import is_dispatcher

from .models import Duty, Report
from .permissions import visible_duties

# How far back the feed goes; calendar apps drop events that leave the feed.
PAST_DAYS = 180


def duties_for(feed):
    user = feed.user
    duties = visible_duties(user).filter(ends_at__gte=timezone.now() - timedelta(days=PAST_DAYS))
    if is_dispatcher(user):
        if not feed.include_drafts:
            duties = duties.exclude(status=Duty.Status.DRAFT)
    elif feed.only_own_reports:
        duties = duties.filter(
            reports__unit_id=user.unit_id, reports__status__in=Report.ACTIVE
        ).distinct()
    return duties.order_by("starts_at", "pk")


def _text(value):
    """Escapes a TEXT value."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line):
    """Lines are at most 75 octets; longer ones continue after a line break and a space."""
    data = line.encode()
    parts = []
    limit = 75
    while len(data) > limit:
        cut = limit
        while data[cut] & 0xC0 == 0x80:  # not inside a UTF-8 character
            cut -= 1
        parts.append(data[:cut])
        data = data[cut:]
        limit = 74  # the space at the start of the next line counts
    parts.append(data)
    return "\r\n ".join(part.decode() for part in parts)


def _time(value):
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _event(request, duty, now):
    if duty.status == Duty.Status.CANCELLED:
        summary, status = _("Cancelled: %(title)s") % {"title": duty.title}, "CANCELLED"
    elif duty.status == Duty.Status.DRAFT:
        summary, status = _("Draft: %(title)s") % {"title": duty.title}, "TENTATIVE"
    else:
        summary, status = duty.title, "CONFIRMED"
    url = request.build_absolute_uri(duty.get_absolute_url())
    lines = [
        "BEGIN:VEVENT",
        f"UID:duty-{duty.pk}@{request.get_host()}",
        f"DTSTAMP:{_time(now)}",
        f"LAST-MODIFIED:{_time(duty.updated_at)}",
        f"DTSTART:{_time(duty.starts_at)}",
        f"DTEND:{_time(duty.ends_at)}",
        f"SUMMARY:{_text(summary)}",
    ]
    if duty.location:
        lines.append(f"LOCATION:{_text(duty.location)}")
    lines += [f"URL:{url}", f"DESCRIPTION:{_text(url)}", f"STATUS:{status}", "END:VEVENT"]
    return lines


def render(request, duties):
    now = timezone.now()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{_text(settings.SITE_NAME)}//{settings.VERSION}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_text(settings.SITE_NAME)}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for duty in duties:
        lines += _event(request, duty, now)
    lines.append("END:VCALENDAR")
    return "".join(_fold(line) + "\r\n" for line in lines)


def feed_url(request, feed):
    return request.build_absolute_uri(reverse("calendar_feed", args=[feed.token]))
