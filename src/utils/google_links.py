"""Public URLs for Google Calendar events and Google Tasks (verification links)."""

from __future__ import annotations

import base64


def calendar_event_url(calendar_id: str, event_id: str) -> str:
    """Open event in Google Calendar (works for primary and shared calendars)."""
    cal = (calendar_id or "").strip()
    eid = (event_id or "").strip()
    if not cal or not eid:
        return "https://calendar.google.com/"
    raw = f"{eid} {cal}".encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"https://www.google.com/calendar/event?eid={token}"


def google_task_url(*, tasklist_id: str = "", task_id: str = "") -> str:
    """Deep link to a task in Gmail / Google Tasks (best-effort)."""
    tl = (tasklist_id or "").strip()
    tid = (task_id or "").strip()
    if tl and tid:
        return f"https://mail.google.com/mail/u/0/#tasks/{tl}/{tid}"
    if tl:
        return f"https://mail.google.com/mail/u/0/#tasks/{tl}"
    return "https://tasks.google.com/"


def pick_calendar_event_url(
    *,
    calendar_id: str,
    event_id: str,
    html_link: str = "",
) -> str:
    link = (html_link or "").strip()
    if link.startswith("http"):
        return link
    return calendar_event_url(calendar_id, event_id)
