"""Turn each calendar platform's feed into a common Event record."""

import html
import json
import re
import unicodedata
import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

MEC_NS = "{http://webnus.net/rss/mec/}"
CIVIC_NS = "{https://www.countyofmerced.com/Calendar.aspx}"
MAX_FEED_PAGES = 6
SUMMARY_LIMIT = 220
CHILDRENS_PROGRAM = "Children's program"

IN_AREA = [
    "Turlock", "Keyes", "Denair", "Hilmar", "Delhi", "Hughson", "Ceres", "Modesto",
    "Merced", "Atwater", "Livingston", "Winton", "Riverbank", "Oakdale", "Salida",
    "Empire", "Waterford", "Patterson", "Gustine", "Ballico", "Cressey", "Stevinson",
    "Planada", "Newman", "Grayson", "Ripon",
]
OUT_OF_AREA = ["Los Banos", "Dos Palos", "Le Grand", "Santa Nella", "Stockton", "Manteca"]


@dataclass
class Event:
    source_id: str
    source: str
    title: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    venue: str = ""
    address: str = ""
    city: str = ""
    cost: str = ""
    url: str = ""
    summary: str = ""
    categories: list = field(default_factory=list)
    ages: list | None = None

    def to_json(self):
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat() if self.end else None
        return data

    @classmethod
    def from_json(cls, data):
        values = dict(data)
        values["start"] = datetime.fromisoformat(data["start"])
        values["end"] = datetime.fromisoformat(data["end"]) if data["end"] else None
        return cls(**values)


def plain_text(value):
    """Strip markup and decorative symbols from text supplied by a website."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in {"So", "Sk", "Cf", "Cn"})
    return re.sub(r"\s+", " ", text).strip()


def shorten(text, limit=SUMMARY_LIMIT):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def safe_url(value):
    url = (value or "").strip()
    return url if url.startswith(("https://", "http://")) else ""


def resolve_city(location, default):
    """Return the area town named in an address, "" if out of area, else default."""
    text = (location or "").lower()
    best_city, best_position = None, -1
    for city in IN_AREA + OUT_OF_AREA:
        position = text.rfind(city.lower())
        if position > best_position:
            best_city, best_position = city, position
    if best_city is None:
        return default
    return "" if best_city in OUT_OF_AREA else best_city


def parse_clock(value):
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m", value or "", re.I)
    if not match:
        return None
    hour = int(match[1]) % 12 + (12 if match[3].lower() == "p" else 0)
    return hour, int(match[2] or 0)


def at_time(day, clock):
    return day.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)


AGE_PATTERNS = [
    (re.compile(r"ages? (\d+)\s*(?:-|–|to)\s*(\d+)", re.I), lambda m: [int(m[1]), int(m[2])]),
    (re.compile(r"ages? (\d+)\s*(?:\+|and (?:up|older))", re.I), lambda m: [int(m[1]), 99]),
    (re.compile(r"ages? (\d+)\s*and (?:under|younger)", re.I), lambda m: [0, int(m[1])]),
    (re.compile(r"up to age (\d+)", re.I), lambda m: [0, int(m[1])]),
    (re.compile(r"(\d+)(?:st|nd|rd|th) grade and up", re.I), lambda m: [int(m[1]) + 5, 99]),
]


def stated_ages(description):
    """Return the first [youngest, oldest] age range a description states, if any."""
    if re.search(r"all ages", description or "", re.I):
        return None
    found = [(match.start(), convert(match))
             for pattern, convert in AGE_PATTERNS
             if (match := pattern.search(description or ""))]
    return min(found)[1] if found else None


def parse_tribe(payload, source):
    """Parse a response from The Events Calendar's WordPress REST API."""
    events = []
    for item in json.loads(payload).get("events", []):
        venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
        categories = [plain_text(c.get("name")) for c in item.get("categories") or []]
        events.append(Event(
            source_id=source["id"],
            source=source["name"],
            title=plain_text(item.get("title")),
            start=datetime.fromisoformat(item["start_date"]),
            end=datetime.fromisoformat(item["end_date"]) if item.get("end_date") else None,
            all_day=bool(item.get("all_day")),
            venue=plain_text(venue.get("venue")),
            address=plain_text(venue.get("address")),
            city=plain_text(venue.get("city")) or source["default_city"],
            cost=plain_text(item.get("cost")),
            url=safe_url(item.get("url")),
            summary=shorten(plain_text(item.get("description"))),
            categories=categories,
            ages=stated_ages(plain_text(item.get("description"))),
        ))
    return events


