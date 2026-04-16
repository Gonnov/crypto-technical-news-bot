"""
Free-source client.

Aggregates technical web3 content across Ethereum, Bitcoin, Solana, Cosmos, L2s:
  - Research: EthResear.ch, Bitcoin Optech, Helius, L2Beat, a16z crypto (via Substack mirror)
  - Blogs: Vitalik, Optimism, Offchain Labs (Arbitrum)
  - Protocol updates: Uniswap Labs, Aave, Lido, EigenLayer, Morpho Labs, Curve, Ondo Finance
  - News: The Block, The Defiant, CoinDesk, Decrypt, Bitcoin Magazine
  - Foundation blogs: Solana Foundation
  - DeFiLlama API: TVL movers + recently-listed protocols (new launches)
  - GitHub trending: hot web3 repos last 7 days

No API keys required. All endpoints are public.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx

log = logging.getLogger(__name__)

UA = "messari-digest-bot/1.0 (+https://github.com/)"

# Titles matching these patterns are recurring meta content (newsletter recaps,
# weekly yield reports, tokenholder updates) and don't belong in a daily digest.
_META_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"newsletter\s*#?\s*\d+", re.I),
    re.compile(r"\b(weekly|monthly|quarterly|yearly)\s+(recap|roundup|digest|report|update)\b", re.I),
    re.compile(r"^\s*recap\s*[:\-]", re.I),
    re.compile(r"\btokenholder\s+(update|report|recap)\b", re.I),
    re.compile(r"\bweek\s*\d+,?\s*\d{4}\b", re.I),
    re.compile(r"\bmonthly\s+recap\s+\w+,?\s*\d{4}\b", re.I),
    re.compile(r"\b(february|march|april|may|june|july|august|september|october|november|december|january)\s+\d{4}\s+(recap|update|roundup|report)\b", re.I),
]

# Exploratory / non-shipping content — applied only to protocol/blog/research
# feeds, where posts often muse about ideas rather than report events.
_EXPLORATORY_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bexplores?\b", re.I),
    re.compile(r"\bexploring\b", re.I),
    re.compile(r"\bconsiders?\b", re.I),
    re.compile(r"\bexamines?\b", re.I),
    re.compile(r"\bmusings?\b", re.I),
    re.compile(r"\bthoughts on\b", re.I),
    re.compile(r"\bcoming soon\b", re.I),
    re.compile(r"\bteas(?:ing|er)\b", re.I),
]

_KINDS_WITHOUT_EXPLORATORY = {"protocol", "blog", "research"}


def _is_meta_title(title: str) -> bool:
    return any(p.search(title or "") for p in _META_TITLE_PATTERNS)


def _is_exploratory_title(title: str) -> bool:
    return any(p.search(title or "") for p in _EXPLORATORY_TITLE_PATTERNS)

RSS_SOURCES: list[dict[str, str]] = [
    # Research (ETH + Bitcoin + Solana + L2 + VC-grade)
    {"name": "EthResear.ch",       "url": "https://ethresear.ch/latest.rss",               "kind": "research"},
    {"name": "Bitcoin Optech",     "url": "https://bitcoinops.org/feed.xml",               "kind": "research"},
    {"name": "Helius",             "url": "https://www.helius.dev/blog/rss.xml",           "kind": "research"},
    {"name": "L2Beat",             "url": "https://medium.com/feed/l2beat",                "kind": "research"},
    {"name": "a16z crypto",        "url": "https://a16zcrypto.substack.com/feed",          "kind": "research"},

    # Personal / team blogs (infrequent, high signal)
    {"name": "Vitalik Buterin",    "url": "https://vitalik.eth.limo/feed.xml",             "kind": "blog"},
    {"name": "Optimism",           "url": "https://medium.com/feed/ethereum-optimism",     "kind": "blog"},
    {"name": "Offchain Labs",      "url": "https://medium.com/feed/offchainlabs",          "kind": "blog"},

    # Protocol updates (Messari-like: upgrades, launches, governance)
    {"name": "Uniswap Labs",       "url": "https://uniswap.substack.com/feed",             "kind": "protocol"},
    {"name": "Aave",               "url": "https://medium.com/feed/aave",                  "kind": "protocol"},
    {"name": "Lido",               "url": "https://blog.lido.fi/rss/",                     "kind": "protocol"},
    {"name": "EigenLayer",         "url": "https://blog.eigenlayer.xyz/rss/",              "kind": "protocol"},
    {"name": "Morpho Labs",        "url": "https://medium.com/feed/morpho-labs",           "kind": "protocol"},
    {"name": "Curve",              "url": "https://news.curve.finance/rss/",               "kind": "protocol"},
    {"name": "Ondo Finance",       "url": "https://medium.com/feed/ondo-finance",          "kind": "protocol"},

    # Ecosystem news (broad — not just ETH)
    {"name": "The Block",          "url": "https://www.theblock.co/rss.xml",               "kind": "news"},
    {"name": "The Defiant",        "url": "https://thedefiant.io/api/feed",                "kind": "news"},
    {"name": "CoinDesk",           "url": "https://www.coindesk.com/arc/outboundfeeds/rss/","kind": "news"},
    {"name": "Decrypt",            "url": "https://decrypt.co/feed",                       "kind": "news"},
    {"name": "Bitcoin Magazine",   "url": "https://bitcoinmagazine.com/.rss/full/",        "kind": "news"},

    # Foundation / ecosystem blog (weekly cadence — needs longer tail than 48h)
    {"name": "Solana Foundation",  "url": "https://solana.com/news/rss.xml",               "kind": "blog"},
]

# Blogs and protocol updates post infrequently — allow older posts through
RECENT_ONLY_KINDS = {"news"}


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
    # Per-source caps kept deliberately tight so no single prolific source
    # (e.g. EigenLayer) can crowd out broader ecosystem coverage.
    if apply_cutoff:
        max_per_source = 3
    elif src["kind"] == "research":
        max_per_source = 4
    elif src["kind"] == "protocol":
        max_per_source = 3
    else:
        max_per_source = 4
    items: list[dict[str, Any]] = []
    exploratory_filter = src["kind"] in _KINDS_WITHOUT_EXPLORATORY
    for e in feed.entries[:40]:
        title = (getattr(e, "title", "") or "").strip()
        if _is_meta_title(title):
            # Skip recurring meta content (newsletter recaps, weekly reports, etc.)
            continue
        if exploratory_filter and _is_exploratory_title(title):
            # Protocol/blog/research posts that are speculation, not shipping events.
            continue
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
            "title": title,
            "link": getattr(e, "link", ""),
            "published": published.isoformat() if published else "",
            "summary": (getattr(e, "summary", "") or "")[:800],
            "author": getattr(e, "author", "") if hasattr(e, "author") else "",
        })
    return items


async def _fetch_defillama(client: httpx.AsyncClient, limit: int = 8) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Single DeFiLlama fetch serving TVL-movers, new-launches, 7d momentum, and a slim logo lookup."""
    try:
        r = await client.get("https://api.llama.fi/protocols", headers={"user-agent": UA}, timeout=20.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("DeFiLlama fetch failed: %s", e)
        return [], [], [], []

    # TVL movers — 24h % change with $10M floor
    movers: list[dict[str, Any]] = []
    for p in data:
        change = p.get("change_1d")
        tvl = p.get("tvl") or 0
        if change is None or tvl < 10_000_000:
            continue
        movers.append({
            "source": "DeFiLlama",
            "kind": "metric",
            "name": p.get("name"),
            "category": p.get("category"),
            "chains": p.get("chains", [])[:5],
            "tvl_usd": tvl,
            "change_1d_pct": change,
            "link": f"https://defillama.com/protocol/{p.get('slug')}",
        })
    movers.sort(key=lambda x: abs(x["change_1d_pct"]), reverse=True)

    # Recently-listed protocols — newly tracked by DeFiLlama with meaningful TVL
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=14)).timestamp())
    launches: list[dict[str, Any]] = []
    for p in data:
        listed = p.get("listedAt")
        tvl = p.get("tvl") or 0
        if not listed or listed < cutoff or tvl < 1_000_000:
            continue
        launches.append({
            "source": "DeFiLlama",
            "kind": "launch",
            "name": p.get("name"),
            "category": p.get("category"),
            "chains": p.get("chains", [])[:5],
            "tvl_usd": tvl,
            "listed_at": datetime.fromtimestamp(listed, tz=timezone.utc).isoformat(),
            "description": (p.get("description") or "")[:400],
            "url": p.get("url") or "",
            "link": f"https://defillama.com/protocol/{p.get('slug')}",
        })
    launches.sort(key=lambda x: x["tvl_usd"], reverse=True)

    # 7d momentum — sustained weekly growth, $20M floor to filter noise.
    # Captures DeFi protocols "making noise" beyond one-day spikes and beyond the
    # launches window (which only catches the first 14 days).
    momentum: list[dict[str, Any]] = []
    launch_slugs = {p.get("slug") for p in data if p.get("listedAt") and p["listedAt"] >= cutoff}
    # Categories we exclude because they're not DeFi protocols per se — CEX reserves,
    # whole chains, and staking services tracked as chain-level numbers move on macro
    # factors, not protocol mechanics.
    _SKIP_CATEGORIES = {"CEX", "Chain", "Exchange", "Services", "Uncollateralized Lending"}
    for p in data:
        change_7d = p.get("change_7d")
        tvl = p.get("tvl") or 0
        if change_7d is None or tvl < 20_000_000 or change_7d < 15:
            continue
        if p.get("category") in _SKIP_CATEGORIES:
            continue
        if p.get("slug") in launch_slugs:
            # Already covered in launches feed — avoid double-counting.
            continue
        momentum.append({
            "source": "DeFiLlama",
            "kind": "momentum",
            "name": p.get("name"),
            "category": p.get("category"),
            "chains": p.get("chains", [])[:5],
            "tvl_usd": tvl,
            "change_7d_pct": change_7d,
            "change_1d_pct": p.get("change_1d"),
            "description": (p.get("description") or "")[:300],
            "link": f"https://defillama.com/protocol/{p.get('slug')}",
        })
    momentum.sort(key=lambda x: x["change_7d_pct"], reverse=True)

    # Slim logo lookup — one row per protocol with >= $1M TVL and a logo URL.
    # Used to attach an image to the spotlight at send time.
    protocol_logos: list[dict[str, Any]] = []
    for p in data:
        if (p.get("tvl") or 0) < 1_000_000:
            continue
        logo = p.get("logo")
        if not logo:
            continue
        protocol_logos.append({
            "name": p.get("name"),
            "slug": p.get("slug"),
            "logo": logo,
        })

    return movers[:limit], launches[:5], momentum[:8], protocol_logos


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")


