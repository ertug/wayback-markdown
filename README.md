# wayback-markdown

Let your AI agents browse the [Wayback Machine](https://web.archive.org/) in clean Markdown — an MCP server and CLI.

It fetches an [Internet Archive](https://archive.org/) snapshot, converts it
to agent-friendly Markdown with [markitdown](https://github.com/microsoft/markitdown) (**HTML/text** plus
**PDF, DOCX, PPTX**), and returns a **metadata frontmatter + length-capped body** — so an
agent sees the signals that matter (capture time, redirects, status) without flooding its
context.

> [!NOTE]
> Please use it responsibly and avoid creating unnecessary load on the archive — the
> Internet Archive is a free, donation-funded nonprofit.

## Why

- **Agent-ready** — connect it to any AI agent or MCP client with a single command.
- **Context-efficient** — spends few tokens per read: outputs Markdown (no HTML noise),
  leads with a YAML frontmatter block (capture time, redirects, status), truncates to a
  character budget, and pages the rest on demand, so the agent gets the signals up front.
- **Toolbar-free** — no injected Wayback UI leaks into the Markdown.
- **Self-navigating** — every link and image is rewritten to its archived form (honoring
  `<base href>`). Links in the output are valid `get` inputs: paste one back in to keep browsing.
- **Cached** — identical requests are served from a disk cache. A capture
  addressed by its full timestamp is cached forever (it's immutable); "latest"/nearest
  lookups refresh daily.
- **Unblocked** — `web.archive.org` is typically off-limits to AI agents
  (e.g. Claude Code's built-in `WebFetch` refuses the host). This tool fetches the raw
  `…id_/…` endpoint directly over `httpx`, so an agent can read captures anyway.
- **Redirect-aware** — surfaces archive redirects (you got a different capture than asked),
  crawl-time redirects (the page was a 3xx when captured), and client-side `<meta refresh>`
  redirects.
- **Meta-aware** — surfaces the `<title>` and `description`/`keywords`/`author` `<meta>`
  tags markitdown drops.
- **Frame-aware** — the frontmatter lists each `<frame>` target as a ready `get` input
  (including ones written at runtime by `document.write`).
- **Fallback-aware** — re-parses `<noframes>`/`<noscript>` bodies as markup, so a page whose
  real content hides in one converts properly instead of dumping unparsed tags.
- **Charset-aware** — decodes each capture by its declared charset (HTTP `Content-Type`
  header first, then `<meta charset>`/BOM, sniffing only as a fallback), so legacy
  Windows-1252/Latin-1 pages keep bytes like `®` instead of `�`.

## Demo (CLI)

```console
$ wayback-markdown get http://www.python.org --at 1997 --max-chars 500
```

```markdown
---
requested-url: http://www.python.org
requested-timestamp: 1997 (1997)
served-timestamp: 19980119014227 (1998-01-19 01:42:27 UTC)
timestamp-note: no capture at the requested time; served the nearest one
final-url: https://web.archive.org/web/19980119014227id_/http://www.python.org/
http-status: 200
redirects:
  - https://web.archive.org/web/1997id_/http://www.python.org
mimetype: text/html
title: "Python Language Home Page"
description: "Home page for Python, an interpreted, interactive, object-oriented, extensible programming language. It provides an extraordinary combination of clarity and versatility, and is free and comprehensively ported."
keywords: "Python programming language object oriented web free source"
markdown-chars: 11225
---

![[](https://web.archive.org/web/19980119014227/http://www.python.org/pics/ArrowLeft.gif)**[Home](https://web.archive.org/web/19980119014227/http://www.python.org/) |
[Software](https://web.archive.org/web/19980119014227/http://www.python.org/python/) |
[Documentation](https://web.archive.org/web/19980119014227/http://www.python.org/doc/) |
[PSA](https://web.archive.org/web/19980119014227/http://www.python.org/psa/) |
[Workshops](https://web.archive.org/web/19980119014227/http://www.python.org/w


[truncated: showing chars 0-500 of 11225 total. Continue from offset 500 for more.]
```

## Install

Needs [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`, or see the [install docs](https://docs.astral.sh/uv/getting-started/installation/)):

```sh
uv tool install git+https://github.com/ertug/wayback-markdown.git
wayback-markdown --help
```

## MCP server

Point any [MCP](https://modelcontextprotocol.io) client at the `mcp` subcommand, e.g. with Claude Code:

```sh
claude mcp add wayback-markdown -- wayback-markdown mcp
```

Or add it to an MCP client config directly:

```json
{
  "mcpServers": {
    "wayback-markdown": {
      "command": "wayback-markdown",
      "args": ["mcp"]
    }
  }
}
```

Optionally, set the cache location by adding an `"env"` block — e.g. to keep it across
reboots: `"env": { "WAYBACK_MARKDOWN_CACHE": "~/.cache/wayback-markdown" }`.

## Tools

- **`list`** — find captures of a URL: date range, HTTP status, URL/MIME-type regex
  filters, and `match=prefix|domain` to sweep a whole path or site.
- **`get`** — fetch a snapshot as frontmatter + Markdown; picks the capture closest to
  `at`, truncates to `max_chars`, and pages long documents via `offset`.
- **`links`** — list a snapshot's outbound links, each rewritten to its archived URL,
  ready to `get`.

The same tools are available as CLI subcommands (`wayback-markdown list|get|links`,
see `--help`) — as used in the Demo and Formats sections.

> [!WARNING]
> Archived pages are untrusted third-party content. As with any web-fetching tool, the
> output may contain text that tries to manipulate the agent (prompt injection).
> The server's instructions do tell the agent to treat fetched content as page data,
> never as directions to follow, but don't rely on that alone —
> run the agent with least privilege and review what it does with fetched content.

## Prompt an agent

With the server connected, hand the agent a prompt and let it list, fetch, and follow
captures on its own — ideal for the obscure and the long-gone:

- "Poke around space.com's earliest captures and surface something interesting
  about the space news it led with back then."
- "X-Files fan sites once filled geocities.com — find a captured one and summarize
  the fan theories it laid out about the show's conspiracy mythology."
- "kuro5hin.org ran first-person essays on surviving the dot-com bust, now all 404 —
  recover one and summarize its story and where the comment thread landed."

## Formats

Run it directly on one real capture per format — each doubles as a smoke test for
its converter:

```sh
# HTML — the first website
wayback-markdown get 'http://info.cern.ch/hypertext/WWW/TheProject.html' --at 19990427150243
# text — robots.txt, passed through verbatim
wayback-markdown get 'http://www.google.com/robots.txt' --at 20010206202714
# PDF  — the Bitcoin whitepaper
wayback-markdown get 'http://www.bitcoin.org:80/bitcoin.pdf' --at 20100704213649
# DOCX — IEEE's conference-paper template
wayback-markdown get 'https://www.ieee.org/content/dam/ieee-org/ieee/web/org/conferences/conference-template-a4.docx' --at 20200823052644
# PPTX — a NASA talk on the JWST mirror coatings
wayback-markdown get 'https://jwst.nasa.gov/resources/SPIE20128442-89RKeski-Kuha.pptx' --at 20130219022902
```

## Configuration & development

- `$WAYBACK_MARKDOWN_CACHE` (env var) — cache location (default `/tmp/wayback-markdown-cache` on Linux).
- `uv sync` once, then `uv run pytest` — offline unit tests, no network.
