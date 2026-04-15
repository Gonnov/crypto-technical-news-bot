"""Telegram bot: newsletter-style daily digest + interactive chat."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import storage
from .config import settings
from .renderer import (
    render_all_markdown,
    render_intro,
    render_item,
    render_spotlight,
    render_takeaways,
)
from .sources_client import fetch_all
from .summarizer import DigestBundle, answer_question, generate_digest

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000
INTER_MESSAGE_DELAY = 0.8  # seconds between newsletter messages

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


async def _send_bundle(bot, chat_id: int, bundle: DigestBundle) -> None:
    await _send_html(bot, chat_id, render_intro(bundle))
    await asyncio.sleep(INTER_MESSAGE_DELAY)
    await _send_html(bot, chat_id, render_spotlight(bundle.spotlight))
    total = len(bundle.items)
    for idx, item in enumerate(bundle.items, 1):
        await asyncio.sleep(INTER_MESSAGE_DELAY)
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:  # noqa: BLE001
            pass
        await _send_html(bot, chat_id, render_item(item, idx, total))
    await asyncio.sleep(INTER_MESSAGE_DELAY)
    await _send_html(bot, chat_id, render_takeaways(bundle))


async def _produce_digest(force_refresh: bool = False) -> DigestBundle:
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
        # retry with the duplicate added to the blocklist. Cap retries at 2.
        for attempt in range(2):
            if not storage.spotlight_already_covered(bundle.spotlight.name):
                break
            log.warning(
                "Spotlight '%s' already covered — retrying (attempt %d/2).",
                bundle.spotlight.name, attempt + 1,
            )
            recent = recent + [bundle.spotlight.name]
            bundle = generate_digest(payload, recent_spotlights=recent)
        else:
            if storage.spotlight_already_covered(bundle.spotlight.name):
                log.error("Spotlight '%s' is still a duplicate after retries; accepting.", bundle.spotlight.name)

        storage.save_digest(bundle, render_all_markdown(bundle))
        storage.record_spotlight(bundle.spotlight.name)
    return bundle


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "Web3 Builder Digest Bot\n\n"
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
    bundle = await _produce_digest(force_refresh=False)
    await _send_bundle(ctx.bot, update.effective_chat.id, bundle)


async def cmd_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    _history[update.effective_chat.id].clear()
    bundle = await _produce_digest(force_refresh=True)
    await _send_bundle(ctx.bot, update.effective_chat.id, bundle)


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


async def send_daily_digest(app: Application) -> None:
    log.info("Running daily digest job.")
    _history[settings.telegram_chat_id].clear()
    try:
        bundle = await _produce_digest(force_refresh=True)
        await _send_bundle(app.bot, settings.telegram_chat_id, bundle)
    except Exception as e:  # noqa: BLE001
        log.exception("Daily digest failed")
        try:
            await app.bot.send_message(settings.telegram_chat_id, f"Daily digest failed: {e}")
        except Exception:  # noqa: BLE001
            pass


def build_app() -> Application:
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app
