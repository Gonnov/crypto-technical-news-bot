"""Render DigestBundle to Telegram-safe HTML messages (one per item)."""
from __future__ import annotations

import html
from datetime import date

from .summarizer import DigestBundle, DigestItem, ProtocolSpotlight

CATEGORY_EMOJI: dict[str, str] = {
    "Research":  "🔬",
    "Blog":      "📝",
    "News":      "📰",
    "Launch":    "🚀",
    "Metric":    "📊",
    "Repo":      "💻",
}


def _h(s: str) -> str:
    """Escape for Telegram HTML parse_mode."""
    return html.escape(s or "", quote=False)


def render_cryptoast_card(item: dict) -> str:
    """Caption text for a single Cryptoast article (sent with the og:image)."""
    title = item.get("title") or "(sans titre)"
    return f"<b>{_h(title)}</b>"


def render_messari_card(item: dict) -> str:
    """Caption for a Messari research report (title + author)."""
    title = item.get("title") or "(no title)"
    author = item.get("author") or ""
    lines = [f"<b>{_h(title)}</b>"]
    if author:
        lines.append(f"<i>by {_h(author)}</i>")
    return "\n".join(lines)


def render_recap_card(item: dict) -> str:
    """One recap headline — title + short summary, rendered as a message.

    `item` is a NewsRecapItem model_dump(). The message carries inline buttons
    (Read / More info) attached by the sender.
    """
    title = item.get("headline") or "(untitled)"
    summary = item.get("summary") or ""
    source = item.get("source") or ""
    lines = [f"<b>📰 {_h(title)}</b>"]
    if summary:
        lines.append(_h(summary))
    if source:
        lines.append(f"<i>{_h(source)}</i>")
    return "\n".join(lines)


def render_messari_feed(items: list[dict]) -> str:
    """Messari's top free research reports (scraped, not curated by Gemini)."""
    if not items:
        return ""
    lines: list[str] = [
        "📚 <b>MESSARI · TOP RESEARCH TODAY</b>",
        "<i>Les meilleurs rapports Messari du jour.</i>",
        "",
    ]
    for i, it in enumerate(items, 1):
        title = it.get("title") or "(no title)"
        author = it.get("author") or ""
        link = it.get("link") or ""
        lines.append(f"<b>{i}. {_h(title)}</b>")
        if author:
            lines.append(f"<i>by {_h(author)}</i>")
        if link:
            lines.append(f'🔗 <a href="{_h(link)}">Read on Messari</a>')
        lines.append("")
    return "\n".join(lines).rstrip()


def render_daily_feed(items: list[dict]) -> str:
    """Cryptoast passthrough — each article title + plain-text summary + link.

    Not produced by Gemini; rendered directly from RSS so the reader sees the
    same daily news scan they'd get on cryptoast.fr/actu/.
    """
    if not items:
        return ""
    lines: list[str] = [
        "🗞 <b>DAILY CRYPTO FEED · Cryptoast</b>",
        "<i>Les titres du jour, directement depuis la rédaction.</i>",
        "",
    ]
    for i, it in enumerate(items, 1):
        title = it.get("title") or "(sans titre)"
        summary = it.get("summary") or ""
        link = it.get("link") or ""
        lines.append(f"<b>{i}. {_h(title)}</b>")
        if summary:
            lines.append(_h(summary))
        if link:
            lines.append(f'🔗 <a href="{_h(link)}">Lire l’article</a>')
        lines.append("")
    return "\n".join(lines).rstrip()


def render_recap(bundle: DigestBundle) -> str:
    lines: list[str] = ["📣 <b>NEWS RECAP</b>", "<i>The day's headlines across crypto — what's happening right now.</i>", ""]
    for i, r in enumerate(bundle.recap, 1):
        lines.append(f"<b>{i}. {_h(r.headline)}</b>")
        lines.append(_h(r.summary))
        link_part = f'<a href="{_h(r.link)}">read</a>' if r.link else ""
        meta = f"<i>{_h(r.source)}</i>"
        if link_part:
            meta = f"{meta} · {link_part}"
        lines.append(meta)
        lines.append("")
    return "\n".join(lines).rstrip()


def render_intro(bundle: DigestBundle, today: date | None = None) -> str:
    today = today or date.today()
    lines: list[str] = [
        f"☀️ <b>Web3 Builder Digest — {today.isoformat()}</b>",
        "",
        "<b>Hi Valentin 👋</b>",
        "",
        _h(bundle.intro),
    ]
    return "\n".join(lines)


def render_item(item: DigestItem, idx: int, total: int) -> str:
    emoji = CATEGORY_EMOJI.get(item.category, "•")
    lines: list[str] = []
    lines.append(f"{emoji} <b>{_h(item.category.upper())}</b> · {idx}/{total}")
    lines.append("")
    lines.append(f"<b>{_h(item.title)}</b>")
    lines.append(f"<i>{_h(item.hook)}</i>")
    lines.append("")
    lines.append(_h(item.introduction))
    for p in item.points:
        lines.append("")
        lines.append(f"{p.emoji} <b>{_h(p.title)}</b>")
        lines.append(_h(p.detail))
    lines.append("")
    lines.append("🧐 <b>My take</b>")
    lines.append(_h(item.critical_take))
    lines.append("")
    lines.append(f"<i>Source: {_h(item.source)}</i>")
    if item.link:
        lines.append(f'🔗 <a href="{_h(item.link)}">Read more</a>')
    return "\n".join(lines)


