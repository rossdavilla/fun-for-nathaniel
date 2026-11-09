import io
import json
import unittest
import urllib.error
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from collector import filters, output
from collector.fetch import Fetcher, RobotsBlocked
from collector.parsers import Event, parse_civicplus, parse_mec, parse_tribe, resolve_city

SOURCE = {"id": "s", "name": "Source", "default_city": "Turlock"}
FREE_DAYS = [
    {"name": "Thanksgiving break", "start": "2026-11-23", "end": "2026-11-27"},
]

MEC_FEED = b"""<?xml version="1.0"?><rss version="2.0" xmlns:mec="http://webnus.net/rss/mec/"><channel>
<item><title>Breakfast &#038; Santa</title><link>https://example.org/santa/</link>
<description><![CDATA[<img src="x.png"/> Pancakes!]]></description>
<mec:startDate>2026-12-05</mec:startDate><mec:startHour>8:00 am</mec:startHour>
<mec:endDate>2026-12-05</mec:endDate><mec:endHour>12:30 pm</mec:endHour>
<mec:location>211 Bodem Street</mec:location><mec:cost>$10.00</mec:cost>
<mec:category>Family</mec:category></item></channel></rss>"""

CIVIC_FEED = b"""<?xml version="1.0"?><rss version="2.0" xmlns:calendarEvent="https://www.countyofmerced.com/Calendar.aspx"><channel>
<item><title>Lego Time @ Los Banos Library 10:30 AM-12:00 PM</title><link>https://example.org/1</link>
<calendarEvent:EventDates> November 10, 2026 </calendarEvent:EventDates>
<calendarEvent:EventTimes>10:30 AM - 12:00 PM</calendarEvent:EventTimes>
<calendarEvent:Location>1312 Seventh StLos Banos, CA 93635</calendarEvent:Location></item>
</channel></rss>"""


def event(title="Show", start="2026-11-21T10:00:00", end=None, city="Turlock", **extra):
    return Event(
        source_id="s", source="Source", title=title,
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end) if end else None,
        city=city, **extra,
    )


class ParserTests(unittest.TestCase):
    def test_tribe_decodes_entities_and_tolerates_missing_venue(self):
        payload = json.dumps({"events": [{
            "title": "Benni&#8217;s 🎄Parade🎄", "start_date": "2026-12-04 19:00:00",
            "end_date": "2026-12-04 22:00:00", "all_day": False, "venue": [],
            "url": "https://example.org/e", "description": "<p>Hi</p>", "cost": "",
            "categories": [{"name": "Parade"}],
        }]})
        parsed = parse_tribe(payload, SOURCE)[0]
        self.assertEqual(parsed.title, "Benni’s Parade")
        self.assertEqual(parsed.city, "Turlock")
        self.assertEqual(parsed.summary, "Hi")

    def test_mec_reads_times_and_keeps_street_address_out_of_venue(self):
        parsed = parse_mec(MEC_FEED, SOURCE)[0]
        self.assertEqual(parsed.title, "Breakfast & Santa")
        self.assertEqual(parsed.start, datetime(2026, 12, 5, 8, 0))
        self.assertEqual(parsed.end, datetime(2026, 12, 5, 12, 30))
        self.assertEqual((parsed.venue, parsed.address), ("", "211 Bodem Street"))
        self.assertEqual(parsed.summary, "Pancakes!")

    def test_civicplus_splits_heading_and_rejects_out_of_area_town(self):
        parsed = parse_civicplus(CIVIC_FEED, SOURCE)[0]
        self.assertEqual((parsed.title, parsed.venue), ("Lego Time", "Los Banos Library"))
        self.assertEqual(parsed.start, datetime(2026, 11, 10, 10, 30))
        self.assertEqual(parsed.city, "")

    def test_civicplus_strips_time_range_with_one_meridiem(self):
        feed = CIVIC_FEED.replace(b"10:30 AM-12:00 PM</title>", "10:30–11:30 AM</title>".encode())
        self.assertEqual(parse_civicplus(feed, SOURCE)[0].venue, "Los Banos Library")

    def test_rejects_non_web_links(self):
        payload = json.dumps({"events": [{
            "title": "x", "start_date": "2026-12-04 19:00:00", "url": "javascript:alert(1)",
        }]})
        self.assertEqual(parse_tribe(payload, SOURCE)[0].url, "")

    def test_city_after_street_name_wins(self):
        self.assertEqual(resolve_city("100 N Turlock Rd, Merced, CA", "Modesto"), "Merced")
        self.assertEqual(resolve_city("Downtown", "Oakdale"), "Oakdale")