def parse_mec(payload, source):
    """Parse an RSS feed from the Modern Events Calendar WordPress plugin."""
    events = []
    for item in ET.fromstring(payload).iter("item"):
        day = datetime.fromisoformat(item.findtext(f"{MEC_NS}startDate").strip())
        end_day_text = (item.findtext(f"{MEC_NS}endDate") or "").strip()
        end_day = datetime.fromisoformat(end_day_text) if end_day_text else day
        start_clock = parse_clock(item.findtext(f"{MEC_NS}startHour"))
        end_clock = parse_clock(item.findtext(f"{MEC_NS}endHour"))
        location = plain_text(item.findtext(f"{MEC_NS}location"))
        address_like = bool(re.match(r"\d", location))
        city = resolve_city(location, source["default_city"])
        events.append(Event(
            source_id=source["id"],
            source=source["name"],
            title=plain_text(item.findtext("title")),
            start=at_time(day, start_clock) if start_clock else day,
            end=at_time(end_day, end_clock) if start_clock and end_clock else end_day,
            all_day=start_clock is None,
            venue="" if address_like else location,
            address=location if address_like else "",
            city=city,
            cost=plain_text(item.findtext(f"{MEC_NS}cost")),
            url=safe_url(item.findtext("link")),
            summary=shorten(plain_text(item.findtext("description"))),
            categories=[plain_text(c.text) for c in item.findall(f"{MEC_NS}category")],
            ages=stated_ages(plain_text(item.findtext("description"))),
        ))
    return events


TRAILING_TIME = re.compile(r"\s+\d{1,2}(:\d{2})?\s*([AP]M)?\s*[-–—]\s*\d{1,2}(:\d{2})?\s*[AP]M\s*$", re.I)
LONG_DATE = re.compile(r"[A-Z][a-z]+ \d{1,2}, \d{4}")


def parse_civicplus(payload, source):
    """Parse a CivicPlus calendar RSS feed."""
    events = []
    for item in ET.fromstring(payload).iter("item"):
        dates = LONG_DATE.findall(item.findtext(f"{CIVIC_NS}EventDates") or "")
        if not dates:
            continue
        start_day = datetime.strptime(dates[0], "%B %d, %Y")
        end_day = datetime.strptime(dates[-1], "%B %d, %Y")
        times = (item.findtext(f"{CIVIC_NS}EventTimes") or "").split(" - ")
        start_clock = parse_clock(times[0])
        end_clock = parse_clock(times[-1]) if len(times) > 1 else None
        heading = TRAILING_TIME.sub("", plain_text(item.findtext("title")))
        title, _, venue = heading.partition(" @ ")
        location = plain_text(item.findtext(f"{CIVIC_NS}Location"))
        events.append(Event(
            source_id=source["id"],
            source=source["name"],
            title=title.strip(),
            start=at_time(start_day, start_clock) if start_clock else start_day,
            end=at_time(end_day, end_clock) if start_clock and end_clock else end_day,
            all_day=start_clock is None,
            venue=venue.strip(),
            address=location,
            city=resolve_city(location, source["default_city"]),
            url=safe_url(item.findtext("link")),
        ))
    return events


EP_DATE_TIME = re.compile(r"^\w+,\s*\w+\.?\s+(\d{1,2}),?\s*(.*)$")
EP_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", re.I)


def parse_clock_range(text):
    """Read "3 pm", "3:30-4:30 pm", or "3, 3:30, or 4 pm" as (start, end) clocks."""
    found = EP_CLOCK.findall(text or "")
    meridiems = [item[2] for item in found if item[2]]
    if not found or not meridiems:
        return None, None

    def clock(item, meridiem):
        return (int(item[0]) % 12 + (12 if meridiem.lower() == "pm" else 0), int(item[1] or 0))

    last = meridiems[-1]
    end = clock(found[-1], found[-1][2] or last) if "-" in text and len(found) > 1 else None
    start = clock(found[0], found[0][2] or last)
    if end and start > end and not found[0][2]:  # "11-1 pm" starts in the morning.
        start = clock(found[0], "am")
    return start, end


def _ep_field(pattern, block):
    match = re.search(pattern, block, re.S)
    return plain_text(match[1]) if match else ""


