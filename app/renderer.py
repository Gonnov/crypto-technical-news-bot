"""Render DigestBundle to Telegram-safe HTML messages (one per item)."""
from __future__ import annotations

import html
from datetime import date

from .summarizer import DigestBundle, DigestItem, ProtocolSpotlight

CATEGORY_EMOJI: dict[str, str] = {
    "Research":  "🔬",
    "EIP":       "⚙️",
    "Blog":      "📝",
    "News":      "📰",
    "Metric":    "📊",
    "Repo":      "💻",
}


def _h(s: str) -> str:
    """Escape for Telegram HTML parse_mode."""
    return html.escape(s or "", quote=False)


def render_intro(bundle: DigestBundle, today: date | None = None) -> str:
    today = today or date.today()
    lines: list[str] = []
    lines.append(f"<b>🗞 Web3 Builder Digest — {today.isoformat()}</b>")
    lines.append("")
    lines.append("Hi Valentin,")
    lines.append("")
    lines.append(_h(bundle.intro))
    lines.append("")
    lines.append(f"<b>Today's brief ({len(bundle.items)} items):</b>")
    for i, it in enumerate(bundle.items, 1):
        emoji = CATEGORY_EMOJI.get(it.category, "•")
        lines.append(f"{i}. {emoji} <i>{_h(it.category)}</i> — {_h(it.title)}")
    return "\n".join(lines)


def render_item(item: DigestItem, idx: int, total: int) -> str:
    emoji = CATEGORY_EMOJI.get(item.category, "•")
    lines: list[str] = []
    lines.append(f"{emoji} <b>{_h(item.category.upper())}</b> · {idx}/{total}")
    lines.append("")
    lines.append(f"<b>{_h(item.title)}</b>")
    lines.append(f"<i>{_h(item.hook)}</i>")
    lines.append("")
    for fact in item.facts:
        lines.append(f"• {_h(fact)}")
    if item.builder_angle:
        lines.append("")
        lines.append("<b>For builders:</b>")
        for ba in item.builder_angle:
            lines.append(f"→ {_h(ba)}")
    lines.append("")
    lines.append(f"<i>Source: {_h(item.source)}</i>")
    if item.link:
        lines.append(f'🔗 <a href="{_h(item.link)}">Read more</a>')
    return "\n".join(lines)


def render_spotlight(sp: ProtocolSpotlight) -> str:
    lines: list[str] = []
    lines.append("🔦 <b>PROTOCOL SPOTLIGHT</b>")
    lines.append("")
    chains = ", ".join(sp.chains) if sp.chains else "multi-chain"
    lines.append(f"<b>{_h(sp.name)}</b> · <i>{_h(sp.category)} · {_h(chains)}</i>")
    lines.append(f"<i>{_h(sp.one_liner)}</i>")
    lines.append("")
    lines.append("<b>How it works</b>")
    for b in sp.how_it_works:
        lines.append(f"• {_h(b)}")
    lines.append("")
    lines.append("<b>What makes it good</b>")
    for b in sp.what_makes_it_good:
        lines.append(f"✦ {_h(b)}")
    if sp.risks_and_caveats:
        lines.append("")
        lines.append("<b>Risks &amp; caveats</b>")
        for b in sp.risks_and_caveats:
            lines.append(f"⚠ {_h(b)}")
    if sp.key_numbers:
        lines.append("")
        lines.append("<b>By the numbers</b>")
        for b in sp.key_numbers:
            lines.append(f"📊 {_h(b)}")
    lines.append("")
    lines.append(f"<b>Takeaway for builders:</b> <i>{_h(sp.builder_takeaway)}</i>")
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
        for f in it.facts:
            out.append(f"- {f}")
        if it.builder_angle:
            out.append("\n**For builders:**")
            for ba in it.builder_angle:
                out.append(f"- {ba}")
        out.append(f"\nSource: {it.source} — {it.link}\n")
    out.append("## Takeaways for builders")
    for t in bundle.takeaways:
        out.append(f"- {t}")
    return "\n".join(out)
