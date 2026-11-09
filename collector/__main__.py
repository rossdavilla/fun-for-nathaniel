"""Collect events and rebuild the site's data. Run: python3 -m collector"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import filters, output
from .fetch import Fetcher
from .parsers import Event, collect_source

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "raw"
SITE_DATA = ROOT / "docs" / "data" / "events.json"
SITE_ICS = ROOT / "docs" / "nathaniel-picks.ics"
HORIZON_DAYS = 60


def read_cache(source):
    path = CACHE_DIR / f"{source['id']}.json"
    if not path.exists():
        return []
    return [Event.from_json(item) for item in json.loads(path.read_text())]


def write_cache(source, events):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{source['id']}.json"
    path.write_text(json.dumps([event.to_json() for event in events], indent=1))


def gather(sources, today, offline):
    """Fetch each source, falling back to its last good copy if it fails."""
    fetcher = Fetcher()
    events, statuses = [], []
    for source in sources:
        status = {"name": source["name"], "state": "ok", "found": 0}
        if offline:
            found = read_cache(source)
            status["state"] = "cached"
        else:
            try:
                found = collect_source(source, fetcher, today, HORIZON_DAYS)
                write_cache(source, found)
            except Exception as error:  # One bad source must never stop the others.
                print(f"  ! {source['name']}: {error}", file=sys.stderr)
                found = read_cache(source)
                status["state"] = "failed"
        status["found"] = len(found)
        statuses.append(status)
        events.extend(found)
    return events, statuses


def in_window(event, today):
    last_listed_day = today + timedelta(days=HORIZON_DAYS)
    return (event.end or event.start).date() >= today and event.start.date() <= last_listed_day


def build(events, today, free_days):
    """Return (site records, events for the subscribable picks feed)."""
    upcoming = [event for event in events if event.city and in_window(event, today)]
    upcoming.sort(key=lambda event: event.start)
    records, picks = [], []
    for event in filters.remove_duplicates(upcoming):
        fit = filters.fit_for_kid(event)
        if not fit:
            continue
        available, reason = filters.availability(event, free_days)
        records.append(output.site_record(event, fit, filters.interest_tags(event), available, reason))
        if available and fit == "great":
            picks.append(event)
    return records, picks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="reuse the last download")
    parser.add_argument("--today", type=date.fromisoformat, help="pretend it is this date")
    args = parser.parse_args()

    all_sources = json.loads((ROOT / "config" / "sources.json").read_text())
    sources = [source for source in all_sources if source.get("enabled", True)]
    free_days = json.loads((ROOT / "config" / "school_breaks.json").read_text())["free_days"]
    today = args.today or datetime.now(ZoneInfo(output.TIME_ZONE)).date()

    events, statuses = gather(sources, today, args.offline)
    if not args.offline and all(status["state"] == "failed" for status in statuses):
        print("Every source failed; leaving the site unchanged.", file=sys.stderr)
        return 1

    records, picks = build(events, today, free_days)
    generated_at = datetime.now(timezone.utc)
    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    SITE_DATA.write_text(json.dumps({
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "free_days": free_days,
        "sources": statuses,
        "events": records,
    }, indent=1, ensure_ascii=False))
    SITE_ICS.write_bytes(output.build_ics(picks, generated_at).encode())

    for status in statuses:
        print(f"  {status['name']}: {status['found']} found ({status['state']})")
    shown = sum(record["available"] for record in records)
    print(f"Listed {len(records)} kid-friendly events: {shown} on free days, {len(picks)} top picks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
