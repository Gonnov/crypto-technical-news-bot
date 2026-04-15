"""Entrypoint: starts the Telegram bot with an internal daily job."""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from .config import settings
from .telegram_bot import build_app, send_daily_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
log = logging.getLogger("main")


async def _daily_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await send_daily_digest(ctx.application)


def main() -> None:
    app = build_app()

    tz = ZoneInfo(settings.schedule_tz)
    run_at = dt.time(hour=settings.schedule_hour, minute=settings.schedule_minute, tzinfo=tz)

    app.job_queue.run_daily(_daily_job, time=run_at, name="daily_digest")
    log.info(
        "Daily digest scheduled at %s %s. Starting polling...",
        run_at.strftime("%H:%M"), settings.schedule_tz,
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