def parse_engagedpatrons(payload, source, year, month):
    """Parse one month of an Engaged Patrons library calendar page."""
    page = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    events = []
    for block in page.split('<div class="LECalendarCellEvent"')[1:]:
        when = EP_DATE_TIME.match(_ep_field(r'LEPopUpDateTime">(.*?)</span>', block))
        if not when:
            continue
        start_clock, end_clock = parse_clock_range(when[2])
        day = datetime(year, month, int(when[1]))
        branch = _ep_field(r'LEPopUpDateTime"><br />(.*?)<br />', block)
        description = _ep_field(r'LEPopUpDescription[^>]*>(.*?)</div>', block).removesuffix("... read more")
        link = re.search(r'href="(//engagedpatrons\.org/EventsExtended\.cfm[^"]*)"', block)
        events.append(Event(
            source_id=source["id"],
            source=source["name"],
            title=_ep_field(r'LEPopUpTitle[^>]*>(.*?)</div>', block),
            start=at_time(day, start_clock) if start_clock else day,
            end=at_time(day, end_clock) if start_clock and end_clock else day if not start_clock else None,
            all_day=start_clock is None,
            venue=branch,
            city=resolve_city(branch, source["default_city"]),
            url=safe_url("https:" + html.unescape(link[1])) if link else "",
            summary=shorten(description.strip()),
            categories=[CHILDRENS_PROGRAM],
            ages=stated_ages(description),
        ))
    return events


def public_facebook_link(value):
    """Keep organizer sites and Facebook Event pages, but never a link into a group."""
    url = safe_url(value)
    parts = urlsplit(url)
    if parts.hostname and parts.hostname.endswith("facebook.com") and not parts.path.startswith("/events/"):
        return ""
    return url


def parse_facebook(payload, source):
    """Read events saved by the Facebook skill. Anything not marked public is dropped."""
    events = []
    for item in json.loads(payload).get("events", []):
        if item.get("visibility") != "public" or not item.get("title"):
            continue
        try:
            start = datetime.fromisoformat(item["start"])
            end = datetime.fromisoformat(item["end"]) if item.get("end") else None
        except (KeyError, TypeError, ValueError):
            continue
        location = " ".join(filter(None, [item.get("venue"), item.get("address"), item.get("city")]))
        events.append(Event(
            source_id=source["id"],
            source=source["name"],
            title=plain_text(item["title"]),
            start=start.replace(tzinfo=None),
            end=end.replace(tzinfo=None) if end else None,
            all_day=bool(item.get("all_day")),
            venue=plain_text(item.get("venue")),
            address=plain_text(item.get("address")),
            city=resolve_city(location, source["default_city"]),
            cost=plain_text(item.get("cost")),
            url=public_facebook_link(item.get("url")),
            summary=shorten(plain_text(item.get("summary"))),
            categories=[CHILDRENS_PROGRAM],
            ages=stated_ages(plain_text(item.get("summary"))),
        ))
    return events


def collect_source(source, fetcher, today, horizon_days):
    """Download every page of a source that falls inside the look-ahead window."""
    if source["type"] == "facebook":
        path = Path(__file__).resolve().parent.parent / source["file"]
        return parse_facebook(path.read_text(), source) if path.exists() else []
    last_day = today + timedelta(days=horizon_days)
    if source["type"] == "tribe":
        return _collect_tribe(source, fetcher, today, last_day)
    if source["type"] == "mec":
        return _collect_mec(source, fetcher, last_day)
    if source["type"] == "civicplus":
        return parse_civicplus(fetcher.get(source["feed"]), source)
    if source["type"] == "engagedpatrons":
        return _collect_engagedpatrons(source, fetcher, today, last_day)
    raise ValueError(f"Unknown source type: {source['type']}")


def _collect_tribe(source, fetcher, today, last_day):
    events = []
    for page in range(1, MAX_FEED_PAGES + 1):
        url = (f"{source['site']}/wp-json/tribe/events/v1/events?per_page=50&page={page}"
               f"&start_date={today.isoformat()}&end_date={last_day.isoformat()}")
        payload = fetcher.get(url)
        events.extend(parse_tribe(payload, source))
        if page >= int(json.loads(payload).get("total_pages") or 1):
            break
    return events


def _collect_engagedpatrons(source, fetcher, today, last_day):
    events = []
    year, month = today.year, today.month
    while (year, month) <= (last_day.year, last_day.month):
        for audience in source["audiences"]:
            url = (f"https://engagedpatrons.org/EventsCalendar.cfm?SiteID={source['site_id']}"
                   f"&thisMonth={month}&thisYear={year}&BranchID=&keyword=&audience={audience}")
            events.extend(parse_engagedpatrons(fetcher.get(url), source, year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return events


def _collect_mec(source, fetcher, last_day):
    events = []
    for page in range(1, MAX_FEED_PAGES + 1):
        url = source["feed"] if page == 1 else f"{source['feed']}?paged={page}"
        try:
            found = parse_mec(fetcher.get(url), source)
        except urllib.error.HTTPError as error:
            if page > 1 and error.code == 404:
                break  # WordPress answers 404 one page past the end.
            raise
        events.extend(found)
        if not found or found[-1].start.date() > last_day:
            break
    return events
