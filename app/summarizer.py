"""Gemini wrapper: structured newsletter digest + grounded chat."""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .config import settings
from .prompts import ARTICLE_DEEP_DIVE_PROMPT, CHAT_SYSTEM_PROMPT, DIGEST_PROMPT_STRUCTURED

log = logging.getLogger(__name__)

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def is_quota_error(exc: BaseException) -> bool:
    """True for Gemini 429 / RESOURCE_EXHAUSTED errors."""
    from google.genai import errors as genai_errors
    if isinstance(exc, genai_errors.APIError):
        if getattr(exc, "code", None) == 429:
            return True
        msg = str(exc)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            return True
    return False


# ---------- Structured output schema ----------

Category = Literal["Research", "Blog", "News", "Launch", "Metric", "Repo"]


class ItemPoint(BaseModel):
    emoji: str = Field(description="One relevant emoji that visually represents this point (e.g. 🔥, ⚙️, 🔗, 🛡️, 📈, 🧩, ⚡, 🧪, 🏛️, 💸, 🔐).")
    title: str = Field(description="3-8 word bold section title for this point, verb-led and specific (e.g. 'New slashing conditions introduced', 'TVL jumps 40% in 48h').")
    detail: str = Field(description="2-4 sentences explaining this point with specifics from the article: names, numbers, mechanisms, contract addresses, chain names. The reader must understand this aspect of the article without clicking through.")


class DigestItem(BaseModel):
    title: str = Field(description="Specific, punchy title, max 70 chars")
    hook: str = Field(description="One editorial sentence, why it matters. Max 140 chars")
    category: Category
    source: str = Field(description="Source name")
    introduction: str = Field(description="1-2 sentence setup framing what the article is about — the subject, the actors, and the overall claim or event. This is the lede, not the analysis.")
    points: list[ItemPoint] = Field(description="3 to 6 structured points covering every important aspect of the article. Each point has its own emoji, bold title, and 2-4 sentence explanation. Together the points must give the reader the full article — they should not need to click through.")
    critical_take: str = Field(description="A balanced editorial take as a single paragraph (3-6 sentences). MUST explicitly state the relevance verdict — e.g. 'This is relevant for X because…' or 'This is mostly noise — the proposal is early-stage and depends on Y that isn't shipping.' Be skeptical where warranted (call out hype, missing details, timeline risk, political economy, centralization). Be neutral where warranted — if the news is genuinely significant, say so clearly. Never vague. Never generic platitudes.")
    link: str = Field(description="Source URL")


class ProtocolSpotlight(BaseModel):
    name: str = Field(description="Protocol name, e.g. 'Pendle', 'Morpho Blue', 'Hyperliquid'")
    category: str = Field(description="Category: DEX, Lending, Perps, LRT, RWA, Stablecoin, Yield, Bridge, etc.")
    chains: list[str] = Field(description="Primary chains it runs on")
    one_liner: str = Field(description="One-sentence pitch, max 160 chars")
    how_it_works: list[str] = Field(description="3-5 bullets on the technical mechanics: AMM curve, interest model, oracle design, vault architecture, etc. Be specific.")
    what_makes_it_good: list[str] = Field(description="2-3 bullets on the genuine design innovation or edge — what it solves that competitors don't. No marketing.")
    risks_and_caveats: list[str] = Field(description="1-2 bullets on real tradeoffs: oracle risk, liquidity fragmentation, centralization vectors, bridge dependencies, etc.")
    key_numbers: list[str] = Field(description="1-3 bullets with concrete metrics (TVL, revenue, users, integrations) — only if you know them factually.")
    builder_takeaway: str = Field(description="1-2 sentences: what a dev should learn or borrow from this design.")
    links: list[str] = Field(description="1-3 URLs: official site, docs, DeFiLlama page.")


class NewsRecapItem(BaseModel):
    headline: str = Field(description="Direct, specific headline — 5 to 12 words. Lead with the concrete noun and the event (e.g. 'SEC approves spot Solana ETF', 'Aave v4 hub contract deployed to mainnet').")
    summary: str = Field(description="1 to 3 sentences describing what actually happened. Just the facts — who did what, when, and the immediate consequence. No analysis here (save it for deep items).")
    source: str = Field(description="Source name")
    link: str = Field(description="Source URL")


class DigestBundle(BaseModel):
    recap: list[NewsRecapItem] = Field(description="8 to 10 headline-style daily crypto news items — the day's broad crypto news scan across institutional moves, regulation, integrations, protocol upgrades, treasury/burn events, launches, and security incidents. Think Cryptoast / CoinDesk / Blockworks homepage. This is the reader's first glance before the deep items.")
    intro: str = Field(description="2-3 sentence setup naming today's big themes")
    spotlight: ProtocolSpotlight = Field(description="One DeFi protocol deep-dive. Rotate daily.")
    items: list[DigestItem] = Field(description="6 to 8 curated items")
    takeaways: list[str] = Field(description="3-5 bullets for builders")


# ---------- Formatting helpers for the prompt ----------

