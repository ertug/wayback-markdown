"""Command-line entry point: ``wayback-markdown list|get|links|mcp``."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

import httpx

from . import render
from .wayback import WaybackError


def _add_url(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("url", help="a bare URL or a full web.archive.org snapshot URL")


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
        description="Let your AI agents browse the Wayback Machine in clean Markdown — an MCP server and CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="list archived captures of a URL")
    _add_url(p_list)
    _add_json(p_list)
    p_list.add_argument("--from", dest="from_", help="earliest date (e.g. 2010)")
    p_list.add_argument("--to", help="latest date (e.g. 2011)")
    p_list.add_argument("--status", help="filter by HTTP status code, e.g. 200")
    p_list.add_argument(
        "--url-filter",
        metavar="REGEX",
        help="only captures whose original URL matches this regex "
        r"(e.g. '.*\.pdf$'); pair with --match domain to sweep a whole site",
    )
    p_list.add_argument(
        "--mimetype",
        metavar="REGEX",
        help="only captures whose MIME type matches this regex (e.g. 'application/pdf')",
    )
    p_list.add_argument(
        "--match",
        choices=render.MATCH_SCOPES,
        default=render.DEFAULT_MATCH,
        help=f"URL match scope (default: {render.DEFAULT_MATCH})",
    )
    p_list.add_argument(
        "--no-collapse",
        action="store_true",
        help="show every capture instead of one per day",
    )
    p_list.add_argument(
        "--limit",
        type=int,
        default=render.DEFAULT_LIST_LIMIT,
        help=f"max rows (default {render.DEFAULT_LIST_LIMIT})",
    )
    p_list.set_defaults(func=cmd_list)

    p_get = subparsers.add_parser("get", help="fetch a snapshot as Markdown")
    _add_url(p_get)
    _add_ts_selection(p_get)
    p_get.add_argument(
        "--max-chars",
        type=int,
        default=render.DEFAULT_MAX_CHARS,
        help=f"max Markdown characters to return (0 = no limit; default {render.DEFAULT_MAX_CHARS})",
    )
    p_get.add_argument(
        "--offset",
        type=int,
        default=render.DEFAULT_OFFSET,
        help=f"start character offset (default {render.DEFAULT_OFFSET})",
    )
    p_get.add_argument(
        "--no-frontmatter",
        action="store_true",
        help="omit the metadata frontmatter; print only the Markdown body",
    )
    p_get.set_defaults(func=cmd_get)

    p_links = subparsers.add_parser("links", help="list a snapshot's outbound links")
    _add_url(p_links)
    _add_json(p_links)
    _add_ts_selection(p_links)
    p_links.add_argument(
        "--limit",
        type=int,
        default=render.DEFAULT_LINKS_LIMIT,
        help=f"max links (default {render.DEFAULT_LINKS_LIMIT})",
    )
    p_links.add_argument(
        "--internal-only",
        action="store_true",
        help="only links on the same host as the page",
    )
    p_links.set_defaults(func=cmd_links)

    p_mcp = subparsers.add_parser(
        "mcp",
        help="run as a stdio MCP server exposing list/get/links as tools",
    )
    p_mcp.set_defaults(func=cmd_mcp)

    return parser


def cmd_list(args) -> int:
    print(
        render.render_list(
            args.url,
            from_=args.from_,
            to=args.to,
            status=args.status,
            url_filter=args.url_filter,
            mimetype=args.mimetype,
            match=args.match,
            collapse_day=not args.no_collapse,
            limit=args.limit,
            as_json=args.json,
        )
    )
    return 0


def cmd_get(args) -> int:
    print(
        render.render_get(
            args.url,
            at=args.at,
            max_chars=args.max_chars,
            offset=args.offset,
            no_frontmatter=args.no_frontmatter,
        )
    )
    return 0


def cmd_links(args) -> int:
    print(
        render.render_links(
            args.url,
            at=args.at,
            limit=args.limit,
            internal_only=args.internal_only,
            as_json=args.json,
        )
    )
    return 0


def cmd_mcp(args) -> int:
    # Imported lazily so the common CLI paths don't pay for loading the server.
    from .mcp_server import serve_stdio

    return serve_stdio()


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
