"""Internet Archive client: CDX listing, timestamp resolution, raw snapshot fetch.

All network access goes through :mod:`wayback_markdown.cache`, so identical requests are
served from disk. A snapshot pinned to a full timestamp is cached forever (that capture
is immutable); everything whose answer drifts as new captures arrive — CDX lookups, and
snapshot requests for a prefix or the latest sentinel — uses a TTL.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from . import __version__, cache
from .cache import Fetched

WEB_BASE = "https://web.archive.org/web"
CDX_URL = "https://web.archive.org/cdx/search/cdx"

USER_AGENT = (
    f"wayback-markdown/{__version__} "
    "(+https://github.com/ertug/wayback-markdown; agent CLI)"
)
# Wayback is slow to connect and to stream, so split the phases with a generous read.
HTTP_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=10.0, pool=10.0)
DEFAULT_TTL = 24 * 3600.0

# Far-future sentinel: Wayback redirects `/web/<ts>id_/<url>` to the nearest capture, so
# requesting this stamp resolves to the latest one server-side (no availability lookup).
LATEST_TS = "29991231235959"


class WaybackError(Exception):
    """Base class for expected, user-facing errors."""


class NoSnapshotError(WaybackError):
    def __init__(self, url: str, detail: str = ""):
        super().__init__(f"No archived snapshot found for: {url}{detail}")
        self.url = url


def normalize_ts(value: Optional[str]) -> str:
    """Reduce a date-ish string ('2010', '2010-01-02', a full ts) to bare digits."""
    return re.sub(r"\D", "", value or "")


def requested_ts(at: Optional[str] = None) -> Optional[str]:
    """The user's requested timestamp as bare digits, or None for 'latest'.

    This is what the user *asked for* (for display/comparison), distinct from the
    precise capture Wayback settles on, read back via :func:`parse_served_ts`.
    """
    return normalize_ts(at) or None


def snapshot_url(orig_url: str, timestamp: str) -> str:
    """Build a toolbar-free archive request URL, e.g. .../web/20100101id_/https://….

    No network: Wayback redirects ``/web/<ts>id_/<url>`` to the nearest capture, so a
    bare year, a full 14-digit stamp, or the :data:`LATEST_TS` sentinel all resolve to a
    real capture server-side — the served timestamp is then read back from the redirect
    target via :func:`parse_served_ts`.
    """
    return f"{WEB_BASE}/{timestamp}id_/{orig_url}"


# The one place that knows the `/web/<ts><modifier>/<orig>` archive-URL layout.
_ARCHIVE_URL_RE = re.compile(
    r"^https?://web\.archive\.org/web/"
    r"(?P<ts>\d{1,14})"
    r"(?:[a-z]{2}_)?"  # optional modifier: id_, if_, im_, cs_, js_, ...
    r"/(?P<orig>.+)$",
    re.IGNORECASE,
)


def split_archive_url(url: str) -> Optional[Tuple[str, str]]:
    """Split a web.archive.org snapshot URL into ``(orig_url, timestamp)``, or None.

    Used both to parse a full archive URL a user pastes back in and to read the served
    timestamp out of a fetched response URL, so the layout lives in exactly one regex.
    """
    m = _ARCHIVE_URL_RE.match((url or "").strip())
    return (m.group("orig"), m.group("ts")) if m else None


def parse_served_ts(archive_url: str) -> Optional[str]:
    """Extract the served timestamp from a final web.archive.org URL, if present."""
    parts = split_archive_url(archive_url)
    return parts[1] if parts else None


def _http_get(request_url: str) -> Fetched:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(
        follow_redirects=True, timeout=HTTP_TIMEOUT, headers=headers
    ) as client:
        resp = client.get(request_url)
    mimetype = resp.headers.get("content-type", "").split(";")[0].strip()
    history = [str(h.url) for h in resp.history]
    return Fetched(
        url=str(resp.url),
        status=resp.status_code,
        mimetype=mimetype,
        charset=resp.charset_encoding or "",
        content=resp.content,
        redirect_history=history,
    )


CDX_FIELDS = ["timestamp", "original", "mimetype", "statuscode", "digest", "length"]


def cdx_search(
    orig_url: str,
    *,
    from_: Optional[str] = None,
    to: Optional[str] = None,
    status: Optional[str] = None,
    match: str = "exact",
    collapse_day: bool = True,
    limit: int = 50,
    directory: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Query the CDX API and return a list of capture rows as dicts."""
    params = [
        ("url", orig_url),
        ("output", "json"),
        ("fl", ",".join(CDX_FIELDS)),
        ("limit", str(limit)),
        ("matchType", match),
    ]
    if from_:
        params.append(("from", normalize_ts(from_)))
    if to:
        params.append(("to", normalize_ts(to)))
    if status:
        params.append(("filter", f"statuscode:{status}"))
    if collapse_day:
        params.append(("collapse", "timestamp:8"))

    request_url = f"{CDX_URL}?{urlencode(params)}"
    fetched = cache.get_or_fetch(
        request_url, lambda: _http_get(request_url), ttl=DEFAULT_TTL, directory=directory
    )
    # An error page would otherwise json-parse to an affirmative "no captures found".
    if fetched.status >= 400:
        raise WaybackError(f"CDX API returned HTTP {fetched.status}; try again later")
    return parse_cdx(fetched.text)


def parse_cdx(text: str) -> List[Dict[str, str]]:
    """Turn CDX JSON (header row + data rows) into a list of dicts."""
    text = text.strip()
    if not text:
        return []
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    if not rows:
        return []
    header, data = rows[0], rows[1:]
    return [dict(zip(header, row)) for row in data]


def fetch_raw(orig_url: str, timestamp: str, directory: Optional[str] = None) -> Fetched:
    """Fetch the toolbar-free ('id_') snapshot, following redirects, via the cache.

    Wayback redirects the requested timestamp to the nearest real capture; the served
    timestamp on the final URL is the precise stamp of that capture. Only a full pinned
    stamp is cached forever: a prefix or the latest sentinel resolves to "nearest",
    which drifts as new captures arrive, so those expire. Two failure modes:

    * No redirect happened and we got a 404 — the URL isn't archived at that address
      (or, for a full pinned stamp, the capture itself recorded a 404 — the message hedges).
    * We landed on a real capture whose crawl-time status was an error — surface it, so
      an archive error page is never handed downstream and converted as if it were the page.
    """
    ts_digits = normalize_ts(timestamp)
    pinned = len(ts_digits) == 14 and ts_digits != LATEST_TS
    request_url = snapshot_url(orig_url, timestamp)
    fetched = cache.get_or_fetch(
        request_url,
        lambda: _http_get(request_url),
        ttl=None if pinned else DEFAULT_TTL,
        directory=directory,
    )
    if fetched.status >= 400:
        served_ts = parse_served_ts(fetched.url)
        if fetched.status == 404 and served_ts == ts_digits:
            detail = (
                " (or the capture at exactly this timestamp was itself a 404)"
                if pinned
                else ""
            )
            raise NoSnapshotError(orig_url, detail)
        raise WaybackError(
            f"archive returned HTTP {fetched.status} for capture "
            f"{served_ts or timestamp} of {orig_url}"
        )
    return fetched
