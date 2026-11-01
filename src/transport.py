"""Fetch layer. `Transport` is the only thing that touches the network; `Fetcher` adds the
manners on top of it - robots.txt, per-domain spacing, retries with exponential backoff and a
timeout - so the same policy is exercised by the tests through `FakeTransport`."""
import time, urllib.error, urllib.request, urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

MAX_BYTES = 5 * 1024 * 1024
RETRY_STATUSES = (429, 500, 502, 503, 504)

@dataclass
class Response:
    status: int
    text: str
    url: str = ""
    headers: dict = field(default_factory=dict)

class TransportError(Exception):
    """The request never produced an HTTP response (DNS, refused, reset, timeout)."""

class Transport:
    def get(self, url, headers, timeout):
        raise NotImplementedError

class UrllibTransport(Transport):
    """Standard library only. HTTP error statuses come back as a Response, not an exception."""

    def get(self, url, headers, timeout):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return self._response(resp, resp.status, resp.geturl())
        except urllib.error.HTTPError as e:
            try:
                return self._response(e, e.code, url)
            finally:
                e.close()
        except (urllib.error.URLError, OSError) as e:
            raise TransportError(f"{type(e).__name__}: {e}") from e

    @staticmethod
    def _response(resp, status, final_url):
        body = resp.read(MAX_BYTES)
        charset = resp.headers.get_content_charset() or "utf-8"
        return Response(status, body.decode(charset, "replace"), final_url, dict(resp.headers.items()))

class FakeTransport(Transport):
    """Routes are url -> Response | Exception | [those, consumed in order; the last one repeats].
    An unknown url is a 404. Every call is recorded with the clock time it was made at."""

    def __init__(self, routes=None, clock=None):
        self.routes = dict(routes or {})
        self.clock = clock
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout,
                           "at": self.clock() if self.clock else None})
        r = self.routes.get(url, Response(404, "", url))
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, BaseException):
            raise r
        return r

class FakeClock:
    """Monotonic time that only moves when someone sleeps on it."""

    def __init__(self, t=0.0):
        self.t = t
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

@dataclass
class Fetched:
    url: str
    response: Response = None
    error: str = ""
    robots_disallowed: bool = False
    attempts: int = 0

    @property
    def ok(self):
        return self.response is not None and 200 <= self.response.status < 300

class Fetcher:
    def __init__(self, transport, user_agent, interval=2.0, timeout=30.0, retries=3,
                 backoff=1.0, max_backoff=60.0, respect_robots=True,
                 clock=time.monotonic, sleep=time.sleep):
        self.transport, self.user_agent = transport, user_agent
        self.interval, self.timeout = interval, timeout
        self.retries, self.backoff, self.max_backoff = retries, backoff, max_backoff
        self.respect_robots = respect_robots
        self.clock, self.sleep = clock, sleep
        self._last = {}     # netloc -> clock time of the previous request
        self._robots = {}   # scheme://netloc -> RobotFileParser, or a reason string if unusable

    def _wait_turn(self, netloc):
        prev = self._last.get(netloc)
        if prev is not None:
            wait = prev + self.interval - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last[netloc] = self.clock()

    def _get(self, url):
        """One request with retries. Returns (Response or None, error, attempts)."""
        netloc = urlsplit(url).netloc.lower()
        headers = {"User-Agent": self.user_agent}
        resp, err = None, ""
        for attempt in range(self.retries + 1):
            if attempt:
                delay = min(self.max_backoff, self.backoff * 2 ** (attempt - 1))
                retry_after = (resp.headers.get("Retry-After") if resp else None) or ""
                if retry_after.strip().isdigit():
                    delay = min(self.max_backoff, max(delay, float(retry_after)))
                self.sleep(delay)
            self._wait_turn(netloc)
            try:
                resp, err = self.transport.get(url, headers, self.timeout), ""
            except TransportError as e:
                resp, err = None, str(e)
                continue
            if resp.status not in RETRY_STATUSES:
                return resp, "", attempt + 1
            err = f"HTTP {resp.status}"
        return resp, err, self.retries + 1

    def _robots_for(self, url):
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc.lower()}"
        if key not in self._robots:
            resp, err, _ = self._get(key + "/robots.txt")
            rp = urllib.robotparser.RobotFileParser()
            if resp is not None and 200 <= resp.status < 300:
                rp.parse(resp.text.splitlines())
            elif resp is not None and resp.status in (401, 403):
                rp.disallow_all = True
            elif resp is not None and 400 <= resp.status < 500:
                rp.allow_all = True
            else:
                # Could not read the rules at all. Not knowing is not permission.
                rp = f"robots.txt unavailable ({err or 'HTTP %s' % resp.status})"
            self._robots[key] = rp
        return self._robots[key]

    def fetch(self, url):
        if self.respect_robots:
            rp = self._robots_for(url)
            if isinstance(rp, str):
                return Fetched(url, error=rp, robots_disallowed=True)
            if not rp.can_fetch(self.user_agent, url):
                return Fetched(url, error="disallowed by robots.txt", robots_disallowed=True)
        resp, err, attempts = self._get(url)
        if resp is not None and not err and not 200 <= resp.status < 300:
            err = f"HTTP {resp.status}"
        return Fetched(url, resp, err, False, attempts)
