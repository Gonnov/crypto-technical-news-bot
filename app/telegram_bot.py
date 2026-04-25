"""Telegram bot: newsletter-style daily digest + interactive chat."""
from __future__ import annotations

import asyncio
import hashlib
import html as _html
import logging
from collections import defaultdict
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import storage
from .article_fetcher import fetch_article
from .config import settings
from .renderer import (
    render_all_markdown,
    render_cryptoast_card,
    render_intro,
    render_item,
    render_messari_card,
    render_recap_card,
    render_spotlight,
    render_spotlight_body,
    render_spotlight_header,
    render_takeaways,
)
from .sources_client import fetch_all, fetch_og_images
from .summarizer import DigestBundle, answer_question, generate_digest, is_quota_error, summarize_article

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000
INTER_MESSAGE_DELAY = 1.2  # seconds between newsletter messages (avoids Telegram flood control)
INFO_CALLBACK_PREFIX = "info:"
DAILY_RETRY_DELAY = 3600  # 1 hour between daily-digest retries on quota errors
DAILY_MAX_RETRIES = 3


def _h_esc(s: str) -> str:
    """HTML-escape for Telegram parse_mode=HTML."""
    return _html.escape(s or "", quote=False)


def _truncate_html(text: str, limit: int) -> str:
    """Truncate Telegram-HTML safely: cut on a line boundary and close any open tags.

    Telegram only accepts a small tag set (b, i, a, code, pre, u, s). We only
    use b/i/a here, so a light-touch closer is enough.
    """
    if len(text) <= limit:
        return text
    # Trim to the last newline before the budget (leave ~20 chars for closers).
    budget = max(1, limit - 20)
    cut = text.rfind("\n", 0, budget)
    trimmed = text[: cut if cut > 0 else budget].rstrip()
    # Close any open tags we might have cut through.
    for tag in ("b", "i"):
        opens = trimmed.count(f"<{tag}>") + trimmed.count(f"<{tag} ")
        closes = trimmed.count(f"</{tag}>")
        if opens > closes:
            trimmed += f"</{tag}>" * (opens - closes)
    # Also close any dangling <a ...> link tags.
    if trimmed.count("<a ") > trimmed.count("</a>"):
        trimmed += "</a>" * (trimmed.count("<a ") - trimmed.count("</a>"))
    return trimmed + "\n…"


def _article_id(url: str) -> str:
    """Short stable id for callback_data (Telegram limits it to 64 bytes)."""
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:12]


def _favicon_url(article_url: str) -> str:
    """Fallback image when og:image isn't available — Google's s2 favicon service."""
    if not article_url:
        return ""
    try:
        from urllib.parse import urlparse
        host = urlparse(article_url).netloc
        if not host:
            return ""
        return f"https://www.google.com/s2/favicons?domain={host}&sz=128"
    except Exception:  # noqa: BLE001
        return ""


def _slug(name: str) -> str:
    """Loose name match: lowercase, alphanumeric only."""
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _find_protocol_logo(spotlight_name: str, protocols: list[dict] | None) -> str:
    """Match the spotlight name against DeFiLlama's protocol list for a logo URL."""
    if not spotlight_name or not protocols:
        return ""
    target = _slug(spotlight_name)
    if not target:
        return ""
    # Exact slug match first
    for p in protocols:
        if _slug(p.get("name", "")) == target or _slug(p.get("slug", "")) == target:
            return p.get("logo") or ""
    # Fallback: the first protocol whose slug starts with the target (handles "Morpho" -> "MorphoBlue")
    for p in protocols:
        pslug = _slug(p.get("name", ""))
        if pslug and (pslug.startswith(target) or target.startswith(pslug)):
            return p.get("logo") or ""
    return ""


def _info_keyboard(article_id: str, extra_url: str | None = None) -> InlineKeyboardMarkup:
    """Inline keyboard with 'Plus d'infos' callback and optional direct link."""
    row: list[InlineKeyboardButton] = []
    if extra_url:
        row.append(InlineKeyboardButton("🔗 Lire", url=extra_url))
    row.append(InlineKeyboardButton("ℹ️ Plus d'infos", callback_data=f"{INFO_CALLBACK_PREFIX}{article_id}"))
    return InlineKeyboardMarkup([row])


