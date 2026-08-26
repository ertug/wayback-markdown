# wayback-markdown

A [Wayback Machine](https://web.archive.org/) to Markdown CLI for AI coding agents.

It fetches an [Internet Archive](https://archive.org/) snapshot from the raw, toolbar-free endpoint, converts it
to Markdown with [markitdown](https://github.com/microsoft/markitdown) (**HTML/text** plus
**PDF, DOCX, PPTX**), and returns a **metadata frontmatter + length-capped body** — so an
agent sees the signals that matter (capture time, redirects, status) without flooding its
context.

> [!NOTE]
> Please use it responsibly and avoid creating unnecessary load on the archive — the
> Internet Archive is a free, donation-funded nonprofit.

## Why

- **Context-efficient** — spends few tokens per read: outputs Markdown (no HTML noise),
  leads with a YAML frontmatter block (capture time, redirects, status), truncates to a
  character budget, and pages the rest on demand, so the agent gets the signals up front.
- **Toolbar-free** — no injected Wayback UI leaks into the Markdown.
- **Self-navigating** — every link and image is rewritten to its archived form (honoring
  `<base href>`). Links in the output are valid `get` inputs: paste one back in to keep browsing.
- **Cached** — identical requests are served from `/tmp/wayback-markdown-cache`. A capture
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

## Demo

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


[truncated: showing chars 0-500 of 11225 total. Re-run with --offset 500 for more.]
```

## Install

```sh
uv tool install git+https://github.com/ertug/wayback-markdown.git
wayback-markdown --help
```

To let an AI agent drive the tool, clone the repo and copy `skills/wayback-markdown/`
into its skills dir:

```sh
git clone https://github.com/ertug/wayback-markdown.git
cp -r wayback-markdown/skills/wayback-markdown ~/.claude/skills/     # Claude Code
cp -r wayback-markdown/skills/wayback-markdown ~/.agents/skills/     # Codex
cp -r wayback-markdown/skills/wayback-markdown ~/.gemini/config/skills/  # Antigravity
```

## Prompt an agent

With the [skill](skills/wayback-markdown/SKILL.md) installed, hand the agent a prompt and let it list, fetch, and
follow captures on its own — ideal for the obscure and the long-gone:

- "Poke around space.com's earliest captures and surface something interesting
  about the space news it led with back then."
- "X-Files fan sites once filled geocities.com — find a captured one and summarize
  the fan theories it laid out about the show's conspiracy mythology."
- "kuro5hin.org ran first-person essays on surviving the dot-com bust, now all 404 —
  recover one and summarize its story and where the comment thread landed."

## Commands

```sh
# list — find captures
wayback-markdown list example.com --from 2010 --to 2011 --status 200
#   --match exact|prefix|domain   --no-collapse (per-capture, not per-day)   --limit   --json

# get — fetch a snapshot as Markdown
wayback-markdown get example.com --at 2010 --max-chars 8000
wayback-markdown get example.com --at 20100210120000            # full stamp, closest capture
wayback-markdown get example.com --at 2010 --offset 8000       # next slice
wayback-markdown get 'https://web.archive.org/web/20100210/https://example.com/'
#   pick a capture: --at <date-or-timestamp> (closest) | default (latest)
#   a full archive URL carries its own timestamp   --max-chars (0 = unlimited)
#   --no-frontmatter   body only, no metadata frontmatter (get is text-only; list/links have --json)

# links — list outbound links, each as a ready `get` argument
wayback-markdown links example.com --at 2010 --internal-only --limit 50
```

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

- `$WAYBACK_MARKDOWN_CACHE` (env var) or `--cache-dir` (flag) — cache location (default `/tmp/wayback-markdown-cache`).
- `uv sync --extra dev` once, then `uv run pytest` — offline unit tests, no network.
