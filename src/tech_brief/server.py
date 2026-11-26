"""Brief-updates MCP server for Hacker News.

- Headlines and individual stories: official HN API (Firebase).
- Keyword search: Algolia HN Search API.
"""

import asyncio
import html
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import lxml.html
import trafilatura
from mcp.server.mcpserver import MCPServer

HN_API = "https://hacker-news.firebaseio.com/v0"
ALGOLIA_API = "https://hn.algolia.com/api/v1"
USER_AGENT = "Mozilla/5.0 (compatible; tech-brief/0.1)"
HN_ITEM = "https://news.ycombinator.com/item?id="

mcp = MCPServer("tech-brief")


def _text(raw: str | None) -> str:
    """Convert HN's HTML snippets to plain text."""
    if not raw:
        return ""
    raw = re.sub(r"<p>", "\n\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _iso(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _story(id: str | int, title: str, url: str | None, **extra: Any) -> dict[str, Any]:
    hn_url = f"{HN_ITEM}{id}"
    return {
        "id": str(id),
        "title": title,
        "url": url or hn_url,  # Ask/Tell HN posts have no external link
        "hn_url": hn_url,
        "link": f"[{title}]({url or hn_url})",
        **extra,
    }


async def _item(client: httpx.AsyncClient, id: int | str) -> dict[str, Any] | None:
    resp = await client.get(f"{HN_API}/item/{id}.json")
    resp.raise_for_status()
    item: dict[str, Any] | None = resp.json()  # None for unknown ids
    return item


@mcp.tool()
async def get_headlines(limit: int = 20) -> list[dict[str, Any]]:
    """Get the current Hacker News front page, in rank order. `limit` is capped at 50."""
    limit = max(1, min(limit, 50))
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{HN_API}/topstories.json")
        resp.raise_for_status()
        items = await asyncio.gather(*(_item(client, i) for i in resp.json()[:limit]))
    return [
        _story(
            it["id"],
            it.get("title", ""),
            it.get("url"),
            points=it.get("score"),
            author=it.get("by"),
            comments=it.get("descendants"),
            created_at=_iso(it.get("time")),
        )
        for it in items
        if it and not it.get("dead") and not it.get("deleted")
    ]


def _xpath(node: Any, query: str) -> list[Any]:
    """lxml's xpath() has a very broad return type; these queries always return elements."""
    result: list[Any] = node.xpath(query)
    return result


def _extract_text(page: str) -> str | None:
    """Readable text from HTML: trafilatura first, plain <article>/<main> text as fallback."""
    text: str | None = trafilatura.extract(page, include_comments=False, include_tables=True)
    if text:
        return text
    try:
        doc = lxml.html.fromstring(page)
    except Exception:
        return None
    for bad in _xpath(doc, "//script|//style|//nav|//header|//footer|//aside|//form|//noscript"):
        bad.drop_tree()
    root = next((r[0] for q in ("//article", "//main", "//body") if (r := _xpath(doc, q))), None)
    if root is None:
        return None
    blocks = _xpath(root, ".//p|.//h1|.//h2|.//h3|.//h4|.//li|.//pre|.//blockquote")
    parts = (re.sub(r"\s+", " ", el.text_content()).strip() for el in blocks)
    return "\n\n".join(p for p in parts if p) or None


async def _fetch_content(client: httpx.AsyncClient, url: str, max_chars: int) -> dict[str, Any]:
    """Download an external page and extract its readable text. Never raises."""
    if not url.startswith(("http://", "https://")):
        return {"content_error": "unsupported URL scheme"}
    try:
        resp = await client.get(
            url, follow_redirects=True, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype:
            return {"content_error": f"not an HTML page ({ctype or 'unknown type'})"}
        text = await asyncio.to_thread(_extract_text, resp.text)
    except Exception as e:  # network errors, 403s, parse errors: report, don't fail the tool
        return {"content_error": f"{type(e).__name__}: {e}"}
    if not text:
        return {"content_error": "could not extract readable text (page may need JavaScript)"}
    truncated = len(text) > max_chars
    return {"content": text[:max_chars], "content_truncated": truncated}


@mcp.tool()
async def get_article(
    id: str,
    max_comments: int = 10,
    include_content: bool = True,
    max_content_chars: int = 8000,
) -> dict[str, Any]:
    """Get one Hacker News story by id: the linked article's text plus top-level comments.

    Use ids from `get_headlines` or `search_stories`. For link posts the external page
    is fetched and its readable text returned in `content` (cut to `max_content_chars`;
    `content_error` explains failures). Set `include_content` false to skip that.
    `max_comments` limits top-level comments (max 50).
    """
    max_comments = max(0, min(max_comments, 50))
    max_content_chars = max(500, min(max_content_chars, 50000))
    async with httpx.AsyncClient(timeout=15) as client:
        it = await _item(client, id)
        if not it:
            raise ValueError(f"No Hacker News item with id {id}")
        kids = it.get("kids", [])
        # Fetch a few extra since some comments are dead/deleted and get skipped.
        comments_task = asyncio.gather(*(_item(client, k) for k in kids[: max_comments * 2]))
        if include_content and it.get("url"):
            content, comments = await asyncio.gather(
                _fetch_content(client, it["url"], max_content_chars), comments_task
            )
        else:
            content, comments = {}, await comments_task
    top = [
        {"author": c.get("by"), "text": _text(c.get("text"))}
        for c in comments
        if c and c.get("text") and not c.get("dead") and not c.get("deleted")
    ][:max_comments]
    return _story(
        it["id"],
        it.get("title", ""),
        it.get("url"),
        author=it.get("by"),
        points=it.get("score"),
        created_at=_iso(it.get("time")),
        text=_text(it.get("text")),
        total_comments=it.get("descendants"),
        **content,
        top_comments=top,
    )


@mcp.tool()
async def search_stories(
    query: str, limit: int = 20, sort_by_date: bool = False
) -> list[dict[str, Any]]:
    """Search Hacker News stories by keyword (Algolia).

    Results are ranked by relevance, or newest first if `sort_by_date` is true.
    `limit` is capped at 50.
    """
    limit = max(1, min(limit, 50))
    endpoint = "search_by_date" if sort_by_date else "search"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{ALGOLIA_API}/{endpoint}",
            params={"query": query, "tags": "story", "hitsPerPage": limit},
        )
        resp.raise_for_status()
    return [
        _story(
            h["objectID"],
            h.get("title") or "",
            h.get("url"),
            points=h.get("points"),
            author=h.get("author"),
            comments=h.get("num_comments"),
            created_at=h.get("created_at"),
        )
        for h in resp.json()["hits"]
    ]


def main() -> None:
    # stdio transport (default). Never print to stdout here: it carries the protocol.
    mcp.run()


if __name__ == "__main__":
    main()
