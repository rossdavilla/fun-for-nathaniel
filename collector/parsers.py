"""Turn each calendar platform's feed into a common Event record."""

import html
import json
import re
import unicodedata
import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

MEC_NS = "{http://webnus.net/rss/mec/}"
CIVIC_NS = "{https://www.countyofmerced.com/Calendar.aspx}"
MAX_FEED_PAGES = 6
SUMMARY_LIMIT = 220

IN_AREA = [
    "Turlock", "Keyes", "Denair", "Hilmar", "Delhi", "Hughson", "Ceres", "Modesto",
    "Merced", "Atwater", "Livingston", "Winton", "Riverbank", "Oakdale", "Salida",
    "Empire", "Waterford", "Patterson", "Gustine", "Ballico", "Cressey", "Stevinson",
    "Planada",
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


def collect_source(source, fetcher, today, horizon_days):
    """Download every page of a source that falls inside the look-ahead window."""
    last_day = today + timedelta(days=horizon_days)
    if source["type"] == "tribe":
        return _collect_tribe(source, fetcher, today, last_day)
    if source["type"] == "mec":
        return _collect_mec(source, fetcher, last_day)
    if source["type"] == "civicplus":
        return parse_civicplus(fetcher.get(source["feed"]), source)
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
