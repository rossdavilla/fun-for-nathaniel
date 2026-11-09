"""Build the site's data file, Google Calendar links, and subscribable .ics feed."""

import hashlib
import re
from datetime import timedelta
from urllib.parse import quote, urlencode

TIME_ZONE = "America/Los_Angeles"
CALENDAR_NAME = "Fun for Nathaniel"
DEFAULT_DURATION = timedelta(hours=2)
EARLIEST_LIKELY_HOUR = 7
LATEST_LIKELY_HOUR = 22

VTIMEZONE = [
    "BEGIN:VTIMEZONE", f"TZID:{TIME_ZONE}",
    "BEGIN:DAYLIGHT", "TZOFFSETFROM:-0800", "TZOFFSETTO:-0700", "TZNAME:PDT",
    "DTSTART:19700308T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", "END:DAYLIGHT",
    "BEGIN:STANDARD", "TZOFFSETFROM:-0700", "TZOFFSETTO:-0800", "TZNAME:PST",
    "DTSTART:19701101T020000", "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU", "END:STANDARD",
    "END:VTIMEZONE",
]


def event_id(event):
    key = f"{re.sub(r'[^a-z0-9]', '', event.title.lower())}|{event.start.date()}|{event.city.lower()}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def effective_end(event):
    if event.end and event.end > event.start:
        return event.end
    return event.start + DEFAULT_DURATION


def location_text(event):
    place = event.venue or event.address
    if event.city and event.city.lower() not in place.lower():
        return f"{place}, {event.city}, CA" if place else f"{event.city}, CA"
    return place


def time_label(event):
    if event.all_day:
        return "All day"
    start = event.start.strftime("%-I:%M %p")
    if not event.end or event.end <= event.start or event.end.date() != event.start.date():
        return start
    end = event.end.strftime("%-I:%M %p")
    if start[-2:] == end[-2:]:
        start = start[:-3]
    return f"{start} – {end}"


def time_looks_wrong(event):
    return not event.all_day and not EARLIEST_LIKELY_HOUR <= event.start.hour < LATEST_LIKELY_HOUR


def _details(event):
    parts = [event.summary, f"Details: {event.url}" if event.url else "", f"Listed by {event.source}"]
    if time_looks_wrong(event):
        parts.insert(0, "Check the time with the organizer: the listing may be wrong.")
    return "\n\n".join(part for part in parts if part)


def google_calendar_url(event):
    if event.all_day:
        last_day = max(event.start.date(), (event.end or event.start).date())
        dates = f"{event.start:%Y%m%d}/{last_day + timedelta(days=1):%Y%m%d}"
    else:
        dates = f"{event.start:%Y%m%dT%H%M%S}/{effective_end(event):%Y%m%dT%H%M%S}"
    query = urlencode({
        "action": "TEMPLATE",
        "text": event.title,
        "dates": dates,
        "ctz": TIME_ZONE,
        "details": _details(event),
        "location": location_text(event),
    }, quote_via=quote)
    return f"https://calendar.google.com/calendar/render?{query}"


def site_record(event, fit, tags, available, reason):
    return {
        "id": event_id(event),
        "title": event.title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat() if event.end else None,
        "all_day": event.all_day,
        "time_label": time_label(event),
        "check_time": time_looks_wrong(event),
        "venue": event.venue,
        "city": event.city,
        "location": location_text(event),
        "cost": event.cost,
        "url": event.url,
        "source": event.source,
        "summary": event.summary,
        "fit": fit,
        "tags": tags,
        "available": available,
        "when_reason": reason,
        "google_url": google_calendar_url(event),
    }


def _escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n"))


def _fold(line):
    """Split a content line at 75 octets without breaking a UTF-8 character."""
    chunks, current, size, limit = [], "", 0, 75
    for char in line:
        width = len(char.encode())
        if size + width > limit:
            chunks.append(current)
            current, size, limit = " ", 1, 75
        current += char
        size += width
    chunks.append(current)
    return "\r\n".join(chunks)


def _vevent(event, stamp):
    lines = ["BEGIN:VEVENT", f"UID:{event_id(event)}@fun-for-nathaniel", f"DTSTAMP:{stamp}"]
    if event.all_day:
        last_day = max(event.start.date(), (event.end or event.start).date())
        lines += [f"DTSTART;VALUE=DATE:{event.start:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{last_day + timedelta(days=1):%Y%m%d}"]
    else:
        lines += [f"DTSTART;TZID={TIME_ZONE}:{event.start:%Y%m%dT%H%M%S}",
                  f"DTEND;TZID={TIME_ZONE}:{effective_end(event):%Y%m%dT%H%M%S}"]
    lines += [f"SUMMARY:{_escape(event.title)}",
              f"LOCATION:{_escape(location_text(event))}",
              f"DESCRIPTION:{_escape(_details(event))}"]
    if event.url:
        lines.append(f"URL:{event.url}")
    lines.append("END:VEVENT")
    return lines


def build_ics(events, generated_at):
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fun for Nathaniel//Events//EN",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{CALENDAR_NAME}", f"X-WR-TIMEZONE:{TIME_ZONE}"]
    lines += VTIMEZONE
    for event in events:
        lines += _vevent(event, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