def render_spotlight_header(sp: ProtocolSpotlight) -> str:
    """Short header — fits in a Telegram photo caption (≤1024 chars)."""
    chains = ", ".join(sp.chains) if sp.chains else "multi-chain"
    lines = [
        "🔦 <b>PROTOCOL SPOTLIGHT</b>",
        f"<b>🟣 {_h(sp.name)}</b>",
        f"<i>{_h(sp.category)} · {_h(chains)}</i>",
        "",
        f"<i>{_h(sp.one_liner)}</i>",
    ]
    return "\n".join(lines)


def render_spotlight_body(sp: ProtocolSpotlight) -> str:
    """Full mechanical body — sent as a follow-up text message (auto-splits on \n\n)."""
    lines: list[str] = []
    lines.append("⚙️ <b>How it works</b>")
    for b in sp.how_it_works:
        lines.append(f"• {_h(b)}")
    lines.append("")
    lines.append("✨ <b>What makes it good</b>")
    for b in sp.what_makes_it_good:
        lines.append(f"• {_h(b)}")
    if sp.risks_and_caveats:
        lines.append("")
        lines.append("⚠️ <b>Risks &amp; caveats</b>")
        for b in sp.risks_and_caveats:
            lines.append(f"• {_h(b)}")
    if sp.key_numbers:
        lines.append("")
        lines.append("📊 <b>By the numbers</b>")
        for b in sp.key_numbers:
            lines.append(f"• {_h(b)}")
    lines.append("")
    lines.append(f"💡 <b>Takeaway:</b> <i>{_h(sp.builder_takeaway)}</i>")
    if sp.links:
        lines.append("")
        link_parts = [f'<a href="{_h(u)}">{_h(u.split("//")[-1].split("/")[0])}</a>' for u in sp.links[:3]]
        lines.append("🔗 " + " · ".join(link_parts))
    return "\n".join(lines)


def render_spotlight(sp: ProtocolSpotlight) -> str:
    lines: list[str] = []
    lines.append("🔦 <b>PROTOCOL SPOTLIGHT</b>")
    lines.append("━━━━━━━━━━━━━━━━━━")
    chains = ", ".join(sp.chains) if sp.chains else "multi-chain"
    lines.append(f"<b>🟣 {_h(sp.name)}</b>")
    lines.append(f"<i>{_h(sp.category)} · {_h(chains)}</i>")
    lines.append("")
    lines.append(f"<i>{_h(sp.one_liner)}</i>")
    lines.append("")
    lines.append("⚙️ <b>How it works</b>")
    for b in sp.how_it_works:
        lines.append(f"• {_h(b)}")
    lines.append("")
    lines.append("✨ <b>What makes it good</b>")
    for b in sp.what_makes_it_good:
        lines.append(f"• {_h(b)}")
    if sp.risks_and_caveats:
        lines.append("")
        lines.append("⚠️ <b>Risks &amp; caveats</b>")
        for b in sp.risks_and_caveats:
            lines.append(f"• {_h(b)}")
    if sp.key_numbers:
        lines.append("")
        lines.append("📊 <b>By the numbers</b>")
        for b in sp.key_numbers:
            lines.append(f"• {_h(b)}")
    lines.append("")
    lines.append(f"💡 <b>Takeaway:</b> <i>{_h(sp.builder_takeaway)}</i>")
    if sp.links:
        lines.append("")
        link_parts = [f'<a href="{_h(u)}">{_h(u.split("//")[-1].split("/")[0])}</a>' for u in sp.links[:3]]
        lines.append("🔗 " + " · ".join(link_parts))
    return "\n".join(lines)


def render_takeaways(bundle: DigestBundle) -> str:
    lines: list[str] = []
    lines.append("<b>🧠 Takeaways for builders</b>")
    lines.append("")
    for t in bundle.takeaways:
        lines.append(f"• {_h(t)}")
    lines.append("")
    lines.append("<i>Stay building.</i>")
    return "\n".join(lines)


def render_all_markdown(bundle: DigestBundle, today: date | None = None) -> str:
    """Plain-markdown flattening used as context for the chat LLM."""
    today = today or date.today()
    out: list[str] = []
    out.append(f"# Web3 Builder Digest — {today.isoformat()}\n")
    if bundle.recap:
        out.append("## News recap")
        for i, r in enumerate(bundle.recap, 1):
            out.append(f"{i}. **{r.headline}** — {r.summary}")
            out.append(f"   Source: {r.source} — {r.link}")
        out.append("")
    out.append(bundle.intro + "\n")
    sp = bundle.spotlight
    out.append(f"## Protocol Spotlight: {sp.name} ({sp.category} on {', '.join(sp.chains)})")
    out.append(f"_{sp.one_liner}_\n")
    out.append("**How it works**")
    for b in sp.how_it_works: out.append(f"- {b}")
    out.append("\n**What makes it good**")
    for b in sp.what_makes_it_good: out.append(f"- {b}")
    if sp.risks_and_caveats:
        out.append("\n**Risks**")
        for b in sp.risks_and_caveats: out.append(f"- {b}")
    if sp.key_numbers:
        out.append("\n**Numbers**")
        for b in sp.key_numbers: out.append(f"- {b}")
    out.append(f"\n**Builder takeaway:** {sp.builder_takeaway}")
    if sp.links: out.append("Links: " + " · ".join(sp.links))
    out.append("")
    for i, it in enumerate(bundle.items, 1):
        out.append(f"## {i}. [{it.category}] {it.title}")
        out.append(f"_{it.hook}_\n")
        out.append(it.introduction)
        for p in it.points:
            out.append(f"\n### {p.emoji} {p.title}")
            out.append(p.detail)
        out.append(f"\n**My take:** {it.critical_take}")
        out.append(f"\nSource: {it.source} — {it.link}\n")
    out.append("## Takeaways for builders")
    for t in bundle.takeaways:
        out.append(f"- {t}")
    return "\n".join(out)