class FilterTests(unittest.TestCase):
    def test_fit(self):
        cases = {
            "BUNCO": None, "Adult Coloring": None, "Teens: D&D": None, "Tiny Tales": None,
            "Bookmobile": None, "Christmas Frame Show Sale": None,
            "Family Friday: Rainbow Magic": "great", "Bookmobile: Bilingual Storytime": "great",
            "Annual Tree Lighting Ceremony": "good", "Atwater Christmas Parade": "good",
        }
        for title, expected in cases.items():
            with self.subTest(title):
                self.assertEqual(filters.fit_for_kid(event(title)), expected)

    def test_availability(self):
        cases = [
            ("2026-11-21T10:00:00", None, True, "Weekend"),
            ("2026-11-18T10:00:00", None, False, "School-week"),
            ("2026-11-24T10:00:00", None, True, "Thanksgiving break"),
            ("2026-11-24T19:00:00", None, False, "School-week"),
            ("2026-11-20T10:00:00", "2026-11-22T16:00:00", True, "Weekend"),
        ]
        for start, end, expected, reason in cases:
            with self.subTest(start):
                self.assertEqual(filters.availability(event(start=start, end=end), FREE_DAYS),
                                 (expected, reason))

    def test_duplicates_need_same_town_and_day(self):
        first = event("The Polar Express")
        elsewhere = event("The Polar Express", city="Merced")
        self.assertTrue(filters.is_duplicate(first, event("The Polar Express!", city="turlock")))
        self.assertEqual(len(filters.remove_duplicates([first, elsewhere])), 2)
        self.assertFalse(filters.is_duplicate(first, event("Story Time")))


class OutputTests(unittest.TestCase):
    def test_google_link_for_timed_event(self):
        url = output.google_calendar_url(event("Art & Play", end="2026-11-21T11:30:00"))
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["dates"], ["20261121T100000/20261121T113000"])
        self.assertEqual(query["text"], ["Art & Play"])
        self.assertEqual(query["ctz"], ["America/Los_Angeles"])

    def test_google_all_day_end_is_exclusive(self):
        all_day = event(start="2026-11-28T00:00:00", end="2026-11-28T23:59:59", all_day=True)
        query = parse_qs(urlsplit(output.google_calendar_url(all_day)).query)
        self.assertEqual(query["dates"], ["20261128/20261129"])

    def test_time_warning(self):
        self.assertTrue(output.time_looks_wrong(event(start="2026-12-05T05:00:00")))
        self.assertFalse(output.time_looks_wrong(event(start="2026-12-05T17:00:00")))

    def test_ics_is_valid_shape(self):
        long_title = "Parade, with; commas " + "é" * 80
        text = output.build_ics([event(long_title)], datetime(2026, 11, 8, 12))
        self.assertIn("\r\n", text)
        self.assertIn("SUMMARY:Parade\\, with\\; commas", text)
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)
        unfolded = text.replace("\r\n ", "")
        self.assertIn("é" * 80, unfolded)
        self.assertIn("DTSTART;TZID=America/Los_Angeles:20261121T100000", unfolded)


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", None, io.BytesIO())


class FetcherTests(unittest.TestCase):
    def fetcher(self, robots, sleeps=None):
        def download(url):
            if url.endswith("/robots.txt"):
                if isinstance(robots, Exception):
                    raise robots
                return robots
            return b"page"
        return Fetcher(download=download, sleep=(sleeps if sleeps is not None else []).append,
                       clock=lambda: 0.0)

    def test_disallowed_url_is_not_fetched(self):
        with self.assertRaises(RobotsBlocked):
            self.fetcher(b"User-agent: *\nDisallow: /private\n").get("https://x.test/private/a")

    def test_missing_robots_allows(self):
        self.assertEqual(self.fetcher(http_error(403)).get("https://x.test/a"), b"page")

    def test_server_error_on_robots_blocks(self):
        with self.assertRaises(RobotsBlocked):
            self.fetcher(http_error(503)).get("https://x.test/a")

    def test_crawl_delay_is_honored(self):
        sleeps = []
        fetcher = self.fetcher(b"User-agent: *\nCrawl-delay: 10\n", sleeps)
        fetcher.get("https://x.test/a")
        fetcher.get("https://x.test/b")
        self.assertEqual(sleeps, [10])


if __name__ == "__main__":
    unittest.main()
