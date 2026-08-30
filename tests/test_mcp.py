"""Offline tests for the MCP server — no network access required."""

import asyncio

import httpx
import pytest

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from wayback_markdown import mcp_server, render, wayback
from wayback_markdown.cache import Fetched
from wayback_markdown.wayback import NoSnapshotError


def _tools():
    return {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}


def test_advertises_three_tools_each_requiring_url():
    tools = _tools()
    assert set(tools) == {"list", "get", "links"}
    for tool in tools.values():
        assert tool.input_schema["required"] == ["url"]


def test_schema_defaults_track_render_signatures():
    props = _tools()["list"].input_schema["properties"]
    assert props["limit"]["default"] == 50
    assert props["match"]["enum"] == ["exact", "prefix", "domain"]


def test_list_schema_publishes_from_without_underscore():
    props = _tools()["list"].input_schema["properties"]
    assert "from" in props
    assert "from_" not in props


def test_call_tool_accepts_from_and_its_python_spelling(monkeypatch):
    captured = {}
    monkeypatch.setattr(render, "render_list", lambda url, **kw: captured.update(kw) or "rows")
    for key in ("from", "from_"):
        captured.clear()
        asyncio.run(
            mcp_server.server.call_tool("list", {"url": "example.com", key: "2010"})
        )
        assert captured["from_"] == "2010", key


def test_get_routes_to_render_with_kwargs(monkeypatch):
    captured = {}

    def fake_render_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return "# hello"

    monkeypatch.setattr(render, "render_get", fake_render_get)
    out = mcp_server.get(url="example.com", at="2010", max_chars=5)
    assert out == "# hello"
    assert captured["url"] == "example.com"
    assert captured["kwargs"]["at"] == "2010"
    assert captured["kwargs"]["max_chars"] == 5


def test_list_maps_from_and_json_aliases(monkeypatch):
    captured = {}
    monkeypatch.setattr(render, "render_list", lambda url, **kw: captured.update(kw) or "rows")
    mcp_server.list_(url="example.com", from_="2010", json=True)
    assert captured["from_"] == "2010"
    assert captured["as_json"] is True


def test_list_passes_url_and_mimetype_filters(monkeypatch):
    captured = {}
    monkeypatch.setattr(render, "render_list", lambda url, **kw: captured.update(kw) or "rows")
    mcp_server.list_(url="example.com", url_filter=r".*\.pdf$", mimetype="application/pdf")
    assert captured["url_filter"] == r".*\.pdf$"
    assert captured["mimetype"] == "application/pdf"


def test_links_maps_json_alias(monkeypatch):
    captured = {}
    monkeypatch.setattr(render, "render_links", lambda url, **kw: captured.update(kw) or "links")
    mcp_server.links(url="example.com", json=True)
    assert captured["as_json"] is True


def test_wayback_error_becomes_tool_error(monkeypatch):
    def boom(url, **kwargs):
        raise render.WaybackError("no snapshot")

    monkeypatch.setattr(render, "render_get", boom)
    with pytest.raises(ToolError, match="no snapshot"):
        mcp_server.get(url="example.com")


def test_transport_timeout_is_labeled_transient(monkeypatch):
    def boom(url, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(render, "render_get", boom)
    with pytest.raises(ToolError) as excinfo:
        mcp_server.get(url="example.com")
    msg = str(excinfo.value)
    assert "transient" in msg
    assert "ReadTimeout" in msg


def test_missing_capture_is_not_labeled_transient(monkeypatch):
    # A completed 404 must keep its own message, never the transport retry wording.
    def boom(url, **kwargs):
        raise NoSnapshotError("https://example.com")

    monkeypatch.setattr(render, "render_get", boom)
    with pytest.raises(ToolError) as excinfo:
        mcp_server.get(url="example.com")
    msg = str(excinfo.value)
    assert "No archived snapshot found" in msg
    assert "transient" not in msg


def test_call_tool_surfaces_error_not_crash(monkeypatch):
    # A ToolError is model-visible; an UnexpectedToolError would be a masked crash.
    def boom(url, **kw):
        raise render.WaybackError("dead host")

    monkeypatch.setattr(render, "render_links", boom)
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(mcp_server.server.call_tool("links", {"url": "example.com"}))
    assert not isinstance(excinfo.value, UnexpectedToolError)
    assert "dead host" in str(excinfo.value)


def test_call_tool_success_returns_text(monkeypatch):
    monkeypatch.setattr(render, "render_links", lambda url, **kw: "one link")
    result = asyncio.run(mcp_server.server.call_tool("links", {"url": "example.com"}))
    assert result.is_error is False
    assert result.content[0].text == "one link"


# These drive the real render functions (only the network is stubbed), so they
# catch forwarding drift the stubbed tests above miss — e.g. a dropped render kwarg.


def test_get_tool_drives_real_render(monkeypatch):
    fetched = Fetched(
        url="https://web.archive.org/web/20100210120000/https://example.com/",
        status=200,
        mimetype="text/plain",
        content=b"hello from the archive",
    )
    monkeypatch.setattr(wayback, "fetch_raw", lambda *a, **kw: fetched)
    out = mcp_server.get(url="example.com", no_frontmatter=True)
    assert out == "hello from the archive"


def test_list_tool_drives_real_render(monkeypatch):
    monkeypatch.setattr(
        wayback,
        "cdx_search",
        lambda *a, **kw: [
            {"timestamp": "20100210120000", "statuscode": "200",
             "mimetype": "text/html", "original": "https://example.com/"}
        ],
    )
    out = mcp_server.list_(url="example.com")
    assert "https://example.com/" in out
    assert "1 capture(s)" in out
    assert "spanning" not in out  # a lone row has no span


def test_list_footer_reports_capture_span(monkeypatch):
    def rows(*a, **kw):
        return [
            {"timestamp": "19961020024433", "original": "http://x/"},
            {"timestamp": "19970210150455", "original": "http://x/"},
            {"timestamp": "19981212020858", "original": "http://x/"},
        ]

    monkeypatch.setattr(wayback, "cdx_search", rows)
    out = mcp_server.list_(url="x")
    assert "3 capture(s) spanning 1996-10-20 → 1998-12-12" in out
