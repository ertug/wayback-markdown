"""Dead-simple on-disk cache for archive requests.

Every HTTP call to the Internet Archive routes through :func:`get_or_fetch`, keyed by
the exact request URL. Snapshot fetches (an immutable capture) are cached forever;
CDX lookups pass a TTL so their drifting "latest" answer expires.

The cache lives in ``/tmp/wayback-markdown-cache`` by default (ephemeral, and snapshots are
always re-fetchable), overridable via ``$WAYBACK_MARKDOWN_CACHE`` or an explicit ``cache_dir``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from bs4 import UnicodeDammit

DEFAULT_CACHE_DIR = "/tmp/wayback-markdown-cache"


@dataclass
class Fetched:
    """The result of one archive HTTP request, in a cache-friendly shape."""

    url: str
    status: int
    mimetype: str
    content: bytes
    redirect_history: List[str] = field(default_factory=list)
    saved_at: float = 0.0  # unix time the entry was written (0 => live, not from cache)

    @property
    def text(self) -> str:
        """Decode the body by its charset. Legacy captures are often Windows-1252/Latin-1,
        not UTF-8; ``UnicodeDammit`` honours a ``<meta charset>``/BOM declaration and only
        sniffs as a fallback, so a byte like ``®`` (0xAE) never becomes a replacement char."""
        if not self.content:
            return ""
        return UnicodeDammit(self.content).unicode_markup or self.content.decode(
            "utf-8", errors="replace"
        )


def cache_dir(explicit: Optional[str] = None) -> Path:
    path = Path(explicit or os.environ.get("WAYBACK_MARKDOWN_CACHE") or DEFAULT_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paths(directory: Path, request_url: str):
    key = hashlib.sha256(request_url.encode("utf-8")).hexdigest()
    return directory / f"{key}.json", directory / f"{key}.body"


def _load(meta_path: Path, body_path: Path) -> Optional[Fetched]:
    if not meta_path.exists() or not body_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
        return Fetched(
            url=meta["url"],
            status=meta["status"],
            mimetype=meta["mimetype"],
            redirect_history=meta.get("redirect_history", []),
            saved_at=meta.get("saved_at", 0.0),
            content=body_path.read_bytes(),
        )
    except (OSError, ValueError, KeyError):
        return None


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file + rename so a concurrent reader never sees a torn file."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _store(meta_path: Path, body_path: Path, fetched: Fetched) -> None:
    # Serialize saved_at into the stored meta only; never mutate the caller's object,
    # which is returned live and keeps saved_at=0 ("not from cache").
    meta = {k: v for k, v in asdict(fetched).items() if k != "content"}
    meta["saved_at"] = time.time()
    try:
        # body first: _load requires both files, so meta only becomes visible last.
        _atomic_write(body_path, fetched.content)
        _atomic_write(meta_path, json.dumps(meta).encode("utf-8"))
    except OSError:
        pass  # cache is best-effort; a write failure must not break the command


def get_or_fetch(
    request_url: str,
    fetch_fn: Callable[[], Fetched],
    *,
    ttl: Optional[float] = None,
    directory: Optional[str] = None,
) -> Fetched:
    """Return a cached :class:`Fetched` for ``request_url`` or produce one via ``fetch_fn``.

    ``ttl`` in seconds: ``None`` caches indefinitely (immutable snapshots); a number
    expires entries older than it (CDX/availability). A fresh fetch is always stored.
    """
    directory_path = cache_dir(directory)
    meta_path, body_path = _paths(directory_path, request_url)

    cached = _load(meta_path, body_path)
    if cached is not None:
        if ttl is None or (time.time() - cached.saved_at) < ttl:
            return cached

    fetched = fetch_fn()
    if fetched.status < 400:
        # Never persist an error response: with ttl=None a transient 429/503 would
        # otherwise poison the cache forever. Errors stay live so a retry re-fetches.
        _store(meta_path, body_path, fetched)
    return fetched