def _fmt_articles(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(none)"
    out = []
    for i, it in enumerate(items, 1):
        out.append(
            f"{i}. {it.get('title','(untitled)')}\n"
            f"   source: {it.get('source','')}\n"
            f"   published: {it.get('published','')}\n"
            f"   author: {it.get('author','')}\n"
            f"   link: {it.get('link','')}\n"
            f"   summary: {it.get('summary','')[:600]}"
        )
    return "\n\n".join(out)


def _fmt_metrics(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(none)"
    out = []
    for i, it in enumerate(items, 1):
        out.append(
            f"{i}. {it['name']} ({it.get('category','?')}) on {it.get('chains')}: "
            f"TVL ${it['tvl_usd']:,.0f}, 1d change {it['change_1d_pct']:+.2f}%\n"
            f"   link: {it['link']}"
        )
    return "\n".join(out)


def _fmt_repos(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(none)"
    out = []
    for i, it in enumerate(items, 1):
        out.append(
            f"{i}. {it['name']} ({it.get('language','?')}, {it.get('stars','?')}★): "
            f"{it.get('description','')[:200]}\n"
            f"   topics: {it.get('topics', [])[:6]}\n"
            f"   link: {it['link']}"
        )
    return "\n".join(out)


def _fmt_launches(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(none)"
    out = []
    for i, it in enumerate(items, 1):
        chains = ", ".join(it.get("chains") or []) or "?"
        out.append(
            f"{i}. {it['name']} ({it.get('category','?')}) on {chains}\n"
            f"   listed: {it.get('listed_at','')}, TVL ${it['tvl_usd']:,.0f}\n"
            f"   description: {it.get('description') or '(none)'}\n"
            f"   site: {it.get('url','')}\n"
            f"   link: {it['link']}"
        )
    return "\n\n".join(out)


def _fmt_momentum(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(none)"
    out = []
    for i, it in enumerate(items, 1):
        chains = ", ".join(it.get("chains") or []) or "?"
        d1 = it.get("change_1d_pct")
        d1_str = f"{d1:+.1f}%" if d1 is not None else "?"
        out.append(
            f"{i}. {it['name']} ({it.get('category','?')}) on {chains}\n"
            f"   TVL ${it['tvl_usd']:,.0f}, 7d {it['change_7d_pct']:+.1f}% (1d {d1_str})\n"
            f"   description: {it.get('description') or '(none)'}\n"
            f"   link: {it['link']}"
        )
    return "\n\n".join(out)


# ---------- Core API ----------

def generate_digest(
    payload: dict[str, Any],
    today: date | None = None,
    recent_spotlights: list[str] | None = None,
) -> DigestBundle:
    today = today or date.today()
    recent_spotlights = recent_spotlights or []
    prompt = DIGEST_PROMPT_STRUCTURED.format(
        current_date=today.isoformat(),
        research_block=_fmt_articles(payload.get("research", [])),
        blog_block=_fmt_articles(payload.get("blog", [])),
        protocol_block=_fmt_articles(payload.get("protocol", [])),
        news_block=_fmt_articles(payload.get("news", [])),
        launches_block=_fmt_launches(payload.get("launches", [])),
        momentum_block=_fmt_momentum(payload.get("momentum", [])),
        metrics_block=_fmt_metrics(payload.get("metrics", [])),
        repos_block=_fmt_repos(payload.get("repos", [])),
        recent_spotlights=", ".join(recent_spotlights) if recent_spotlights else "(none yet)",
    )
    resp = client().models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.35,
            response_mime_type="application/json",
            response_schema=DigestBundle,
        ),
    )
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, DigestBundle):
        return parsed
    # Fallback: parse JSON manually
    return DigestBundle.model_validate_json(resp.text or "{}")


def summarize_article(
    *,
    url: str,
    title: str,
    source: str,
    body: str,
) -> DigestItem:
    """Generate a structured deep-dive for a single fetched article."""
    prompt = ARTICLE_DEEP_DIVE_PROMPT.format(
        source=source or "",
        url=url or "",
        title=title or "",
        body=(body or "")[:15_000],
    )
    resp = client().models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.35,
            response_mime_type="application/json",
            response_schema=DigestItem,
        ),
    )
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, DigestItem):
        return parsed
    return DigestItem.model_validate_json(resp.text or "{}")


def answer_question(
    question: str,
    payload: dict[str, Any] | None,
    digest_md: str | None,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Answer a user question with today's context + Google Search grounding."""
    history = history or []
    context_parts = []
    if digest_md:
        context_parts.append(f"TODAY'S DIGEST:\n{digest_md}")
    if payload:
        context_parts.append(f"TODAY'S RAW DATA (JSON):\n{json.dumps(payload, default=str)[:60000]}")
    if not context_parts:
        context_parts.append("(No data fetched yet today.)")

    contents: list[types.Content] = []
    for turn in history:
        role = "user" if turn["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=turn["content"])]))
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part(text="\n\n".join(context_parts) + f"\n\nQUESTION: {question}")],
        )
    )

    resp = client().models.generate_content(
        model=settings.gemini_model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=CHAT_SYSTEM_PROMPT,
            temperature=0.4,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    text = resp.text or "(empty response)"

    try:
        gm = resp.candidates[0].grounding_metadata  # type: ignore[attr-defined]
        chunks = getattr(gm, "grounding_chunks", None) or []
        urls = []
        for c in chunks:
            web = getattr(c, "web", None)
            if web and getattr(web, "uri", None):
                urls.append(f"- {web.title or web.uri}: {web.uri}")
        if urls:
            text += "\n\n**Sources**\n" + "\n".join(urls[:8])
    except Exception:  # noqa: BLE001
        pass

    return text
