"""Extract and rewrite content in archived HTML.

The three URL-bearing helpers share one resolver (:func:`_resolve`), which
absolutizes a raw attribute against the page URL and pins it to the served
timestamp:

* :func:`rewrite_urls` rewrites *every* URL-bearing attribute (links, images, css,
  js, media) to its archived form, so the Markdown ``get`` returns is
  self-navigating (links are valid ``wayback get`` inputs, images point at archived
  assets).
* :func:`extract` lists the anchors for the ``links`` command.
* :func:`frame_sources` finds a frameset's ``<frame>`` targets — including the
  ones written at runtime by ``document.write`` — so ``get`` can signpost them
  even though markitdown (no JavaScript) only ever sees the ``<noframes>`` body.

Standing apart, :func:`head_meta` pulls the ``description``/``keywords`` ``<meta>``
text, which markitdown drops but which is often the most informative content on old
pages.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .wayback import WEB_BASE

URL_ATTRS = {
    "a": ["href"],
    "img": ["src", "srcset"],
    "link": ["href"],
    "script": ["src"],
    "source": ["src", "srcset"],
    "iframe": ["src"],
    "frame": ["src"],
}

# Scans raw HTML rather than the parse tree so it also catches frames emitted from
# a ``document.write('<frame src=...>')`` string, which bs4 hides in a script node.
_FRAME_SRC = re.compile(r"<frame\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)

_SKIP_PREFIXES = ("#", "javascript:", "mailto:", "tel:", "data:", "about:")


def _archive(orig_absolute: str, served_ts: str) -> str:
    return f"{WEB_BASE}/{served_ts}/{orig_absolute}"


def _attr_str(value) -> str:
    """A tag attribute as a plain string (bs4 returns a list for multi-valued attrs)."""
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


def _resolve(value: str, base_url: str) -> Optional[str]:
    """Absolutize a raw attribute value against the page URL, or None to skip."""
    value = (value or "").strip()
    if not value or value.lower().startswith(_SKIP_PREFIXES):
        return None
    return urljoin(base_url, value)


def _rewrite_srcset(value: str, base_url: str, served_ts: str) -> str:
    """Rewrite each URL in a srcset ('url 1x, url 2x') to archived form."""
    out = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(None, 1)
        absolute = _resolve(bits[0], base_url)
        url = _archive(absolute, served_ts) if absolute else bits[0]
        out.append(url + (" " + bits[1] if len(bits) > 1 else ""))
    return ", ".join(out)


# ``<noframes>`` is a raw-text element: the parser keeps its body as one opaque
# string, so a frameset's real content otherwise converts to a dump of unparsed
# tags; re-parsing that string as markup recovers it. (``<noscript>`` is *not*
# raw-text — bs4 already parses its children into a real tree, so unwrapping it
# via ``strings`` would flatten that tree to bare text and drop its links.)
_RAW_TEXT_FALLBACKS = ("noframes",)


def _unwrap_fallbacks(soup: BeautifulSoup) -> None:
    for tag_name in _RAW_TEXT_FALLBACKS:
        for tag in soup.find_all(tag_name):
            # ``strings`` yields the raw (unescaped) text; ``decode_contents`` would
            # re-escape it back to the &lt;body&gt; dump we're trying to avoid.
            inner = "".join(tag.strings)
            tag.replace_with(BeautifulSoup(inner, "html.parser"))


def rewrite_urls(html: str, base_url: str, served_ts: str) -> str:
    """Return ``html`` with every URL attribute pointed at the archived copy."""
    soup = BeautifulSoup(html, "html.parser")
    _unwrap_fallbacks(soup)  # first, so links revealed inside get rewritten too
    for tag_name, attrs in URL_ATTRS.items():
        for tag in soup.find_all(tag_name):
            for attr in attrs:
                if attr not in tag.attrs:
                    continue
                value = _attr_str(tag[attr])
                if attr == "srcset":
                    tag[attr] = _rewrite_srcset(value, base_url, served_ts)
                    continue
                absolute = _resolve(value, base_url)
                if absolute:
                    tag[attr] = _archive(absolute, served_ts)
    return str(soup)


def extract(
    html: str,
    base_url: str,
    served_ts: str,
    *,
    internal_only: bool = False,
    limit: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Return anchor links as ``{text, original, archived}`` dicts."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlsplit(base_url).netloc
    seen = set()
    out: List[Dict[str, str]] = []
    for tag in soup.find_all("a"):
        absolute = _resolve(_attr_str(tag.get("href", "")), base_url)
        if not absolute or absolute in seen:
            continue
        if internal_only and urlsplit(absolute).netloc != base_host:
            continue
        seen.add(absolute)
        out.append(
            {
                "text": " ".join(tag.get_text().split()),
                "original": absolute,
                "archived": _archive(absolute, served_ts),
            }
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def frame_sources(
    html: str,
    base_url: str,
    served_ts: str,
    *,
    limit: Optional[int] = None,
) -> List[str]:
    """Return archived URLs for a frameset's ``<frame>`` targets, in document order.

    Each is a ready ``get`` input. Deduplicated; empty when the page has no frames.
    """
    seen = set()
    out: List[str] = []
    for match in _FRAME_SRC.finditer(html):
        absolute = _resolve(match.group(1), base_url)
        if not absolute or absolute in seen:
            continue
        seen.add(absolute)
        out.append(_archive(absolute, served_ts))
        if limit is not None and len(out) >= limit:
            break
    return out


def head_meta(html: str) -> Dict[str, str]:
    """Return the ``description``/``keywords`` ``<meta>`` contents, when present.

    Whitespace is collapsed; keys with empty content are omitted.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, str] = {}
    for tag in soup.find_all("meta"):
        name = _attr_str(tag.get("name", "")).strip().lower()
        if name not in ("description", "keywords") or name in out:
            continue
        content = " ".join(_attr_str(tag.get("content", "")).split())
        if content:
            out[name] = content
    return out
