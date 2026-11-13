"""Outbound HTTP for requirement lookups, with pacing and rate-limit handling.

Every request to Steam or PCGamingWiki goes through `fetch`, so the request
budget lives in one place:

* Steam's store endpoints (storesearch, appdetails) are undocumented and Valve
  publishes no limit for them. The figure developers usually quote, about 200
  requests per five minutes, comes from a 2015 SteamDB blog post, not from
  Valve. Requests are sent one at a time, at most one every 1.5 seconds, which
  stays at or below that figure, and an HTTP 429 pauses all Steam requests.
* PCGamingWiki runs MediaWiki. MediaWiki's API etiquette asks clients to send
  requests in series rather than in parallel and to honour Retry-After. One
  request at a time, a short gap between them.

A 429 or 503 answer pauses that host for the Retry-After time when the server
gives one, otherwise for an exponential backoff, and the request is retried a
couple of times before the error is reported. Times use a monotonic clock so a
change of the system clock cannot stall or speed up requests.
"""
import email.utils
import threading
import time
import urllib.error
import urllib.request

from . import __version__

USER_AGENT = "GameSpecCheck/%s (local desktop tool)" % __version__

# Swappable for tests.
_clock = time.monotonic
_sleep = time.sleep


class SourceError(Exception):
    pass


class RateLimited(SourceError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class Host:
    """Request budget for one upstream service."""

    def __init__(self, name, min_interval, concurrency=1, backoff=5.0,
                 max_retries=2, max_wait=60.0):
        self.name = name
        self.min_interval = min_interval
        self.backoff = backoff
        self.max_retries = max_retries
        self.max_wait = max_wait
        self._slots = threading.BoundedSemaphore(concurrency)
        self._lock = threading.Lock()
        self._next = 0.0
        self._paused_until = 0.0

    def _wait_turn(self):
        with self._lock:
            now = _clock()
            start = max(now, self._next, self._paused_until)
            self._next = start + self.min_interval
        if start > now:
            _sleep(start - now)

    def pause(self, seconds):
        with self._lock:
            self._paused_until = max(self._paused_until, _clock() + seconds)

    def reset(self):
        with self._lock:
            self._next = self._paused_until = 0.0


STEAM = Host("Steam", min_interval=1.5, backoff=10.0)
PCGW = Host("PCGamingWiki", min_interval=0.4, backoff=5.0)


def retry_after_seconds(value, now=None):
    """Seconds from a Retry-After header (a number or an HTTP date), or None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    now = time.time() if now is None else now
    return max(0.0, when.timestamp() - now)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _urllib_transport(url, timeout, follow):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "en"})
    opener = urllib.request.build_opener() if follow else \
        urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.headers, resp.read()


_transport = _urllib_transport


def fetch(host, url, timeout=20, follow=True):
    """GET `url` within `host`'s budget. Returns (status, headers, body)."""
    attempt = 0
    while True:
        with host._slots:
            host._wait_turn()
            try:
                return _transport(url, timeout, follow)
            except urllib.error.HTTPError as e:
                if not follow and e.code in (301, 302, 303, 307, 308):
                    return e.code, e.headers, b""
                if e.code not in (429, 503):
                    raise SourceError("%s answered HTTP %d." % (host.name, e.code))
                wait = retry_after_seconds((e.headers or {}).get("Retry-After"))
                if wait is None:
                    wait = host.backoff * (2 ** attempt)
                host.pause(wait)
                if attempt >= host.max_retries or wait > host.max_wait:
                    raise RateLimited(
                        "%s is limiting requests right now. Try again in about %d seconds."
                        % (host.name, max(1, round(wait))), wait)
                attempt += 1
            except (urllib.error.URLError, OSError) as e:
                raise SourceError("Could not reach %s (%s)."
                                  % (host.name, getattr(e, "reason", e)))
