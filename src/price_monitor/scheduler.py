"""Service mode: scheduled collections, notification delivery and the Telegram bot in one process."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import config, outbox, storage
from .collector import collect_once
from .locking import FileLock

log = logging.getLogger("price_monitor")
DELIVERY_INTERVAL_SECONDS = 15


def _collect_job(source_id: str) -> None:
    source = config.sources()[source_id]
    conn = storage.connect(config.db_path())
    try:
        result = collect_once(conn, source, recipients=_recipients(), lock_dir=config.lock_dir())
    finally:
        conn.close()
    if result.ok:
        log.info("collected %s: %d products, %d events", source_id, result.product_count, len(result.events))
    else:
        log.warning("collection of %s failed: %s", source_id, result.error)


def _recipients() -> tuple[int, ...]:
    return config.allowed_ids() if config.telegram_enabled() else ()


def build_scheduler(deliver) -> AsyncIOScheduler:
    """One job per source. coalesce + max_instances=1 stop missed or overlapping runs from piling up."""
    scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60})
    now = datetime.now(timezone.utc)
    for source in config.sources().values():
        # next_run_time=now: after a restart exactly one fresh collection runs, then the normal interval.
        scheduler.add_job(
            asyncio.to_thread, args=[_collect_job, source.id], trigger="interval", seconds=source.interval,
            next_run_time=now, id=f"collect-{source.id}",
        )
    if deliver is not None:
        scheduler.add_job(deliver, trigger="interval", seconds=DELIVERY_INTERVAL_SECONDS, id="deliver")
    return scheduler


async def run_service() -> int:
    lock = FileLock(config.lock_dir() / "service.lock")
    if not lock.try_acquire():
        log.error("Another price-monitor service already uses %s. Only one instance may run.", config.lock_dir())
        return 1
    try:
        config.db_path().parent.mkdir(parents=True, exist_ok=True)
        storage.connect(config.db_path()).close()  # create / migrate before the jobs start

        bot = dispatcher = None
        deliver = None
        if config.telegram_enabled():
            bot, dispatcher, deliver = _build_bot()
        else:
            log.info("Telegram is off: set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_IDS to enable it.")

        if bot:
            await _set_bot_profile(bot)
        scheduler = build_scheduler(deliver)
        scheduler.start()
        try:
            if dispatcher:
                await dispatcher.start_polling(bot, handle_signals=True)
            else:
                await asyncio.Event().wait()
        finally:
            scheduler.shutdown(wait=True)  # lets an active collection finish
            if bot:
                await bot.session.close()
        return 0
    finally:
        lock.release()


async def _set_bot_profile(bot) -> None:
    """Texts shown on the bot's own page before the first message. A failure here is not fatal."""
    from aiogram.types import BotCommand

    from .telegram import BOT_COMMANDS, BOT_DESCRIPTION, BOT_SHORT_DESCRIPTION

    try:
        await bot.set_my_description(BOT_DESCRIPTION)
        await bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in BOT_COMMANDS])
    except Exception:
        log.exception("Could not update the bot description")


def _build_bot():
    from aiogram import Bot, Dispatcher
    from aiogram.types import FSInputFile, Message

    from .telegram import handle_command, make_sender

    bot = Bot(config.telegram_token())
    dispatcher = Dispatcher()
    loop = asyncio.get_running_loop()
    sender = make_sender(bot, loop)

    def handle(user_id: int, text: str):
        conn = storage.connect(config.db_path())
        try:
            return handle_command(
                conn, user_id, text, sources=config.sources(), allowed_ids=config.allowed_ids(),
                reports_dir=config.reports_dir(),
            )
        finally:
            conn.close()

    @dispatcher.message()
    async def on_message(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return
        reply = await asyncio.to_thread(handle, message.from_user.id, message.text)
        await message.answer(reply.text)
        if reply.file:
            await message.answer_document(FSInputFile(reply.file))

    def deliver_job() -> None:
        conn = storage.connect(config.db_path())
        try:
            outbox.deliver_pending(conn, sender)
        finally:
            conn.close()

    async def deliver() -> None:
        await asyncio.to_thread(deliver_job)

    return bot, dispatcher, deliver
