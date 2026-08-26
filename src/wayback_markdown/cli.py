"""Command-line entry point: ``wayback-markdown list|get|links``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, NamedTuple, Optional

import httpx

from . import convert, links, output, wayback
from .cache import Fetched
from .input import parse_target
from .wayback import WaybackError


def _add_common(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("url", help="a bare URL or a full web.archive.org snapshot URL")
    sub.add_argument(
        "--cache-dir",
        default=None,
        help="cache directory (default $WAYBACK_MARKDOWN_CACHE or /tmp/wayback-markdown-cache)",
    )


def _add_json(sub: argparse.ArgumentParser) -> None:
    # Only for subcommands whose payload is structured records (list rows, link
    # tuples). `get` returns Markdown, which JSON would only wrap as a string.
    sub.add_argument("--json", action="store_true", help="emit JSON instead of text")


def _add_ts_selection(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--at",
        metavar="YYYY[MMDDhhmmss]",
        help="a timestamp prefix of any length; the closest capture is served. "
        "e.g. 2010, 201002, 20100210, or a full 20100210120000 (default: latest)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wayback-markdown",
        description="Fetch Wayback Machine snapshots as agent-friendly Markdown.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="list archived captures of a URL")
    _add_common(p_list)
    _add_json(p_list)
    p_list.add_argument("--from", dest="from_", help="earliest date (e.g. 2010)")
    p_list.add_argument("--to", help="latest date (e.g. 2011)")
    p_list.add_argument("--status", help="filter by HTTP status code, e.g. 200")
    p_list.add_argument(
        "--match",
        choices=["exact", "prefix", "domain"],
        default="exact",
        help="URL match scope (default: exact)",
    )
    p_list.add_argument(
        "--no-collapse",
        action="store_true",
        help="show every capture instead of one per day",
    )
    p_list.add_argument("--limit", type=int, default=50, help="max rows (default 50)")
    p_list.set_defaults(func=cmd_list)

    p_get = subparsers.add_parser("get", help="fetch a snapshot as Markdown")
    _add_common(p_get)
    _add_ts_selection(p_get)
    p_get.add_argument(
        "--max-chars",
        type=int,
        default=20000,
        help="max Markdown characters to return (0 = no limit; default 20000)",
    )
    p_get.add_argument(
        "--offset", type=int, default=0, help="start character offset (default 0)"
    )
    p_get.add_argument(
        "--no-frontmatter",
        action="store_true",
        help="omit the metadata frontmatter; print only the Markdown body",
    )
    p_get.set_defaults(func=cmd_get)

    p_links = subparsers.add_parser("links", help="list a snapshot's outbound links")
    _add_common(p_links)
    _add_json(p_links)
    _add_ts_selection(p_links)
    p_links.add_argument(
        "--limit", type=int, default=100, help="max links (default 100)"
    )
    p_links.add_argument(
        "--internal-only",
        action="store_true",
        help="only links on the same host as the page",
    )
    p_links.set_defaults(func=cmd_links)

    return parser


class Resolved(NamedTuple):
    """Everything ``get``/``links`` need after locating and fetching a capture."""

    orig: str
    requested_display: Optional[str]  # bare digits, compares against served_ts; None => latest
    fetched: Fetched
    served_ts: str


def _resolve_and_fetch(args) -> Resolved:
    """Shared by get/links: resolve the capture, fetch it, return everything."""
    orig, embedded_ts = parse_target(args.url)
    if embedded_ts:
        ts = requested_display = embedded_ts
    else:
        requested_display = wayback.requested_ts(args.at)
        ts = requested_display or wayback.LATEST_TS
    fetched = wayback.fetch_raw(orig, ts, directory=args.cache_dir)
    # fetch_raw only returns a real, fetched capture, so its final URL should carry a
    # served timestamp; surface a missing one rather than guess a stamp that would pin
    # every rewritten link to a capture that never existed.
    served_ts = wayback.parse_served_ts(fetched.url)
    if served_ts is None:
        raise WaybackError(
            f"could not read the served capture timestamp from {fetched.url}"
        )
    return Resolved(orig, requested_display, fetched, served_ts)


def cmd_list(args) -> int:
    orig, _ = parse_target(args.url)
    rows = wayback.cdx_search(
        orig,
        from_=args.from_,
        to=args.to,
        status=args.status,
        match=args.match,
        collapse_day=not args.no_collapse,
        limit=args.limit,
        directory=args.cache_dir,
    )
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no captures found")
        return 0
    for row in rows:
        ts = row.get("timestamp", "-")
        print(
            f"{ts}  "
            f"{output.human_ts(ts) or '':<23}  "
            f"{str(row.get('statuscode', '-')):>3}  "
            f"{row.get('mimetype', '-'):<24}  "
            f"{row.get('original', '')}"
        )
    footer = f"\n{len(rows)} capture(s)"
    if len(rows) >= args.limit:
        footer += " (limit reached; narrow with --from/--to or raise --limit)"
    print(footer)
    return 0


def cmd_get(args) -> int:
    r = _resolve_and_fetch(args)
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
    chunk, info = output.truncate(markdown, args.max_chars, args.offset)

    if not args.no_frontmatter:
        print(output.metadata_frontmatter(meta))
        print()
    print(chunk if chunk else "[empty document]")
    if info.truncated:
        print(output.truncation_marker(info))
    return 0


def cmd_links(args) -> int:
    r = _resolve_and_fetch(args)
    fetched = r.fetched

    kind = convert.classify(fetched.mimetype, fetched.url)
    if kind is not convert.Kind.HTML:
        print(f"no links: content is {fetched.mimetype or 'non-HTML'}", file=sys.stderr)
        return 0

    found = links.extract(
        fetched.text,
        r.orig,
        r.served_ts,
        internal_only=args.internal_only,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(found, indent=2))
        return 0
    if not found:
        print("no links found")
        return 0
    for link in found:
        label = link["text"] or "(no text)"
        print(f"{link['archived']}\n    {label}")
    print(f"\n{len(found)} link(s)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except WaybackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"error: network request failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
