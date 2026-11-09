import io
import json
import unittest
import urllib.error
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from collector import filters, output
from collector.fetch import Fetcher, RobotsBlocked
from collector.parsers import (
    CHILDRENS_PROGRAM, Event, parse_barnesnoble, parse_civicplus, parse_clock_range,
    parse_engagedpatrons, parse_facebook, parse_gallo, parse_localist, parse_mec,
    parse_squarespace, parse_tribe, resolve_city, stated_ages,
)

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

    def test_engagedpatrons_event(self):
        page = (
            '<div class="LECalendarCellEvent" style="x"><a class="LEToolTipLink" '
            'href="//engagedpatrons.org/EventsExtended.cfm?SiteID=2487&EventID=9&PK=">x</a>'
            '<div class="tooltiptext"><div class="LEPopUpTitle" style="y">Children&rsquo;s LEGO&reg; Play</div>'
            '<span class="LEPopUpDateTime"> Saturday, Nov. 14, 3:30-4:30 pm</span>'
            '<span class="LEPopUpDateTime"><br />Turlock Library <br />'
            '<div class="LEPopUpDescription" align="left">For ages 8 and up; ages 10 ... read more</div>'
        )
        parsed = parse_engagedpatrons(page, {**SOURCE, "default_city": ""}, 2026, 11)[0]
        self.assertEqual(parsed.title, "Children’s LEGO Play")
        self.assertEqual((parsed.start, parsed.end),
                         (datetime(2026, 11, 14, 15, 30), datetime(2026, 11, 14, 16, 30)))
        self.assertEqual((parsed.venue, parsed.city), ("Turlock Library", "Turlock"))
        self.assertEqual(parsed.ages, [8, 99])
        self.assertTrue(parsed.url.startswith("https://engagedpatrons.org/EventsExtended.cfm"))

    def test_clock_ranges(self):
        cases = {
            "3 pm": ((15, 0), None),
            "11:30 am - 12 pm": ((11, 30), (12, 0)),
            "11-1 pm": ((11, 0), (13, 0)),
            "3, 3:30, or 4 pm": ((15, 0), None),
            "": (None, None),
        }
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertEqual(parse_clock_range(text), expected)

    def test_stated_ages(self):
        cases = {
            "This program is designed for ages 9-18.": [9, 18],
            "for children ages 3 to 5.": [3, 5],
            "For ages 8 and up; content suitable for ages 10 and up": [8, 99],
            "toys for ages 5 and under.": [0, 5],
            "Best for 3rd grade and up.": [8, 99],
            "Under 5, but all ages are welcome!": None,
            "Pancakes!": None,
        }
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertEqual(stated_ages(text), expected)

    def facebook_events(self, **changes):
        item = {
            "title": "Family Farm Day", "start": "2026-11-21T10:00:00", "end": "2026-11-21T14:00:00",
            "venue": "Example Farm", "city": "Hilmar", "summary": "Hayrides.",
            "url": "https://www.facebook.com/events/123/", "visibility": "public",
        }
        item.update(changes)
        return parse_facebook(json.dumps({"events": [item]}), {**SOURCE, "default_city": ""})

    def test_facebook_reads_public_event(self):
        parsed = self.facebook_events()[0]
        self.assertEqual((parsed.title, parsed.city), ("Family Farm Day", "Hilmar"))
        self.assertEqual(parsed.url, "https://www.facebook.com/events/123/")
        self.assertEqual(parsed.categories, [CHILDRENS_PROGRAM])

    def test_facebook_never_passes_private_or_unmarked_events(self):
        self.assertEqual(self.facebook_events(visibility="private"), [])
        self.assertEqual(self.facebook_events(visibility=None), [])

    def test_facebook_strips_links_into_groups_and_profiles(self):
        for url in ("https://www.facebook.com/groups/123/posts/456/", "https://m.facebook.com/someone"):
            self.assertEqual(self.facebook_events(url=url)[0].url, "")
        self.assertEqual(self.facebook_events(url="https://farm.example/x")[0].url, "https://farm.example/x")

    def test_facebook_skips_records_with_bad_dates(self):
        self.assertEqual(self.facebook_events(start="this Saturday"), [])
        self.assertEqual(self.facebook_events(start=None), [])

    def test_rejects_non_web_links(self):
        payload = json.dumps({"events": [{
            "title": "x", "start_date": "2026-12-04 19:00:00", "url": "javascript:alert(1)",
        }]})
        self.assertEqual(parse_tribe(payload, SOURCE)[0].url, "")

    def test_barnesnoble_reads_embedded_calendar_for_this_store_only(self):
        day = json.dumps([
            {"eventId": "a", "name": "Storytime", "date": "2026-11-14", "time": "11:00 AM", "storeId": 3585,
             "storeAddress1": "Monte Vista Crossing", "storeAddress2": "2693 Countryside Drive",
             "descriptionText": 'Say "hi"', "isStoryTime": True},
            {"eventId": "b", "name": "Elsewhere", "date": "2026-11-14", "time": "1:00 PM", "storeId": 1},
        ])
        escaped = day.replace("\\", "\\\\").replace('"', '\\"')
        page = f'x\\"dayEvents\\":{escaped}y\\"dayEvents\\":{escaped}z'
        source = {**SOURCE, "url": "https://example.org/store", "store_id": 3585}
        found = parse_barnesnoble(page, source)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].start, datetime(2026, 11, 14, 11, 0))
        self.assertEqual(found[0].summary, 'Say "hi"')
        self.assertEqual(found[0].categories, [CHILDRENS_PROGRAM])

    def test_squarespace_reads_upcoming_events_only(self):
        page = """
        <article class="eventlist-event eventlist-event--past"><a href="/old" class="eventlist-title-link">Old</a></article>
        <article class="eventlist-event eventlist-event--upcoming">
        <h1><a href="/upcoming/camp" class="eventlist-title-link">Camp &amp; Play</a></h1>
        <time class="event-date" datetime="2026-11-27">Friday</time>
        <time class="event-time-localized-start" datetime="2026-11-27">9:00 AM</time>
        <time class="event-time-localized-end" datetime="2026-11-27">4:00 PM</time>
        <li class="eventlist-meta-address event-meta-item">The Museum <a href="http://maps">(map)</a></li>
        </article>"""
        source = {**SOURCE, "url": "https://example.org/upcoming", "kids_only": True}
        found = parse_squarespace(page, source)
        self.assertEqual([item.title for item in found], ["Camp & Play"])
        self.assertEqual(found[0].end, datetime(2026, 11, 27, 16, 0))
        self.assertEqual(found[0].venue, "The Museum")
        self.assertEqual(found[0].url, "https://example.org/upcoming/camp")
        self.assertEqual(found[0].categories, [CHILDRENS_PROGRAM])

    def test_gallo_reads_table_rows(self):
        page = ('<tr><td class="text-nowrap">11/22/2026 5:00 PM</td><td><a href="https://t.example/1">'
                '<b>Fraggle Rock</b></a></td><td class="hidden-xs">Rogers Theater</td></tr>')
        found = parse_gallo(page, SOURCE)[0]
        self.assertEqual((found.title, found.venue), ("Fraggle Rock", "Rogers Theater"))
        self.assertEqual(found.start, datetime(2026, 11, 22, 17, 0))
        self.assertEqual(found.url, "https://t.example/1")

    def test_localist_skips_listed_event_types(self):
        def record(title, kind):
            return {"event": {
                "title": title, "filters": {"event_types": [{"name": kind}]},
                "event_instances": [{"event_instance": {"start": "2026-12-04T19:00:00-08:00", "end": None}}],
                "localist_url": "https://example.org/e",
            }}
        payload = json.dumps({"events": [record("Science Night", "Special Event"), record("Child Club", "Club Event")]})
        found = parse_localist(payload, {**SOURCE, "skip_types": ["Club Event"]})
        self.assertEqual([item.title for item in found], ["Science Night"])
        self.assertEqual(found[0].start, datetime(2026, 12, 4, 19, 0))

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

    def test_childrens_programs_respect_age_limits(self):
        program = {"categories": [CHILDRENS_PROGRAM]}
        self.assertEqual(filters.fit_for_kid(event("Dinosaur Dig", **program)), "good")
        self.assertIsNone(filters.fit_for_kid(event("Coding", ages=[9, 18], **program)))
        self.assertEqual(filters.fit_for_kid(event("Messy Art", ages=[3, 5], **program)), "good")
        for title in ("Turlock Wiggle Worms", "T(w)een Tuesday: Cork Boards",
                      "Friends of the Ceres Library Meeting", "Veterans Day - All Libraries Closed"):
            self.assertIsNone(filters.fit_for_kid(event(title, **program)), title)

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