def _collect_articles(payload: dict[str, Any] | None, bundle: DigestBundle | None) -> dict[str, dict[str, Any]]:
    """Build the {article_id: {source,title,url}} map for today's callbacks."""
    mapping: dict[str, dict[str, Any]] = {}
    payload = payload or {}
    for it in payload.get("cryptoast", []) or []:
        url = it.get("link")
        if not url:
            continue
        mapping[_article_id(url)] = {"source": "Cryptoast", "title": it.get("title", ""), "url": url}
    for it in payload.get("messari", []) or []:
        url = it.get("link")
        if not url:
            continue
        mapping[_article_id(url)] = {"source": "Messari", "title": it.get("title", ""), "url": url}
    if bundle is not None:
        for r in bundle.recap or []:
            url = r.link
            if not url:
                continue
            mapping[_article_id(url)] = {"source": r.source, "title": r.headline, "url": url}
    return mapping

# In-memory per-chat conversation history
_history: dict[int, list[dict[str, str]]] = defaultdict(list)


def _authorized(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id == settings.telegram_chat_id


async def _send_html(bot, chat_id: int, text: str) -> None:
    """Send one message as HTML with plain-text fallback. Splits if too long."""
    if not text:
        return
    # Split on double-newline if over limit (rare for items)
    chunks: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > TELEGRAM_LIMIT:
            if buf:
                chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    for c in chunks:
        try:
            await bot.send_message(
                chat_id, c, parse_mode=ParseMode.HTML, disable_web_page_preview=False
            )
        except Exception as e:  # noqa: BLE001
            log.warning("HTML send failed (%s); retrying plain.", e)
            # Strip tags as best effort
            import re
            plain = re.sub(r"<[^>]+>", "", c)
            await bot.send_message(chat_id, plain)


async def _send_photo_card(
    bot, chat_id: int, image_url: str, caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Send a photo with caption + optional inline keyboard. Fall back to text if the upload fails."""
    try:
        await bot.send_photo(
            chat_id, image_url, caption=caption, parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("send_photo failed, falling back to text: %s", e)
        try:
            await bot.send_message(
                chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=keyboard,
            )
        except Exception as e2:  # noqa: BLE001
            log.warning("send_message fallback also failed: %s", e2)


async def _send_text_card(
    bot, chat_id: int, text: str, keyboard: InlineKeyboardMarkup | None = None
) -> None:
    try:
        await bot.send_message(
            chat_id, text, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, reply_markup=keyboard,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("send_message card failed: %s", e)


async def _send_bundle(bot, chat_id: int, bundle: DigestBundle, payload: dict | None = None) -> None:
    payload = payload or {}

    # Persist article map for callback resolution.
    storage.save_articles(_collect_articles(payload, bundle))

    # --- Intro FIRST (greeting card) ---
    await _send_text_card(bot, chat_id, render_intro(bundle))
    await asyncio.sleep(INTER_MESSAGE_DELAY)

    # --- Cryptoast: one photo card per article (title + image + Lire + Plus d'infos) ---
    cryptoast = payload.get("cryptoast") or []
    if cryptoast:
        await _send_text_card(bot, chat_id, "🗞 <b>CRYPTOAST · ACTU DU JOUR</b>")
        await asyncio.sleep(INTER_MESSAGE_DELAY)
        for it in cryptoast:
            url = it.get("link", "")
            kb = _info_keyboard(_article_id(url), extra_url=url or None)
            caption = render_cryptoast_card(it)
            img = it.get("image_url") or ""
            if img:
                await _send_photo_card(bot, chat_id, img, caption, kb)
            else:
                await _send_text_card(bot, chat_id, caption, kb)
            await asyncio.sleep(INTER_MESSAGE_DELAY)

    # --- Messari: one card per research report (image + title + author + Lire + Plus d'infos) ---
    messari = payload.get("messari") or []
    if messari:
        await _send_text_card(bot, chat_id, "📚 <b>MESSARI · TOP RESEARCH</b>")
        await asyncio.sleep(INTER_MESSAGE_DELAY)
        for it in messari:
            url = it.get("link", "")
            kb = _info_keyboard(_article_id(url), extra_url=url or None)
            caption = render_messari_card(it)
            img = it.get("image_url") or ""
            if img:
                await _send_photo_card(bot, chat_id, img, caption, kb)
            else:
                await _send_text_card(bot, chat_id, caption, kb)
            await asyncio.sleep(INTER_MESSAGE_DELAY)

    # --- News recap: one card per headline (image + title + summary + Read + More info) ---
    if bundle.recap:
        await _send_text_card(bot, chat_id, "📣 <b>NEWS RECAP</b>")
        await asyncio.sleep(INTER_MESSAGE_DELAY)
        recap_urls = [r.link or "" for r in bundle.recap]
        recap_images = await fetch_og_images(recap_urls)
        for r, img in zip(bundle.recap, recap_images):
            kb = _info_keyboard(_article_id(r.link), extra_url=r.link or None)
            caption = render_recap_card(r.model_dump())
            # Fall back to the source domain's favicon if og:image is missing.
            if not img:
                img = _favicon_url(r.link)
            if img:
                await _send_photo_card(bot, chat_id, img, caption, kb)
            else:
                await _send_text_card(bot, chat_id, caption, kb)
            await asyncio.sleep(INTER_MESSAGE_DELAY)

    # --- Spotlight: photo + short header caption, then full body as text.
    # The body auto-splits on paragraph boundaries via _send_html when it
    # exceeds 4000 chars, so it can naturally span 2-3 messages when Gemini
    # produces deep mechanical content.
    sp = bundle.spotlight
    logo = _find_protocol_logo(sp.name, payload.get("protocol_logos"))
    header_caption = render_spotlight_header(sp)
    if len(header_caption) > 1024:
        header_caption = _truncate_html(header_caption, 1024)

    sent_photo = False
    if logo:
        try:
            await bot.send_photo(chat_id, logo, caption=header_caption, parse_mode=ParseMode.HTML)
            sent_photo = True
        except Exception as e:  # noqa: BLE001
            log.warning("spotlight photo send failed (%s): %s — falling back to text header", logo, e)
    if not sent_photo:
        await _send_text_card(bot, chat_id, header_caption)
    await asyncio.sleep(INTER_MESSAGE_DELAY)

    # Body — _send_html splits on \n\n when above TELEGRAM_LIMIT (4000 chars).
    await _send_html(bot, chat_id, render_spotlight_body(sp))
    await asyncio.sleep(INTER_MESSAGE_DELAY)

    # --- Takeaways ---
    await _send_text_card(bot, chat_id, render_takeaways(bundle))


async def _produce_digest(force_refresh: bool = False) -> tuple[DigestBundle, dict]:
    payload = None if force_refresh else storage.load_payload()
    if payload is None:
        log.info("Fetching sources (force=%s)...", force_refresh)
        payload = await fetch_all()
        storage.save_payload(payload)

    bundle = None if force_refresh else storage.load_digest_bundle()
    if bundle is None:
        log.info("Generating newsletter digest with Gemini...")
        recent = storage.load_recent_spotlights()
        bundle = generate_digest(payload, recent_spotlights=recent)

        # Guard: if Gemini picked a protocol already covered (even via a variant),
        # retry with the duplicate added to the blocklist. Cap retries at 4.
        for attempt in range(4):
            if not storage.spotlight_already_covered(bundle.spotlight.name):
                break
            log.warning(
                "Spotlight '%s' already covered — retrying (attempt %d/4).",
                bundle.spotlight.name, attempt + 1,
            )
            recent = recent + [bundle.spotlight.name]
            bundle = generate_digest(payload, recent_spotlights=recent)
        else:
            if storage.spotlight_already_covered(bundle.spotlight.name):
                log.error("Spotlight '%s' is still a duplicate after retries; accepting.", bundle.spotlight.name)

        storage.save_digest(bundle, render_all_markdown(bundle))
        storage.record_spotlight(bundle.spotlight.name)
    return bundle, payload


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    from datetime import date
    await update.message.reply_text(
        f"☀️ Hi Valentin — {date.today().isoformat()}\n\n"
        "Commands:\n"
        "/digest — show today's digest\n"
        "/refresh — re-fetch sources and regenerate\n"
        "/reset — clear chat history\n\n"
        "Or send any question about today's data."
    )


async def cmd_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    bundle, payload = await _produce_digest(force_refresh=False)
    await _send_bundle(ctx.bot, update.effective_chat.id, bundle, payload)


async def cmd_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    _history[update.effective_chat.id].clear()
    bundle, payload = await _produce_digest(force_refresh=True)
    await _send_bundle(ctx.bot, update.effective_chat.id, bundle, payload)


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    _history[update.effective_chat.id].clear()
    await update.message.reply_text("Conversation history cleared.")


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update) or not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    question = update.message.text.strip()
    await update.message.chat.send_action(ChatAction.TYPING)

    payload = storage.load_payload()
    digest_md = storage.load_digest_markdown()
    hist = _history[chat_id]

    try:
        answer = answer_question(question, payload, digest_md, history=hist)
    except Exception as e:  # noqa: BLE001
        log.exception("answer_question failed")
        if is_quota_error(e):
            await update.message.reply_text(
                "Gemini is rate-limited right now — please retry in a few minutes."
            )
        else:
            await update.message.reply_text(f"Error while answering: {e}")
        return

    hist.append({"role": "user", "content": question})
    hist.append({"role": "assistant", "content": answer})
    if len(hist) > 40:
        del hist[:-40]

    # Chat answer uses plain/markdown to avoid HTML parse issues
    chunks: list[str] = []
    buf = ""
    for para in answer.split("\n\n"):
        if len(buf) + len(para) + 2 > TELEGRAM_LIMIT:
            if buf:
                chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    for c in chunks:
        try:
            await ctx.bot.send_message(chat_id, c, parse_mode=ParseMode.MARKDOWN)
        except Exception:  # noqa: BLE001
            await ctx.bot.send_message(chat_id, c)


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'Plus d'infos' button clicks: fetch the article and send a deep-dive."""
    q = update.callback_query
    if q is None:
        return
    chat = update.effective_chat
    if chat is None or chat.id != settings.telegram_chat_id:
        await q.answer("Not authorized.", show_alert=True)
        return
    data = (q.data or "").strip()
    if not data.startswith(INFO_CALLBACK_PREFIX):
        await q.answer()
        return
    article_id = data[len(INFO_CALLBACK_PREFIX):]
    article = storage.get_article(article_id)
    if not article:
        await q.answer("Article not found in today's index.", show_alert=True)
        return

    await q.answer("Fetching article…")
    try:
        await ctx.bot.send_chat_action(chat.id, ChatAction.TYPING)
    except Exception:  # noqa: BLE001
        pass

    source = article.get("source") or ""
    title = article.get("title") or ""
    url = article.get("url") or ""

    try:
        fetched = await fetch_article(url, source=source)
    except Exception as e:  # noqa: BLE001
        log.exception("fetch_article failed")
        await _send_text_card(ctx.bot, chat.id, f"<i>Article fetch failed:</i> {e}")
        return

    body = fetched.get("text") or ""
    if not body:
        await _send_text_card(
            ctx.bot, chat.id,
            "<i>Couldn't extract article body — the site may be JS-heavy or paywalled.</i>",
        )
        return

    # Run the blocking Gemini call off the event loop.
    try:
        item = await asyncio.to_thread(
            summarize_article,
            url=url,
            title=fetched.get("title") or title,
            source=source,
            body=body,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("summarize_article failed")
        if is_quota_error(e):
            await _send_text_card(
                ctx.bot, chat.id,
                "<i>Gemini is rate-limited right now — please retry in a few minutes.</i>",
            )
        else:
            await _send_text_card(ctx.bot, chat.id, f"<i>Summary generation failed:</i> {e}")
        return

    await _send_html(ctx.bot, chat.id, render_item(item, idx=1, total=1))


async def send_daily_digest(app: Application, attempt: int = 0) -> None:
    log.info("Running daily digest job (attempt %d).", attempt)
    if attempt == 0:
        _history[settings.telegram_chat_id].clear()
    try:
        # On retries reuse the cached payload — only Gemini gets re-called.
        bundle, payload = await _produce_digest(force_refresh=(attempt == 0))
        await _send_bundle(app.bot, settings.telegram_chat_id, bundle, payload)
    except Exception as e:  # noqa: BLE001
        if is_quota_error(e) and attempt < DAILY_MAX_RETRIES:
            log.warning(
                "Gemini quota exhausted on daily digest (attempt %d/%d). Retrying in %ds.",
                attempt + 1, DAILY_MAX_RETRIES, DAILY_RETRY_DELAY,
            )
            app.job_queue.run_once(
                _retry_daily_job,
                when=DAILY_RETRY_DELAY,
                data={"attempt": attempt + 1},
                name=f"daily_digest_retry_{attempt + 1}",
            )
            return
        log.exception("Daily digest failed")
        try:
            suffix = " (quota exhausted, no more retries today)" if is_quota_error(e) else ""
            await app.bot.send_message(
                settings.telegram_chat_id, f"Daily digest failed{suffix}: {e}"
            )
        except Exception:  # noqa: BLE001
            pass


async def _retry_daily_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    attempt = (ctx.job.data or {}).get("attempt", 1) if ctx.job else 1
    await send_daily_digest(ctx.application, attempt=attempt)


def build_app() -> Application:
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=rf"^{INFO_CALLBACK_PREFIX}"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app
