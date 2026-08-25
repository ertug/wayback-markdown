"""Parse a user/agent-supplied target into (original_url, timestamp).

Accepts either a bare URL (``example.com/page``, ``https://example.com/page``) or a
full Wayback archive URL (``https://web.archive.org/web/<ts>[modifier]/<orig>``). The
latter is what our own ``get``/``links`` output emits, so the agent can copy a link
straight back in.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from .wayback import split_archive_url


def _ensure_scheme(url: str) -> str:
    """Give a bare host/path a scheme so httpx and urljoin behave."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        return url
    return "https://" + url


def parse_target(s: str) -> Tuple[str, Optional[str]]:
    """Return ``(original_url, timestamp_or_None)``.

    If ``s`` is a Wayback archive URL, the embedded timestamp is returned; otherwise
    the timestamp is ``None`` and the caller resolves one (``--at``/latest).
    """
    s = s.strip()
    parts = split_archive_url(s)
    if parts:
        orig, ts = parts
        return _ensure_scheme(orig), ts
    return _ensure_scheme(s), None
