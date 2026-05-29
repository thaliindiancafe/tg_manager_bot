"""Tests for Google Calendar / Tasks public URLs."""

from src.utils.google_links import calendar_event_url, google_task_url, pick_calendar_event_url


def test_calendar_event_url():
    url = calendar_event_url("abc@group.calendar.google.com", "evt123")
    assert url.startswith("https://www.google.com/calendar/event?eid=")


def test_google_task_url_with_ids():
    url = google_task_url(tasklist_id="TL1", task_id="T1")
    assert "mail.google.com" in url
    assert "TL1" in url
    assert "T1" in url


def test_pick_calendar_prefers_html_link():
    url = pick_calendar_event_url(
        calendar_id="cal@google.com",
        event_id="e1",
        html_link="https://www.google.com/calendar/event?eid=abc",
    )
    assert url == "https://www.google.com/calendar/event?eid=abc"
