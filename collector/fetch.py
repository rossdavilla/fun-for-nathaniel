"""Polite HTTP: honors each site's robots.txt and pauses between requests."""

import functools
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "FamilyEventsCalendar/0.1 (personal weekly calendar)"
TIMEOUT_SECONDS = 30
MIN_DELAY_SECONDS = 2.0
MACOS_ROOT_KEYCHAIN = "/System/Library/Keychains/SystemRootCertificates.keychain"


class RobotsBlocked(Exception):
    """The site's robots.txt does not allow this URL, or could not be read."""


@functools.cache
def _tls_context():
    """Verify HTTPS normally; python.org's macOS builds ship with no root certificates."""
    context = ssl.create_default_context()
    if sys.platform == "darwin" and not context.get_ca_certs():
        roots = subprocess.run(
            ["security", "find-certificate", "-a", "-p", MACOS_ROOT_KEYCHAIN],
            capture_output=True, text=True, check=True,
        ).stdout
        context.load_verify_locations(cadata=roots)
    return context


def _download(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=_tls_context()) as response:
        return response.read()


class Fetcher:
    def __init__(self, download=_download, sleep=time.sleep, clock=time.monotonic):
        self._download = download
        self._sleep = sleep
        self._clock = clock
        self._rules = {}
        self._last_request = {}

    def _rules_for(self, origin):
        if origin not in self._rules:
            parser = RobotFileParser()
            try:
                text = self._download(origin + "/robots.txt")
            except urllib.error.HTTPError as error:
                # RFC 9309: a 4xx means no rules apply; a 5xx means assume blocked.
                if not 400 <= error.code < 500:
                    raise RobotsBlocked(f"robots.txt returned {error.code}") from error
                parser.allow_all = True
            except urllib.error.URLError as error:
                raise RobotsBlocked(f"robots.txt unreachable: {error.reason}") from error
            else:
                parser.parse(text.decode("utf-8", "replace").splitlines())
            self._rules[origin] = parser
        return self._rules[origin]

    def _wait_turn(self, origin, rules):
        delay = max(MIN_DELAY_SECONDS, rules.crawl_delay(USER_AGENT) or 0)
        last = self._last_request.get(origin)
        if last is not None:
            remaining = delay - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[origin] = self._clock()

    def get(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rules = self._rules_for(origin)
        if not rules.can_fetch(USER_AGENT, url):
            raise RobotsBlocked(f"robots.txt disallows {url}")
        self._wait_turn(origin, rules)
        return self._download(url)
