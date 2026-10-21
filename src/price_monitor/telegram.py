"""Telegram commands. `handle_command` is plain Python and tested without a network or a token;
`classify_send_error` and `make_sender` are the only parts that touch aiogram.

Interface text is Ukrainian. Only IDs from the allowlist get answers; everything else is refused.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import cards, menu, outbox, reports, storage
from .models import SourceConfig

log = logging.getLogger("price_monitor")
MAX_LIST_LINES = 30
DEFAULT_REPORT_DAYS = 7
MAX_REPORT_DAYS = 90

BOT_SHORT_DESCRIPTION = "Стежить за цінами й наявністю товарів і пише, коли вони змінюються. Демо на вигаданому магазині."
BOT_DESCRIPTION = (
    "Price Monitor — демонстраційний проєкт. Бот стежить за цінами й наявністю товарів у каталозі магазину "
    "і надсилає повідомлення, коли щось змінилося: товар подешевшав, закінчився, з'явився знову або зник з каталогу. "
    "Приклад: у вигаданому магазині Demo Shop лампа подешевшала з 24.90 до 19.90 USD — бот пише про це "
    "з карткою товару. Натисніть «Почати», щоб дізнатися більше. Магазин і товари вигадані."
)
BOT_COMMANDS = [
    ("menu", "Головне меню"),
    ("sources", "Магазини й стан останньої перевірки"),
    ("products", "Товари з останньої перевірки"),
    ("tracked", "Що відстежується"),
    ("track", "Відстежувати товар"),
    ("untrack", "Припинити відстеження"),
    ("report", "XLSX-звіт зі змінами"),
    ("status", "Черга сповіщень і помилки"),
    ("help", "Що це і як користуватися"),
]

COMMANDS_HELP = """Команди:
/sources — магазини та стан останньої перевірки
/products <магазин> — товари з останньої перевірки
/tracked — що відстежується
/track <магазин> <код> [below=ціна] [drop=відсотки] — відстежувати товар
/untrack <магазин> <код> — припинити відстеження
/report [магазин] [днів] — XLSX-звіт (за замовчуванням 7 днів, не більше 90)
/status — черга сповіщень і помилки доставки
Код товару — рядок, ведучі нулі важливі: 00123 і 123 різні товари."""


HELP = COMMANDS_HELP


@dataclass(frozen=True)
class Reply:
    text: str
    file: Path | None = None
    buttons: list | None = None  # rows of menu.Button for an inline keyboard


def _parse_options(parts: list[str]) -> tuple[Decimal | None, Decimal | None]:
    threshold = drop = None
    for part in parts:
        name, _, value = part.partition("=")
        if name not in ("below", "drop") or not value:
            raise ValueError(f"Невідомий параметр «{part}». Допустимо below=ціна та drop=відсотки.")
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"«{value}» — не число.")
        if not number.is_finite():
            raise ValueError(f"«{value}» — не число.")
        if name == "below":
            threshold = number
        else:
            drop = number
    return threshold, drop


def handle_command(
    conn: sqlite3.Connection,
    user_id: int,
    text: str,
    *,
    sources: dict[str, SourceConfig],
    allowed_ids: tuple[int, ...],
    reports_dir: Path,
) -> Reply:
    parts = text.strip().split()
    command = parts[0].split("@")[0].lower() if parts and parts[0].startswith("/") else ""
    args = parts[1:]
    if user_id not in allowed_ids:
        if command in ("/start", "/help"):
            return Reply(
                menu.about_text(sources)
                + "Керування (відстеження, звіти) доступне лише власнику бота. "
                "Як це виглядає в роботі — у README проєкту та в демонстраційному відео."
            )
        return Reply("Доступ заборонено.")
    if not command:
        view = menu.search(conn, user_id, text, sources, allowed_ids)
        return Reply(view.text, buttons=view.rows)

    def known_source(arg: str) -> SourceConfig | None:
        return sources.get(arg)

    if command in ("/start", "/help", "/menu"):
        extra = "" if command == "/menu" else menu.about_text(sources) + COMMANDS_HELP
        return Reply(extra or menu.main_view(sources).text, buttons=menu.main_buttons())

    if command == "/sources":
        return Reply("\n".join(menu.source_line(conn, s, sources[s].title) for s in sources))

    if command == "/products":
        if len(args) != 1 or not known_source(args[0]):
            return Reply(f"Вкажіть джерело: /products <джерело>. Доступні: {', '.join(sources)}.")
        last = storage.last_success_run(conn, args[0])
        if not last:
            return Reply("Для цього джерела ще немає успішного збору.")
        rows = conn.execute(
            "SELECT * FROM observations WHERE run_id = ? ORDER BY product_key", (last["id"],)
        ).fetchall()
        lines = [menu.source_line(conn, args[0], sources[args[0]].title)]
        lines += [f"{r['product_key']} — {r['name']} — {r['price'] or 'ціну не вказано'} {r['currency']} — {r['availability']}" for r in rows[:MAX_LIST_LINES]]
        if len(rows) > MAX_LIST_LINES:
            lines.append(f"…і ще {len(rows) - MAX_LIST_LINES}. Повний список — у звіті /report.")
        return Reply("\n".join(lines))

    if command == "/tracked":
        rows = conn.execute(
            "SELECT r.*, p.name FROM tracking_rules r JOIN products p USING (source_id, product_key) "
            "ORDER BY r.source_id, r.product_key"
        ).fetchall()
        if not rows:
            return Reply("Нічого не відстежується. Додайте: /track <джерело> <код>.")
        lines = []
        for r in rows:
            opts = []
            if r["threshold"]:
                opts.append(f"нижче {r['threshold']}")
            if r["drop_percent"]:
                opts.append(f"зниження ≥ {r['drop_percent']}%")
            lines.append(f"{r['source_id']} {r['product_key']} — {r['name']}" + (f" ({', '.join(opts)})" if opts else ""))
        return Reply("\n".join(lines))

    if command == "/track":
        if len(args) < 2 or not known_source(args[0]):
            return Reply(f"Формат: /track <джерело> <код> [below=ціна] [drop=відсотки]. Джерела: {', '.join(sources)}.")
        try:
            threshold, drop = _parse_options(args[2:])
            storage.set_rule(conn, args[0], args[1], threshold, drop)
        except ValueError as exc:
            return Reply(str(exc))
        return Reply(f"Відстежується: {args[0]} {args[1]}.")

    if command == "/untrack":
        if len(args) != 2 or not known_source(args[0]):
            return Reply("Формат: /untrack <джерело> <код>.")
        storage.remove_rule(conn, args[0], args[1])
        return Reply(f"Відстеження вимкнено: {args[0]} {args[1]}.")

    if command == "/report":
        source_arg = args[0] if args and not args[0].isdigit() else (next(iter(sources)) if len(sources) == 1 else None)
        days_args = args[1:] if args and not args[0].isdigit() else args
        if source_arg is None or not known_source(source_arg) or len(days_args) > 1:
            return Reply(f"Формат: /report [джерело] [днів]. Джерела: {', '.join(sources)}.")
        try:
            days = int(days_args[0]) if days_args else DEFAULT_REPORT_DAYS
        except ValueError:
            return Reply("Кількість днів має бути цілим числом.")
        if not 1 <= days <= MAX_REPORT_DAYS:
            return Reply(f"Період — від 1 до {MAX_REPORT_DAYS} днів.")
        now = datetime.now(timezone.utc)
        try:
            path = reports.write_report(conn, reports_dir, source_arg, now - timedelta(days=days), now + timedelta(minutes=1))
        except reports.ReportError as exc:
            return Reply(f"Звіт не створено: {exc}")
        return Reply(f"Звіт за {days} дн., джерело {source_arg}.", file=path)

    if command == "/status":
        c = outbox.counts(conn)
        lines = [menu.source_line(conn, s, sources[s].title) for s in sources]
        lines.append(f"Черга сповіщень: очікують {c['pending']}, надіслано {c['sent']}, помилок доставки {c['failed']}.")
        failure = outbox.last_failure(conn)
        if failure:
            lines.append(f"Остання помилка доставки — {failure}")
        return Reply("\n".join(lines))

    return Reply(HELP)


def classify_send_error(exc: BaseException) -> Exception:
    """Map an aiogram or network error to the outbox's permanent/temporary distinction."""
    from aiogram.exceptions import (
        TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramUnauthorizedError,
    )

    if isinstance(exc, TelegramRetryAfter):
        return outbox.RateLimitedError(str(exc), exc.retry_after)
    if isinstance(exc, (TelegramForbiddenError, TelegramUnauthorizedError)):
        return outbox.PermanentDeliveryError(str(exc))
    if isinstance(exc, TelegramBadRequest) and "chat not found" in str(exc).lower():
        return outbox.PermanentDeliveryError(str(exc))
    # Timeouts, network failures and 5xx: the message may still have been delivered.
    return outbox.TemporaryDeliveryError(f"{type(exc).__name__}: {exc}")


def make_sender(bot, loop: asyncio.AbstractEventLoop, timeout: float = 30.0):
    """Blocking sender for outbox.deliver_pending, to be called from a worker thread."""

    from aiogram.types import BufferedInputFile, LinkPreviewOptions

    def picture(card: dict | None) -> bytes | None:
        if not card:
            return None
        try:
            return cards.render_alert_card(card)
        except Exception:  # a broken picture must not block the alert itself
            log.exception("Could not render the alert card; sending text only")
            return None

    def send(chat_id: int, text: str, card: dict | None = None) -> None:
        png = picture(card)
        if png:
            coro = bot.send_photo(chat_id, BufferedInputFile(png, "alert.png"), caption=text)
        else:
            coro = bot.send_message(chat_id, text, link_preview_options=LinkPreviewOptions(is_disabled=True))
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            future.result(timeout=timeout)
        except Exception as exc:  # every error is classified, none is swallowed
            raise classify_send_error(exc) from exc

    return send
