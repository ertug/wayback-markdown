"""Convert archived content to Markdown with markitdown.

Handles HTML/plain text (the common case) plus binary documents — PDF, DOCX, PPTX —
which markitdown converts from their raw bytes given the right file extension.
"""

from __future__ import annotations

import enum
import io
import re
from typing import Optional
from urllib.parse import urlsplit

from markitdown import MarkItDown, StreamInfo

from .wayback import WEB_BASE, WaybackError

_converter = MarkItDown()

# empty type included: the archive sometimes omits it, and the common case is HTML.
_HTML_TYPES = {"text/html", "application/xhtml+xml", ""}
_TEXT_PREFIX = "text/"

_DOC_EXT_BY_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".docx",
    "application/vnd.ms-powerpoint": ".pptx",
}

# the same extensions, for detecting a doc by URL suffix when the archive reports a
# generic/missing mimetype
_DOC_SUFFIXES = tuple(sorted(set(_DOC_EXT_BY_MIME.values())))


class Kind(enum.Enum):
    """How a fetched capture should be turned into Markdown."""

    HTML = "html"
    TEXT = "text"
    DOC = "doc"
    UNSUPPORTED = "unsupported"


def _url_doc_ext(url: str) -> Optional[str]:
    """The doc extension implied by the URL's path suffix, if any."""
    path = urlsplit(url).path.lower()
    return next((s for s in _DOC_SUFFIXES if path.endswith(s)), None)


def doc_ext(mimetype: str, url: str = "") -> Optional[str]:
    """The document extension for a capture, or None if it isn't a binary doc.

    A binary-doc mimetype wins outright; an explicit ``text/*`` type is never a doc
    (even at a ``.pdf`` URL); otherwise an unknown/generic type falls back to the URL
    suffix, catching docs the archive served with a missing type. This is exactly the
    ``Kind.DOC`` condition, so a truthy result and :func:`classify` agree by construction.
    """
    mt = (mimetype or "").lower()
    if mt in _DOC_EXT_BY_MIME:
        return _DOC_EXT_BY_MIME[mt]
    if mt.startswith(_TEXT_PREFIX):
        return None
    return _url_doc_ext(url)


def classify(mimetype: str, url: str = "") -> Kind:
    """Decide how a capture is converted, most-specific first:

    1. A binary-doc mimetype (or, for an unknown/generic type, a doc URL suffix) → DOC.
    2. An explicit textual type is trusted: ``text/html`` is rewritten and converted,
       any other ``text/*`` passes through unchanged — so it never routes to a doc.
    3. An empty type defaults to HTML (the archive often omits it); any other unknown
       type with no doc suffix is UNSUPPORTED.
    """
    mt = (mimetype or "").lower()
    if doc_ext(mimetype, url):
        return Kind.DOC
    if mt in _HTML_TYPES:
        return Kind.HTML
    if mt.startswith(_TEXT_PREFIX):
        return Kind.TEXT
    return Kind.UNSUPPORTED


def _convert_stream(data: bytes, extension: str, charset: Optional[str] = None) -> str:
    # A known charset is declared via StreamInfo so markitdown trusts it instead of
    # re-sniffing — its sniffer mis-guesses a legacy encoding for byte patterns with
    # few non-ASCII chars (e.g. a lone "®"), reintroducing mojibake.
    try:
        if charset is not None:
            stream_info = StreamInfo(extension=extension, charset=charset)
            result = _converter.convert_stream(io.BytesIO(data), stream_info=stream_info)
        else:
            result = _converter.convert_stream(io.BytesIO(data), file_extension=extension)
    except Exception as exc:
        raise WaybackError(f"could not convert capture to Markdown: {exc}") from exc
    return (result.text_content or "").strip()


# markitdown percent-encodes the reserved chars of the nested URL inside our archive
# links (``http%3A//space.com%3A80``). Decode the colon back — only inside archive-link
# destinations, and only that octet, so genuinely-encoded bytes like ``%20`` survive — to
# keep links clean and re-parseable as ``get`` inputs.
_ARCHIVE_DEST = re.compile(r"(?<=\]\()" + re.escape(WEB_BASE) + r"/[^)\s]+")


def _restore_archive_colons(markdown: str) -> str:
    return _ARCHIVE_DEST.sub(
        lambda m: m.group(0).replace("%3A", ":").replace("%3a", ":"), markdown
    )


def html_to_markdown(html: str) -> str:
    """Convert an HTML/text string to Markdown."""
    return _restore_archive_colons(
        _convert_stream(html.encode("utf-8"), ".html", charset="utf-8")
    )


def doc_to_markdown(data: bytes, extension: str) -> str:
    """Convert raw document bytes (PDF/DOCX/PPTX) to Markdown for ``extension``.

    The caller supplies the extension from :func:`doc_ext` (the same value that made
    :func:`classify` route the capture to :attr:`Kind.DOC`).
    """
    return _convert_stream(data, extension)
