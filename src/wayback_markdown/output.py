"""Rendering helpers: metadata frontmatter, char truncation, human/JSON output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Long free-text meta values (keywords stuffing, especially) are capped in the
# frontmatter so a single tag can't drown out the signal.
_META_CAP = 500


def human_ts(ts: Optional[str]) -> Optional[str]:
    """Render a bare-digit Wayback stamp (a prefix of ``YYYYMMDDhhmmss``) with
    separators, e.g. ``'20010206202714'`` -> ``'2001-02-06 20:27:14 UTC'``.

    Only the precision actually present is shown, so a coarse ``'2010'`` stays
    ``'2010'`` rather than inventing a month and day. Returns ``None`` for
    anything without at least a 4-digit year.
    """
    digits = re.sub(r"\D", "", ts or "")
    if len(digits) < 4:
        return None
    date = "-".join(p for p in (digits[0:4], digits[4:6], digits[6:8]) if p)
    clock = ":".join(p for p in (digits[8:10], digits[10:12], digits[12:14]) if p)
    return f"{date} {clock} UTC" if clock else date


def _stamp_line(value: Optional[str], empty: str) -> str:
    """A timestamp for the frontmatter: raw stamp plus its human rendering, or a fallback."""
    if not value:
        return empty
    human = human_ts(value)
    return f"{value} ({human})" if human else value


@dataclass
class TruncInfo:
    offset: int
    end: int
    total: int
    truncated: bool


def truncate(text: str, max_chars: int, offset: int = 0) -> Tuple[str, TruncInfo]:
    """Return ``text[offset:offset+max_chars]`` plus info for the caller's marker."""
    total = len(text)
    offset = max(0, min(offset, total))
    end = total if max_chars <= 0 else min(offset + max_chars, total)
    chunk = text[offset:end]
    return chunk, TruncInfo(offset=offset, end=end, total=total, truncated=end < total)


def truncation_marker(info: TruncInfo) -> str:
    return (
        f"\n\n[truncated: showing chars {info.offset}-{info.end} of {info.total} total. "
        f"Re-run with --offset {info.end} for more.]"
    )


@dataclass
class Meta:
    requested_url: str
    requested_ts: Optional[str]
    served_ts: Optional[str]
    final_url: str
    status: int
    redirect_history: List[str]
    mimetype: str
    total_chars: int
    frames: List[str] = field(default_factory=list)
    refresh: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None
    author: Optional[str] = None

    @property
    def served_differs(self) -> bool:
        if not self.requested_ts or not self.served_ts:
            return False
        # requested may be a prefix (e.g. '2010'); a differing prefix means a shift.
        return not self.served_ts.startswith(self.requested_ts)


def metadata_frontmatter(meta: Meta) -> str:
    # Rendered as YAML frontmatter (``---`` fences) so the metadata block is
    # unambiguously delimited from the Markdown body that follows.
    lines = [
        "---",
        f"requested-url: {meta.requested_url}",
        f"requested-timestamp: {_stamp_line(meta.requested_ts, 'latest')}",
        f"served-timestamp: {_stamp_line(meta.served_ts, 'unknown')}",
    ]
    if meta.served_differs:
        lines.append(
            "timestamp-note: no capture at the requested time; served the nearest one"
        )
    lines.append(f"final-url: {meta.final_url}")
    lines.append(f"http-status: {meta.status}")
    if meta.redirect_history:
        lines.append("redirects:")
        lines.extend(f"  - {url}" for url in meta.redirect_history)
    if meta.refresh:
        lines.append(f"meta-refresh: {meta.refresh}")
    if meta.frames:
        lines.append("frames:")
        lines.extend(f"  - {url}" for url in meta.frames)
    lines.append(f"mimetype: {meta.mimetype or 'unknown'}")
    if meta.title:
        lines.append(f"title: {_meta_scalar(meta.title)}")
    if meta.description:
        lines.append(f"description: {_meta_scalar(meta.description)}")
    if meta.keywords:
        lines.append(f"keywords: {_meta_scalar(meta.keywords)}")
    if meta.author:
        lines.append(f"author: {_meta_scalar(meta.author)}")
    lines.append(f"markdown-chars: {meta.total_chars}")
    lines.append("---")
    return "\n".join(lines)


def _meta_scalar(value: str) -> str:
    """A capped, double-quoted YAML scalar for free-text meta content.

    ``json.dumps`` yields a valid double-quoted YAML flow scalar, so colons,
    quotes, and newlines in the content can't break the frontmatter.
    """
    if len(value) > _META_CAP:
        value = value[: _META_CAP - 1] + "…"
    return json.dumps(value, ensure_ascii=False)
