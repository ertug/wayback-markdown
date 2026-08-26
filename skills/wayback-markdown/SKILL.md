---
name: wayback-markdown
description: >-
  Read archived/old versions of web pages (Wayback Machine / Internet Archive /
  web.archive.org) as clean Markdown. Use when the user wants a historical
  snapshot of a URL, a page that is dead or changed, or content from a specific
  past date — especially since built-in web fetchers refuse web.archive.org.
---

# wayback-markdown

CLI that fetches an Internet Archive snapshot and returns metadata frontmatter +
a length-capped Markdown body. Handles HTML/text plus PDF, DOCX, and PPTX
captures. Run it via the shell.

Requires the `wayback-markdown` command on PATH:
`uv tool install git+https://github.com/ertug/wayback-markdown.git`.

## Commands

```sh
# Find captures of a URL
wayback-markdown list example.com --from 2010 --to 2011 --status 200 --json

# Fetch a snapshot as Markdown (default: latest capture)
wayback-markdown get example.com --at 2010 --max-chars 8000
wayback-markdown get example.com --at 20100210120000        # closest to full timestamp
wayback-markdown get 'https://web.archive.org/web/20100210/https://example.com/'

# List outbound links, each a ready `get` argument
wayback-markdown links example.com --at 2010 --internal-only --json
```

## Workflow

1. Unsure which capture exists? `list` first (use `--json` to parse).
2. `get` the capture. `--at <date|timestamp>` picks the closest; omit for latest.
3. Body truncated? Re-fetch the next slice with `--offset <chars>` (matching `--max-chars`).
4. Follow the page: any link in the output is a valid `get` argument; or use `links`.
5. Near-empty body? Check the frontmatter for `frames:` or `meta-refresh:` and `get` one.

## Notes

- `--max-chars 0` = unlimited. `--no-frontmatter` = body only.
- `--json` is available on `list` and `links`; `get` is text-only.
- Identical requests are cached under `/tmp/wayback-markdown-cache`.
- Be gentle: the Internet Archive is a free nonprofit. Fetch one at a time — no
  parallel requests, no tight loops.

