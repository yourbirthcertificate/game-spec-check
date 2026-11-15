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

* GitHub's REST API allows 60 unauthenticated requests per hour from one IP
  address (GitHub documents this). The update check sends one request a day at
  most, plus one each time the person asks. GitHub reports a used-up limit as
  HTTP 403 with X-RateLimit-Remaining: 0, or as 403 or 429 with Retry-After,
  and those count as rate limiting too.

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

    def remaining_pause(self):
        """Seconds until this host may be asked again after a rate limit."""
        with self._lock:
            return max(0.0, self._paused_until - _clock())

    def reset(self):
        with self._lock:
            self._next = self._paused_until = 0.0


STEAM = Host("Steam", min_interval=1.5, backoff=10.0)
PCGW = Host("PCGamingWiki", min_interval=0.4, backoff=5.0)
# Not retried: the check is optional, and a page should not wait on it.
GITHUB = Host("GitHub", min_interval=2.0, backoff=60.0, max_retries=0)


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


def _rate_reset_seconds(headers, now=None):
    """Seconds until X-RateLimit-Reset (a Unix time), or None."""
    value = str((headers or {}).get("X-RateLimit-Reset") or "").strip()
    if not value.isdigit():
        return None
    now = time.time() if now is None else now
    return max(0.0, float(value) - now)


def _wait_words(seconds):
    seconds = max(1, round(seconds))
    if seconds == 1:
        return "about 1 second"
    if seconds < 120:
        return "about %d seconds" % seconds
    return "about %d minutes" % round(seconds / 60.0)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class _NoDowngrade(urllib.request.HTTPRedirectHandler):
    """Follows redirects, but never from https to plain http."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.full_url.lower().startswith("https:") and \
                not newurl.lower().startswith("https:"):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urllib_transport(url, timeout, follow):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "en"})
    opener = urllib.request.build_opener(_NoDowngrade if follow else _NoRedirect)
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
                headers = e.headers or {}
                retry = retry_after_seconds(headers.get("Retry-After"))
                used_up = e.code == 403 and (
                    str(headers.get("X-RateLimit-Remaining", "")).strip() == "0"
                    or retry is not None)
                if e.code not in (429, 503) and not used_up:
                    raise SourceError("%s answered HTTP %d." % (host.name, e.code))
                wait = retry
                if wait is None and used_up:
                    wait = _rate_reset_seconds(headers)
                if wait is None:
                    wait = host.backoff * (2 ** attempt)
                host.pause(wait)
                if attempt >= host.max_retries or wait > host.max_wait:
                    raise RateLimited(
                        "%s is limiting requests right now. Try again in %s."
                        % (host.name, _wait_words(wait)), wait)
                attempt += 1
            except (urllib.error.URLError, OSError) as e:
                raise SourceError("Could not reach %s (%s)."
                                  % (host.name, getattr(e, "reason", e)))
