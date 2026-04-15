"""
Free-source client.

Aggregates technical web3 content from:
  - EthResear.ch           (Ethereum research — consensus, MEV, PBS, zk…)
  - Ethereum Magicians     (EIP discussions, protocol governance)
  - Vitalik's blog         (authoritative essays)
  - Paradigm research      (VC-grade technical posts)
  - a16z crypto research   (VC-grade technical posts)
  - The Block (RSS)        (industry news, more technical than most)
  - DeFiLlama API          (TVL movers — on-chain data)
  - GitHub trending        (hot web3 repos last 7 days)

No API keys required. All endpoints are public.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx

log = logging.getLogger(__name__)

UA = "messari-digest-bot/1.0 (+https://github.com/)"

RSS_SOURCES: list[dict[str, str]] = [
    {"name": "EthResear.ch",       "url": "https://ethresear.ch/latest.rss",               "kind": "research"},
    {"name": "Ethereum Magicians", "url": "https://ethereum-magicians.org/latest.rss",     "kind": "discussion"},
    {"name": "Vitalik Buterin",    "url": "https://vitalik.eth.limo/feed.xml",             "kind": "blog"},
    {"name": "The Block",          "url": "https://www.theblock.co/rss.xml",               "kind": "news"},
]

# Blogs post infrequently — allow older posts through for them
RECENT_ONLY_KINDS = {"discussion", "news"}


async def _fetch_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url, headers={"user-agent": UA}, timeout=20.0, follow_redirects=True)
    r.raise_for_status()
    return r.content


async def _fetch_rss(client: httpx.AsyncClient, src: dict[str, str], hours: int) -> list[dict[str, Any]]:
    try:
        raw = await _fetch_bytes(client, src["url"])
    except Exception as e:  # noqa: BLE001
        log.warning("RSS fetch failed %s: %s", src["name"], e)
        return []
    feed = feedparser.parse(raw)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    apply_cutoff = src["kind"] in RECENT_ONLY_KINDS
    max_per_source = 8 if apply_cutoff else (15 if src["kind"] == "research" else 6)
    items: list[dict[str, Any]] = []
    for e in feed.entries[:40]:
        published = None
        for key in ("published_parsed", "updated_parsed"):
            tp = getattr(e, key, None)
            if tp:
                published = datetime(*tp[:6], tzinfo=timezone.utc)
                break
        if apply_cutoff and published and published < cutoff:
            continue
        if len(items) >= max_per_source:
            break
        items.append({
            "source": src["name"],
            "kind": src["kind"],
            "title": getattr(e, "title", "").strip(),
            "link": getattr(e, "link", ""),
            "published": published.isoformat() if published else "",
            "summary": (getattr(e, "summary", "") or "")[:800],
            "author": getattr(e, "author", "") if hasattr(e, "author") else "",
        })
    return items


async def _fetch_defillama(client: httpx.AsyncClient, limit: int = 8) -> list[dict[str, Any]]:
    try:
        r = await client.get("https://api.llama.fi/protocols", headers={"user-agent": UA}, timeout=20.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("DeFiLlama fetch failed: %s", e)
        return []
    rows: list[dict[str, Any]] = []
    for p in data:
        change = p.get("change_1d")
        tvl = p.get("tvl") or 0
        if change is None or tvl < 10_000_000:  # ignore noise
            continue
        rows.append({
            "source": "DeFiLlama",
            "kind": "metric",
            "name": p.get("name"),
            "category": p.get("category"),
            "chains": p.get("chains", [])[:5],
            "tvl_usd": tvl,
            "change_1d_pct": change,
            "link": f"https://defillama.com/protocol/{p.get('slug')}",
        })
    rows.sort(key=lambda x: abs(x["change_1d_pct"]), reverse=True)
    return rows[:limit]


async def _fetch_github_trending(client: httpx.AsyncClient, limit: int = 6) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    items: list[dict[str, Any]] = []
    for topic in ("ethereum", "solana", "defi", "zero-knowledge"):
        q = f"topic:{topic} pushed:>{since}"
        try:
            r = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": 3},
                headers={"user-agent": UA, "accept": "application/vnd.github+json"},
                timeout=20.0,
            )
            r.raise_for_status()
            items.extend(r.json().get("items", []))
        except Exception as e:  # noqa: BLE001
            log.warning("GitHub search %s failed: %s", topic, e)
    # Dedup by full_name, keep highest stars
    dedup: dict[str, dict[str, Any]] = {}
    for it in items:
        key = it["full_name"]
        if key not in dedup or it["stargazers_count"] > dedup[key]["stargazers_count"]:
            dedup[key] = it
    items = sorted(dedup.values(), key=lambda x: x["stargazers_count"], reverse=True)[:limit]
    return [
        {
            "source": "GitHub",
            "kind": "repo",
            "name": it["full_name"],
            "description": it.get("description") or "",
            "stars": it.get("stargazers_count"),
            "language": it.get("language"),
            "topics": it.get("topics", []),
            "pushed_at": it.get("pushed_at"),
            "link": it["html_url"],
        }
        for it in items
    ]


async def fetch_all(hours: int = 48) -> dict[str, Any]:
    """Fetch all sources in parallel. Returns sections grouped by kind."""
    async with httpx.AsyncClient() as client:
        rss_tasks = [_fetch_rss(client, s, hours) for s in RSS_SOURCES]
        llama_task = _fetch_defillama(client)
        gh_task = _fetch_github_trending(client)

        rss_results = await asyncio.gather(*rss_tasks, return_exceptions=False)
        llama, gh = await asyncio.gather(llama_task, gh_task)

    # Flatten and group
    research: list[dict[str, Any]] = []
    discussion: list[dict[str, Any]] = []
    blog: list[dict[str, Any]] = []
    news: list[dict[str, Any]] = []
    for items in rss_results:
        for it in items:
            {
                "research": research,
                "discussion": discussion,
                "blog": blog,
                "news": news,
            }[it["kind"]].append(it)

    # Cap and sort by recency
    def _by_date(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(xs, key=lambda x: x.get("published") or "", reverse=True)

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "research": _by_date(research)[:15],
        "discussion": _by_date(discussion)[:4],
        "blog": _by_date(blog)[:8],
        "news": _by_date(news)[:10],
        "metrics": llama,
        "repos": gh,
    }
