"""Offline unit tests — no network access required."""

import json

import pytest

from wayback_markdown import cache, convert, links, output, wayback
from wayback_markdown.cache import Fetched
from wayback_markdown.input import parse_target
from wayback_markdown.wayback import NoSnapshotError, WaybackError


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Point the cache at a per-test temp dir, the same env var production uses."""
    monkeypatch.setenv("WAYBACK_MARKDOWN_CACHE", str(tmp_path / "cache"))


# --- input parsing ----------------------------------------------------------


def test_parse_bare_url_adds_scheme():
    assert parse_target("example.com/x") == ("https://example.com/x", None)


def test_parse_bare_url_keeps_scheme():
    assert parse_target("http://example.com/x") == ("http://example.com/x", None)


def test_parse_archive_url_extracts_ts_and_modifier():
    orig, ts = parse_target(
        "https://web.archive.org/web/20100210id_/https://example.com/x"
    )
    assert orig == "https://example.com/x"
    assert ts == "20100210"


def test_parse_archive_url_without_modifier():
    orig, ts = parse_target("https://web.archive.org/web/20100210/http://example.com/")
    assert orig == "http://example.com/"
    assert ts == "20100210"


def test_parse_target_rejects_calendar_wildcard_url():
    with pytest.raises(WaybackError):
        parse_target("https://web.archive.org/web/2010*/example.com")
    with pytest.raises(WaybackError):
        parse_target("https://web.archive.org/web/collections/example.com")


# --- wayback url helpers ----------------------------------------------------


def test_snapshot_url_uses_id_modifier():
    assert (
        wayback.snapshot_url("https://example.com", "20100101")
        == "https://web.archive.org/web/20100101id_/https://example.com"
    )


def test_parse_served_ts():
    url = "https://web.archive.org/web/20100210031029/https://example.com/"
    assert wayback.parse_served_ts(url) == "20100210031029"


def test_split_archive_url():
    assert wayback.split_archive_url(
        "https://web.archive.org/web/20100210id_/https://example.com/x"
    ) == ("https://example.com/x", "20100210")
    assert wayback.split_archive_url("https://example.com/") is None


def test_requested_ts_normalizes_and_latest_is_none():
    assert wayback.requested_ts(at="2010-01-02") == "20100102"
    assert wayback.requested_ts(at="2010") == "2010"
    # no date given => None, and the caller falls back to the latest-capture sentinel
    assert wayback.requested_ts() is None


def test_normalize_ts_strips_non_digits():
    assert wayback.normalize_ts("2010-01-02") == "20100102"


def test_parse_cdx():
    text = json.dumps(
        [["timestamp", "original"], ["20100101", "http://x"], ["20100102", "http://y"]]
    )
    assert wayback.parse_cdx(text) == [
        {"timestamp": "20100101", "original": "http://x"},
        {"timestamp": "20100102", "original": "http://y"},
    ]
    assert wayback.parse_cdx("") == []
    assert wayback.parse_cdx("[]") == []


# --- link rewrite / extraction ----------------------------------------------


def test_rewrite_urls_absolutizes_and_pins_timestamp():
    html = '<a href="/page">x</a><img src="img.png">'
    out = links.rewrite_urls(html, "https://example.com/dir/", "20100101")
    assert "https://web.archive.org/web/20100101/https://example.com/page" in out
    assert "https://web.archive.org/web/20100101/https://example.com/dir/img.png" in out


def test_rewrite_urls_skips_non_navigable():
    html = '<a href="mailto:a@b.com">m</a><a href="#frag">f</a>'
    out = links.rewrite_urls(html, "https://example.com/", "20100101")
    assert "mailto:a@b.com" in out
    assert "web.archive.org" not in out


def test_rewrite_honors_base_href():
    # A <base href> must resolve relatives against the base, not the page URL — we fetch
    # the unrewritten `id_` capture, so nothing but us applies it.
    html = '<head><base href="http://ex.com/sub/"></head><a href="p.htm">x</a>'
    out = links.rewrite_urls(html, "http://ex.com/other/page.html", "20100101")
    assert "https://web.archive.org/web/20100101/http://ex.com/sub/p.htm" in out


def test_extract_honors_base_href():
    html = '<head><base href="http://ex.com/sub/"></head><a href="p.htm">x</a>'
    got = links.extract(html, "http://ex.com/other/page.html", "20100101")
    assert got[0]["original"] == "http://ex.com/sub/p.htm"


def test_rewrite_unwraps_noframes_fallback():
    # A raw-text fallback whose body is real HTML: it must become markup (not an
    # escaped &lt;a&gt; dump), and links revealed inside must be archived too.
    html = '<noframes><a href="/real">content</a></noframes>'
    out = links.rewrite_urls(html, "https://example.com/", "20100101")
    assert "&lt;a" not in out
    assert "https://web.archive.org/web/20100101/https://example.com/real" in out


def test_rewrite_preserves_noscript_markup():
    # bs4 parses <noscript> children into a real tree (it is NOT raw-text), so its
    # links must be archived and its structure kept — never flattened to bare text.
    html = '<noscript><a href="/deal">Buy</a><p>Big <b>sale</b></p></noscript>'
    out = links.rewrite_urls(html, "https://example.com/", "20100101")
    assert "https://web.archive.org/web/20100101/https://example.com/deal" in out
    assert "<p>" in out and "<b>" in out


def test_html_to_markdown_restores_link_scheme():
    # Regression: markitdown's `http%3A//` must not leak into the body (links stay clean
    # and re-parseable as `get` inputs); `<img>` srcs, which it leaves intact, still match.
    rewritten = links.rewrite_urls(
        '<a href="p.htm">x</a><img src="i.jpg">', "http://ex.com/", "20100101"
    )
    md = convert.html_to_markdown(rewritten)
    assert "http%3A//" not in md and "http%3a//" not in md
    assert "https://web.archive.org/web/20100101/http://ex.com/p.htm" in md
    assert "https://web.archive.org/web/20100101/http://ex.com/i.jpg" in md


def test_html_to_markdown_restores_nested_port_colon():
    # A host:port must survive as `space.com:80`; a genuine `%20` must not be decoded.
    rewritten = links.rewrite_urls(
        '<a href="http://space.com:80/a%20b/x.html">x</a>',
        "http://space.com:80/",
        "19991004052946",
    )
    md = convert.html_to_markdown(rewritten)
    assert "space.com:80/" in md and "%3A" not in md and "%3a" not in md
    assert "/a%20b/x.html" in md  # legit encoding preserved


def test_html_to_markdown_keeps_legacy_charset_through_conversion():
    # Regression: markitdown re-sniffs the re-encoded body and mis-guesses a legacy
    # encoding for a lone "®", so the full pipeline must still preserve it.
    body = b'<html><head><meta charset="windows-1252"></head><body>' \
           b'<p>Microsoft\xae Internet Explorer</p></body></html>'
    text = Fetched(url="u", status=200, mimetype="text/html", content=body).text
    md = convert.html_to_markdown(links.rewrite_urls(text, "http://x/", "20100101"))
    assert "®" in md
    assert "�" not in md and "Â" not in md


def test_extract_internal_only():
    html = '<a href="/a">A</a><a href="https://other.com/b">B</a>'
    all_links = links.extract(html, "https://example.com/", "20100101")
    assert len(all_links) == 2
    internal = links.extract(
        html, "https://example.com/", "20100101", internal_only=True
    )
    assert len(internal) == 1
    assert internal[0]["original"] == "https://example.com/a"
    assert (
        internal[0]["archived"]
        == "https://web.archive.org/web/20100101/https://example.com/a"
    )


def test_frame_sources_finds_static_and_js_written_frames():
    html = (
        '<frameset cols="115,*"><frame src="nav.html"></frameset>'
        "<script>document.write('<frame src=\"main.html\" name=x>');</script>"
    )
    frames = links.frame_sources(html, "https://example.com/", "20100101")
    assert frames == [
        "https://web.archive.org/web/20100101/https://example.com/nav.html",
        "https://web.archive.org/web/20100101/https://example.com/main.html",
    ]


def test_frame_sources_empty_and_ignores_iframe():
    html = '<iframe src="embed.html"></iframe><p>no frames here</p>'
    assert links.frame_sources(html, "https://example.com/", "20100101") == []


def test_frame_sources_handles_escaped_quotes_in_js_string():
    # document.write with backslash-escaped quotes, the other common inline form.
    html = "<script>document.write(\"<frame src=\\\"main.html\\\">\");</script>"
    assert links.frame_sources(html, "https://example.com/", "20100101") == [
        "https://web.archive.org/web/20100101/https://example.com/main.html"
    ]


def test_head_meta_extracts_and_collapses_whitespace():
    html = (
        '<meta name="Description" content="A  page\n about dogs">'
        '<meta name="keywords" content="dogs, puppies">'
        '<meta name="viewport" content="width=1">'
    )
    assert links.head_meta(html) == {
        "description": "A page about dogs",
        "keywords": "dogs, puppies",
    }


def test_head_meta_extracts_title_and_author():
    html = (
        "<head><title>\n  Area 51  -  SPACE.com\n</title>"
        '<meta name="author" content="Jane Roe"></head>'
    )
    assert links.head_meta(html) == {
        "title": "Area 51 - SPACE.com",
        "author": "Jane Roe",
    }


def test_meta_refresh_signposts_target():
    # Relative target resolves against the page URL and comes back archived.
    html = '<meta http-equiv="Refresh" content="0; url=/area51/real.html">'
    assert (
        links.meta_refresh(html, "http://space.com/area51/index.html", "19991004")
        == "https://web.archive.org/web/19991004/http://space.com/area51/real.html"
    )


def test_meta_refresh_ignores_plain_reload():
    # A refresh with no url= is a timed reload, not a redirect.
    assert links.meta_refresh('<meta http-equiv="refresh" content="5">', "http://x/", "1") is None
    assert links.meta_refresh("<p>no meta here</p>", "http://x/", "1") is None


# --- content type routing ---------------------------------------------------


def test_classify_covers_the_full_routing_table():
    K = convert.Kind
    assert convert.classify("application/pdf") is K.DOC
    assert convert.classify(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ) is K.DOC
    assert convert.classify(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ) is K.DOC
    # html: explicit type, and the empty type the archive sometimes sends
    assert convert.classify("text/html") is K.HTML
    assert convert.classify("", "http://x/page") is K.HTML
    assert convert.classify("text/plain") is K.TEXT
    # a doc with a missing/generic type is still caught by its URL suffix...
    assert convert.classify("", "http://x/report.pdf") is K.DOC
    assert convert.classify("application/octet-stream", "http://x/d.docx") is K.DOC
    # ...but an explicit text/* type must never route to a binary doc
    assert convert.classify("text/html", "http://x/a.pdf") is K.HTML
    assert convert.classify("text/plain", "http://x/a.docx") is K.TEXT
    assert convert.classify("image/png") is K.UNSUPPORTED
    assert convert.classify("application/octet-stream", "http://x/page.html") is K.UNSUPPORTED


def test_doc_ext_resolution_matches_classify_routing():
    # the extension cmd_get feeds doc_to_markdown: mimetype wins, else URL suffix,
    # and an explicit text/* type is never treated as a doc.
    assert convert.doc_ext("application/pdf") == ".pdf"
    assert convert.doc_ext("application/msword") == ".docx"
    assert convert.doc_ext("application/vnd.ms-powerpoint") == ".pptx"
    assert convert.doc_ext("", "http://x/report.pdf") == ".pdf"
    assert convert.doc_ext("application/octet-stream", "http://x/d.docx") == ".docx"
    assert convert.doc_ext("text/html", "http://x/a.pdf") is None
    assert convert.doc_ext("image/png") is None


# --- truncation -------------------------------------------------------------


def test_truncate_cuts_and_reports():
    chunk, info = output.truncate("abcdefghij", 4, 0)
    assert chunk == "abcd"
    assert info.truncated and info.end == 4 and info.total == 10


def test_truncate_offset_reaches_end():
    chunk, info = output.truncate("abcdefghij", 4, 8)
    assert chunk == "ij"
    assert not info.truncated


def test_truncate_no_limit():
    chunk, info = output.truncate("abc", 0, 0)
    assert chunk == "abc" and not info.truncated


def test_truncation_marker_points_to_next_offset():
    _, info = output.truncate("abcdefghij", 4, 0)
    assert "offset 4" in output.truncation_marker(info)


def test_human_ts_renders_only_the_precision_present():
    assert output.human_ts("20010206202714") == "2001-02-06 20:27:14 UTC"
    assert output.human_ts("20100210") == "2010-02-10"
    assert output.human_ts("201002") == "2010-02"
    assert output.human_ts("2010") == "2010"
    # too short to name a year, or empty/None
    assert output.human_ts("201") is None
    assert output.human_ts(None) is None


def _meta(requested_ts):
    return output.Meta(
        requested_url="u",
        requested_ts=requested_ts,
        served_ts="20100210031029",
        final_url="f",
        status=200,
        redirect_history=[],
        mimetype="text/html",
        total_chars=0,
    )


def test_served_differs():
    assert not _meta("2010").served_differs
    assert _meta("20090101").served_differs
    assert not _meta(None).served_differs


def test_metadata_frontmatter_surfaces_frames_and_meta():
    meta = output.Meta(
        requested_url="u",
        requested_ts="2010",
        served_ts="20100210031029",
        final_url="f",
        status=200,
        redirect_history=[],
        mimetype="text/html",
        total_chars=0,
        frames=["https://web.archive.org/web/20100210031029/https://example.com/main.html"],
        description='He said "hi": welcome',
        keywords="a, b",
    )
    front = output.metadata_frontmatter(meta)
    assert "frames:" in front
    assert "  - https://web.archive.org/web/20100210031029/https://example.com/main.html" in front
    # Colons and quotes in free text stay inside a quoted scalar, not breaking YAML.
    assert r'description: "He said \"hi\": welcome"' in front
    assert "keywords: \"a, b\"" in front


def test_metadata_frontmatter_caps_long_meta():
    meta = output.Meta(
        requested_url="u",
        requested_ts=None,
        served_ts="20100210031029",
        final_url="f",
        status=200,
        redirect_history=[],
        mimetype="text/html",
        total_chars=0,
        keywords="x" * 2000,
    )
    line = next(
        ln for ln in output.metadata_frontmatter(meta).splitlines() if ln.startswith("keywords:")
    )
    assert line.endswith('…"')
    assert len(line) < 600


# --- cache ------------------------------------------------------------------


def test_text_decodes_legacy_charset_without_mojibake():
    # Regression: a page declaring windows-1252 must decode by that charset, not be forced
    # through UTF-8 into replacement chars (0xAE -> "®", never "�").
    body = (
        b'<meta charset="windows-1252">Copyright \xa9 1998. Microsoft\xae IE.'
    )
    text = Fetched(url="u", status=200, mimetype="text/html", content=body).text
    assert "�" not in text
    assert "©" in text and "®" in text


def test_text_header_charset_beats_sniffing():
    # No in-document declaration: sniffing alone misguesses koi8-r, but the charset
    # from the HTTP Content-Type header (which the id_ endpoint replays) pins it.
    body = "Привет, мир".encode("koi8-r")
    fetched = Fetched(
        url="u", status=200, mimetype="text/plain", charset="koi8-r", content=body
    )
    assert fetched.text == "Привет, мир"


def test_text_header_charset_beats_meta_declaration():
    # HTTP semantics: the header wins over a conflicting <meta charset>.
    body = '<meta charset="iso-8859-1">café'.encode("utf-8")
    fetched = Fetched(
        url="u", status=200, mimetype="text/html", charset="utf-8", content=body
    )
    assert "café" in fetched.text


def test_text_decodes_utf8_body():
    body = "café — déjà vu".encode("utf-8")
    assert Fetched(url="u", status=200, mimetype="text/html", content=body).text == (
        "café — déjà vu"
    )


def _counter_fetch(calls):
    def fetch():
        calls.append(1)
        return Fetched(url="u", status=200, mimetype="text/html", content=b"hi")

    return fetch


def test_cache_hit_serves_second_call():
    calls = []
    fetch = _counter_fetch(calls)
    cache.get_or_fetch("http://x/1", fetch)
    result = cache.get_or_fetch("http://x/1", fetch)
    assert len(calls) == 1
    assert result.content == b"hi"


def test_cache_ttl_zero_always_refetches():
    calls = []
    fetch = _counter_fetch(calls)
    cache.get_or_fetch("http://x/2", fetch, ttl=0.0)
    cache.get_or_fetch("http://x/2", fetch, ttl=0.0)
    assert len(calls) == 2


def test_fetch_raw_only_pins_full_timestamps_in_cache(monkeypatch):
    # "latest" and prefix requests resolve to "nearest", which drifts as new captures
    # arrive — they must expire. Only a full pinned stamp is immutable (cached forever).
    ttls = []

    def spy(url, fetch_fn, *, ttl=None):
        ttls.append(ttl)
        return Fetched(
            url="https://web.archive.org/web/20100101120000/https://example.com",
            status=200,
            mimetype="text/html",
            content=b"x",
        )

    monkeypatch.setattr(wayback.cache, "get_or_fetch", spy)
    wayback.fetch_raw("https://example.com", wayback.LATEST_TS)
    wayback.fetch_raw("https://example.com", "2010")
    wayback.fetch_raw("https://example.com", "20100101120000")
    assert ttls == [wayback.DEFAULT_TTL, wayback.DEFAULT_TTL, None]


def test_cdx_search_surfaces_error_status(monkeypatch):
    monkeypatch.setattr(
        wayback,
        "_http_get",
        lambda url: Fetched(url=url, status=503, mimetype="text/html", content=b"busy"),
    )
    with pytest.raises(WaybackError, match="503"):
        wayback.cdx_search("https://example.com")


def test_cdx_search_builds_url_and_mimetype_filters(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    seen = {}

    def fake_http_get(url):
        seen["url"] = url
        return Fetched(url=url, status=200, mimetype="application/json", content=b"[]")

    monkeypatch.setattr(wayback, "_http_get", fake_http_get)
    wayback.cdx_search(
        "https://example.com",
        url_filter=r".*\.pdf$",
        mimetype="application/pdf",
        match="domain",
    )
    query = parse_qs(urlsplit(seen["url"]).query)
    assert query["matchType"] == ["domain"]
    assert r"original:.*\.pdf$" in query["filter"]
    assert "mimetype:application/pdf" in query["filter"]


def test_fetch_raw_not_archived_raises_no_snapshot(monkeypatch):
    # 404 with no redirect (served ts still equals the request) => URL isn't archived.
    monkeypatch.setattr(
        wayback,
        "_http_get",
        lambda url: Fetched(url=url, status=404, mimetype="text/html", content=b"gone"),
    )
    with pytest.raises(NoSnapshotError) as exc:
        wayback.fetch_raw("https://example.com", "20100101")
    assert "itself a 404" not in str(exc.value)


def test_fetch_raw_pinned_404_hedges_message(monkeypatch):
    # A full pinned stamp is served directly (no redirect), so a 404 there can also be
    # a real capture whose crawl-time status was 404 — the message must say so.
    monkeypatch.setattr(
        wayback,
        "_http_get",
        lambda url: Fetched(url=url, status=404, mimetype="text/html", content=b"gone"),
    )
    with pytest.raises(NoSnapshotError) as exc:
        wayback.fetch_raw("https://example.com", "20100101120000")
    assert "itself a 404" in str(exc.value)


def test_converter_failure_is_a_wayback_error(monkeypatch):
    # markitdown's library-specific errors must surface as WaybackError, not a traceback.
    def boom(*args, **kwargs):
        raise RuntimeError("corrupt stream")

    monkeypatch.setattr(convert._converter, "convert_stream", boom)
    with pytest.raises(WaybackError, match="could not convert"):
        convert.doc_to_markdown(b"x", ".pdf")


def test_fetch_raw_crawl_time_error_is_surfaced(monkeypatch):
    # A real capture was reached (served ts differs from the request) but its crawl-time
    # status was an error: surface the HTTP error, not "no snapshot".
    landed = "https://web.archive.org/web/20100101120000/https://example.com"
    monkeypatch.setattr(
        wayback,
        "_http_get",
        lambda url: Fetched(url=landed, status=500, mimetype="text/html", content=b"err"),
    )
    with pytest.raises(WaybackError) as exc:
        wayback.fetch_raw("https://example.com", "2010")
    assert not isinstance(exc.value, NoSnapshotError)
    assert "500" in str(exc.value)


def test_cache_does_not_persist_error_responses():
    # a transient 503 must never be cached; with ttl=None it would poison forever
    calls = []

    def fetch_error():
        calls.append(1)
        return Fetched(url="u", status=503, mimetype="text/html", content=b"busy")

    cache.get_or_fetch("http://x/e", fetch_error, ttl=None)
    cache.get_or_fetch("http://x/e", fetch_error, ttl=None)
    assert len(calls) == 2