def _clean_html_summary(raw: str, max_chars: int = 350) -> str:
    """Strip HTML tags from a feed summary and return plain text."""
    if not raw:
        return ""
    # Preserve paragraph breaks before tag removal
    text = raw.replace("</p>", "\n\n").replace("<br />", "\n").replace("<br>", "\n")
    text = _HTML_TAG_RE.sub("", text)
    text = _html.unescape(text)
    # Collapse runs of blank lines and trim intra-line whitespace
    text = "\n".join(_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


async def fetch_og_images(urls: list[str]) -> list[str]:
    """Parallel og:image lookup — returns a list aligned to the input URLs.

    Exposed because the Telegram layer also needs it (recap items carry URLs
    that weren't in our fetch_all payload).
    """
    async with httpx.AsyncClient() as client:
        return list(
            await asyncio.gather(*(_fetch_og_image(client, u) for u in urls))
        )


_RELATIVE_AGE_RE = re.compile(
    r"(\d+)\s*(minute|min|hour|hr|day|week|month|year)s?\s+ago", re.I
)


def _parse_relative_hours(text: str) -> float | None:
    """'18 hours ago' -> 18.0, '2 days ago' -> 48.0, 'yesterday' -> 24.0, 'today' -> 0.0."""
    if not text:
        return None
    t = text.lower().strip()
    if t in ("today", "just now"):
        return 0.0
    if t == "yesterday":
        return 24.0
    m = _RELATIVE_AGE_RE.search(t)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("minute", "min"):
        return n / 60.0
    if unit in ("hour", "hr"):
        return n
    if unit == "day":
        return n * 24.0
    if unit == "week":
        return n * 24.0 * 7.0
    if unit in ("month", "year"):
        return n * 24.0 * 30.0
    return None


async def _fetch_messari(limit: int = 10, max_age_hours: float = 24.0) -> list[dict[str, Any]]:
    """Scrape Messari's homepage for today's fresh free research reports via Playwright.

    Only keeps reports younger than ``max_age_hours`` (default 24h) and skips
    'Enterprise'-tier cards. Messari has no working RSS and their API is paywalled.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        log.warning("Playwright not installed, skipping Messari: %s", e)
        return []

    rows: list[dict[str, Any]] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                )
                page = await ctx.new_page()
                await page.goto(
                    "https://messari.io/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                # Allow React to hydrate and cards to render.
                await page.wait_for_selector(
                    'a.MesArticleCard-card[href^="/report/"]',
                    timeout=15000,
                )
                cards = await page.evaluate(
                    """() => {
                        const seen = new Set();
                        const roots = Array.from(
                            document.querySelectorAll('.articleCard-module__sm6FqW__root')
                        );
                        const rows = [];
                        for (const root of roots) {
                            const a = root.querySelector('a.MesArticleCard-card[href^=\"/report/\"]');
                            if (!a) continue;
                            const href = a.getAttribute('href');
                            if (seen.has(href)) continue;
                            seen.add(href);
                            const lines = (a.innerText || '')
                                .split('\\n').map(s => s.trim()).filter(Boolean);
                            if (!lines.length) continue;
                            const title = lines[0];
                            const author = lines[1] || '';
                            const tier = lines[2] || '';
                            const catEl = root.querySelector('.MesArticleCard-metadata > div');
                            const dateEl = root.querySelector('.MesDate-relativeDate');
                            const category = (catEl?.innerText || '').trim();
                            const relativeDate = (dateEl?.innerText || '').trim();
                            rows.push({ href, title, author, tier, category, relativeDate });
                        }
                        return rows;
                    }"""
                )
            finally:
                await browser.close()
    except Exception as e:  # noqa: BLE001
        log.warning("Messari scrape failed: %s", e)
        return []

    for c in cards:
        if c.get("tier", "").lower() == "enterprise":
            continue
        href = c.get("href") or ""
        if not href.startswith("/report/"):
            continue
        age = _parse_relative_hours(c.get("relativeDate") or "")
        if age is None or age > max_age_hours:
            continue
        rows.append({
            "source": "Messari",
            "title": c.get("title") or "",
            "author": c.get("author") or "",
            "category": c.get("category") or "",
            "relative_date": c.get("relativeDate") or "",
            "age_hours": age,
            "link": f"https://messari.io{href}",
            "image_url": "",
        })
        if len(rows) >= limit:
            break

    # Enrich each report with its og:image (parallel, soft-timeout).
    images = await fetch_og_images([r["link"] for r in rows])
    for r, img in zip(rows, images):
        r["image_url"] = img

    return rows


_OG_IMAGE_RE = re.compile(
    r"""<meta[^>]+property=['"]og:image['"][^>]+content=['"]([^'"]+)""", re.I
)


async def _fetch_og_image(client: httpx.AsyncClient, url: str) -> str:
    """Return the article's og:image URL, or empty string on any failure."""
    if not url:
        return ""
    try:
        r = await client.get(
            url,
            headers={"user-agent": "Mozilla/5.0"},
            timeout=8.0,
            follow_redirects=True,
        )
        m = _OG_IMAGE_RE.search(r.text[:20_000])
        return m.group(1).strip() if m else ""
    except Exception:  # noqa: BLE001
        return ""


async def _fetch_cryptoast(client: httpx.AsyncClient, limit: int = 20, max_age_hours: int = 24) -> list[dict[str, Any]]:
    """Passthrough Cryptoast feed — rendered verbatim, with og:image for each article.

    Filters to articles published within ``max_age_hours`` (default 24h).
    """
    url = "https://cryptoast.fr/feed/"
    try:
        raw = await _fetch_bytes(client, url)
    except Exception as e:  # noqa: BLE001
        log.warning("Cryptoast fetch failed: %s", e)
        return []
    feed = feedparser.parse(raw)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[dict[str, Any]] = []
    for e in feed.entries:
        published = None
        for key in ("published_parsed", "updated_parsed"):
            tp = getattr(e, key, None)
            if tp:
                published = datetime(*tp[:6], tzinfo=timezone.utc)
                break
        if published is None or published < cutoff:
            continue
        items.append({
            "source": "Cryptoast",
            "title": (getattr(e, "title", "") or "").strip(),
            "summary": _clean_html_summary(getattr(e, "summary", "") or ""),
            "link": getattr(e, "link", ""),
            "published": published.isoformat(),
            "author": getattr(e, "author", "") if hasattr(e, "author") else "",
            "image_url": "",
        })
        if len(items) >= limit:
            break

    # Fetch og:image for each article in parallel.
    img_tasks = [_fetch_og_image(client, it["link"]) for it in items]
    img_urls = await asyncio.gather(*img_tasks)
    for it, img in zip(items, img_urls):
        it["image_url"] = img

    return items


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
        cryptoast_task = _fetch_cryptoast(client)

        rss_results = await asyncio.gather(*rss_tasks, return_exceptions=False)
        (llama_movers, llama_launches, llama_momentum, llama_logos), gh, cryptoast = await asyncio.gather(
            llama_task, gh_task, cryptoast_task
        )

    # Messari scrape runs outside the httpx client (Playwright owns its own browser).
    messari = await _fetch_messari()

    # Flatten and group
    research: list[dict[str, Any]] = []
    blog: list[dict[str, Any]] = []
    protocol: list[dict[str, Any]] = []
    news: list[dict[str, Any]] = []
    buckets = {"research": research, "blog": blog, "protocol": protocol, "news": news}
    for items in rss_results:
        for it in items:
            bucket = buckets.get(it["kind"])
            if bucket is not None:
                bucket.append(it)

    # Cap and sort by recency
    def _by_date(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(xs, key=lambda x: x.get("published") or "", reverse=True)

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cryptoast": cryptoast,
        "messari": messari,
        "research": _by_date(research)[:18],
        "blog": _by_date(blog)[:8],
        "protocol": _by_date(protocol)[:14],
        "news": _by_date(news)[:18],
        "launches": llama_launches,
        "momentum": llama_momentum,
        "metrics": llama_movers,
        "repos": gh,
        "protocol_logos": llama_logos,
    }
