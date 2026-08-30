"""Pure rendering helpers shared by the CLI and the MCP server.

Each function performs the fetch and returns the finished text block that a
caller either prints (the CLI) or hands back as tool output (the MCP server).
No printing, no argparse, no ``sys.exit`` — just ``str`` in, ``str`` out — so
the two front ends stay in lockstep without duplicating the conversion logic.
"""

from __future__ import annotations

import json
from typing import List, Literal, NamedTuple, Optional, get_args

from . import convert, links, output, wayback
from .cache import Fetched
from .input import parse_target
from .wayback import WaybackError

MatchScope = Literal["exact", "prefix", "domain"]
MATCH_SCOPES = get_args(MatchScope)

DEFAULT_MATCH: MatchScope = "exact"
DEFAULT_LIST_LIMIT = 50
DEFAULT_LINKS_LIMIT = 100
DEFAULT_MAX_CHARS = 20000
DEFAULT_OFFSET = 0


class Resolved(NamedTuple):
    """Everything ``get``/``links`` need after locating and fetching a capture."""

    orig: str
    requested_display: Optional[str]  # bare digits, compares against served_ts; None => latest
    fetched: Fetched
    served_ts: str


def _resolve_and_fetch(url: str, at: Optional[str]) -> Resolved:
    """Shared by get/links: resolve the capture, fetch it, return everything."""
    orig, embedded_ts = parse_target(url)
    if embedded_ts:
        ts = requested_display = embedded_ts
    else:
        requested_display = wayback.requested_ts(at)
        ts = requested_display or wayback.LATEST_TS
    fetched = wayback.fetch_raw(orig, ts)
    # fetch_raw only returns a real, fetched capture, so its final URL should carry a
    # served timestamp; surface a missing one rather than guess a stamp that would pin
    # every rewritten link to a capture that never existed.
    served_ts = wayback.parse_served_ts(fetched.url)
    if served_ts is None:
        raise WaybackError(
            f"could not read the served capture timestamp from {fetched.url}"
        )
    return Resolved(orig, requested_display, fetched, served_ts)


def render_list(
    url: str,
    *,
    from_: Optional[str],
    to: Optional[str],
    status: Optional[str],
    url_filter: Optional[str],
    mimetype: Optional[str],
    match: MatchScope,
    collapse_day: bool,
    limit: int,
    as_json: bool,
) -> str:
    """List archived captures of ``url`` as a text table (or JSON)."""
    orig, _ = parse_target(url)
    rows = wayback.cdx_search(
        orig,
        from_=from_,
        to=to,
        status=status,
        url_filter=url_filter,
        mimetype=mimetype,
        match=match,
        collapse_day=collapse_day,
        limit=limit,
    )
    if as_json:
        return json.dumps(rows, indent=2)
    if not rows:
        return "no captures found"
    lines = []
    for row in rows:
        ts = row.get("timestamp", "-")
        lines.append(
            f"{ts}  "
            f"{output.human_ts(ts) or '':<23}  "
            f"{str(row.get('statuscode', '-')):>3}  "
            f"{row.get('mimetype', '-'):<24}  "
            f"{row.get('original', '')}"
        )
    footer = f"\n{len(rows)} capture(s)"
    # Span of the returned rows (CDX sorts oldest-first): a dense run vs. captures strung
    # thinly across years reads the same in the count alone.
    first, last = rows[0].get("timestamp"), rows[-1].get("timestamp")
    if first and last and first != last:
        footer += f" spanning {output.human_ts(first[:8])} → {output.human_ts(last[:8])}"
    if len(rows) >= limit:
        footer += " (limit reached; narrow the from/to range or raise the limit)"
    lines.append(footer)
    return "\n".join(lines)


def render_get(
    url: str,
    *,
    at: Optional[str],
    max_chars: int,
    offset: int,
    no_frontmatter: bool,
) -> str:
    """Fetch a snapshot and return it as Markdown, with optional frontmatter."""
    r = _resolve_and_fetch(url, at)
    fetched = r.fetched

    frames: List[str] = []
    refresh: Optional[str] = None
    page_meta: dict = {}
    kind = convert.classify(fetched.mimetype, fetched.url)
    if kind is convert.Kind.HTML:
        rewritten = links.rewrite_urls(fetched.text, r.orig, r.served_ts)
        markdown = convert.html_to_markdown(rewritten)
        frames = links.frame_sources(fetched.text, r.orig, r.served_ts)
        refresh = links.meta_refresh(fetched.text, r.orig, r.served_ts)
        page_meta = links.head_meta(fetched.text)
    elif kind is convert.Kind.TEXT:
        markdown = fetched.text
    elif kind is convert.Kind.DOC:
        ext = convert.doc_ext(fetched.mimetype, fetched.url)
        assert ext is not None  # classify returns DOC only when doc_ext is set
        markdown = convert.doc_to_markdown(fetched.content, ext)
    else:
        markdown = (
            f"[unsupported content: {fetched.mimetype or 'unknown type'}. "
            f"Fetch the raw asset directly at {fetched.url}]"
        )

    meta = output.Meta(
        requested_url=r.orig,
        requested_ts=r.requested_display,
        served_ts=r.served_ts,
        final_url=fetched.url,
        status=fetched.status,
        redirect_history=fetched.redirect_history,
        mimetype=fetched.mimetype,
        total_chars=len(markdown),
        frames=frames,
        refresh=refresh,
        title=page_meta.get("title"),
        description=page_meta.get("description"),
        keywords=page_meta.get("keywords"),
        author=page_meta.get("author"),
    )
    chunk, info = output.truncate(markdown, max_chars, offset)

    parts: List[str] = []
    if not no_frontmatter:
        parts.append(output.metadata_frontmatter(meta))
        parts.append("")
    parts.append(chunk if chunk else "[empty document]")
    if info.truncated:
        parts.append(output.truncation_marker(info))
    return "\n".join(parts)


def render_links(
    url: str,
    *,
    at: Optional[str],
    limit: int,
    internal_only: bool,
    as_json: bool,
) -> str:
    """List a snapshot's outbound links as text (or JSON)."""
    r = _resolve_and_fetch(url, at)
    fetched = r.fetched

    kind = convert.classify(fetched.mimetype, fetched.url)
    if kind is not convert.Kind.HTML:
        return f"no links: content is {fetched.mimetype or 'non-HTML'}"

    found = links.extract(
        fetched.text,
        r.orig,
        r.served_ts,
        internal_only=internal_only,
        limit=limit,
    )
    if as_json:
        return json.dumps(found, indent=2)
    if not found:
        return "no links found"
    lines = []
    for link in found:
        label = link["text"] or "(no text)"
        lines.append(f"{link['archived']}\n    {label}")
    lines.append(f"\n{len(found)} link(s)")
    return "\n".join(lines)
