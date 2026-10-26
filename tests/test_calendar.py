"""The calendar subscription (iCal): a secret address per account with its duties."""

from datetime import timedelta

import pytest
from django.urls import reverse

from accounts.models import CalendarFeed
from duties.calendar import PAST_DAYS, _fold
from duties.models import Duty, Report

from .conftest import create_duty, create_report


def _feed(user, **fields):
    feed = CalendarFeed(user=user, **fields)
    feed.new_token()
    feed.save()
    return feed


def _get(client, feed):
    return client.get(reverse("calendar_feed", args=[feed.token]))


def _summaries(response):
    return [
        line.removeprefix("SUMMARY:")
        for line in response.content.decode().split("\r\n")
        if line.startswith("SUMMARY:")
    ]


# --- The feed ----------------------------------------------------------------------------------


def test_feed_without_sign_in(client, member, unit):
    duty = create_duty(title="Marathon, half", location="Market square; north")
    feed = _feed(member)
    response = _get(client, feed)
    assert response.status_code == 200
    assert response["Content-Type"] == "text/calendar; charset=utf-8"
    body = response.content.decode()
    assert body.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert body.endswith("END:VCALENDAR\r\n")
    assert f"UID:duty-{duty.pk}@testserver" in body
    assert "SUMMARY:Marathon\\, half" in body
    assert "LOCATION:Market square\\; north" in body
    assert f"URL:http://testserver/duties/{duty.pk}/" in body
    assert "STATUS:CONFIRMED" in body
    assert "DTSTART:" in body and "DTEND:" in body


def test_no_helpers_in_the_feed(client, member, unit):
    duty = create_duty()
    create_report(duty, unit, name="Hanna Secret", note="Phone 0171")
    body = _get(client, _feed(member)).content.decode()
    assert "Hanna Secret" not in body
    assert "0171" not in body


def test_units_get_the_duties_offered_to_them(client, member, unit, other_unit):
    create_duty(title="Ours")
    create_duty(title="Theirs", units=[other_unit])
    create_duty(title="Draft", status=Duty.Status.DRAFT)
    create_duty(title="Long ago", starts_in=timedelta(days=-PAST_DAYS - 2))
    cancelled = create_duty(title="Called off", status=Duty.Status.CANCELLED)
    response = _get(client, _feed(member))
    assert _summaries(response) == ["Ours", "Cancelled: Called off"]
    body = response.content.decode()
    assert f"UID:duty-{cancelled.pk}@testserver\r\n" in body
    assert "STATUS:CANCELLED" in body


def test_units_can_limit_it_to_duties_with_their_reports(client, member, unit, other_unit):
    with_report = create_duty(title="With report")
    create_report(with_report, unit)
    withdrawn = create_duty(title="Withdrawn")
    create_report(withdrawn, unit, status=Report.Status.WITHDRAWN)
    create_duty(title="Without")
    feed = _feed(member, only_own_reports=True)
    assert _summaries(_get(client, feed)) == ["With report"]


def test_dispatchers_get_all_duties_drafts_if_wanted(client, dispatcher, other_unit):
    create_duty(title="Released", units=[other_unit])
    create_duty(title="Plan", status=Duty.Status.DRAFT)
    feed = _feed(dispatcher)
    assert _summaries(_get(client, feed)) == ["Released"]
    feed.include_drafts = True
    feed.save()
    assert _summaries(_get(client, feed)) == ["Released", "Draft: Plan"]


def test_feed_in_the_language_of_the_account(client, member):
    member.language = "de"
    member.save()
    create_duty(title="Fest", status=Duty.Status.CANCELLED)
    assert _summaries(_get(client, _feed(member))) == ["Abgesagt: Fest"]


@pytest.mark.parametrize("change", ["unknown", "blocked"])
def test_feed_only_with_a_valid_token_of_an_active_account(client, member, change):
    feed = _feed(member)
    if change == "blocked":
        member.is_active = False
        member.save()
    else:
        feed.token = "unknown"
    assert _get(client, feed).status_code == 404


def test_long_lines_are_folded():
    line = "SUMMARY:" + "Sanitätsdienst " * 10
    folded = _fold(line)
    parts = folded.split("\r\n ")
    assert all(len(part.encode()) <= 75 for part in parts)
    assert "".join(parts) == line


# --- Settings ----------------------------------------------------------------------------------


def test_create_renew_and_end_the_subscription(client, member):
    client.force_login(member)
    url = reverse("calendar_settings")
    page = client.get(url).text
    assert "Create calendar address" in page
    client.post(url, {"action": "create"})
    feed = CalendarFeed.objects.get(user=member)
    page = client.get(url).text
    assert f"http://testserver/duties/calendar/{feed.token}.ics" in page
    assert f"webcal://testserver/duties/calendar/{feed.token}.ics" in page
    old_token = feed.token
    client.post(url, {"action": "renew"})
    feed.refresh_from_db()
    assert feed.token != old_token
    assert client.get(reverse("calendar_feed", args=[old_token])).status_code == 404
    client.post(url, {"action": "delete"})
    assert not CalendarFeed.objects.exists()


@pytest.mark.parametrize(
    ("account", "shown", "hidden"),
    [
        ("member", "only_own_reports", "include_drafts"),
        ("dispatcher", "include_drafts", "only_own_reports"),
    ],
)
def test_options_depend_on_the_role(client, request, account, shown, hidden):
    user = request.getfixturevalue(account)
    feed = _feed(user)
    client.force_login(user)
    page = client.get(reverse("calendar_settings")).text
    assert f'name="{shown}"' in page
    assert f'name="{hidden}"' not in page
    client.post(reverse("calendar_settings"), {"action": "save", shown: "on", hidden: "on"})
    feed.refresh_from_db()
    assert getattr(feed, shown) is True
    assert getattr(feed, hidden) is False


def test_settings_are_linked_under_my_account(client, member):
    client.force_login(member)
    assert reverse("calendar_settings") in client.get(reverse("profile")).text


def test_line_breaks_in_texts_are_escaped(client, member):
    create_duty(title="Concert\rSTATUS:CANCELLED", location="Hall\r\nnorth\nentrance")
    body = _get(client, _feed(member)).content.decode()
    assert "SUMMARY:Concert\\nSTATUS:CANCELLED\r\n" in body
    assert "LOCATION:Hall\\nnorth\\nentrance\r\n" in body
    assert "\rSTATUS:CANCELLED" not in body
