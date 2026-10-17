"""Button menu of the Telegram bot, as plain data.

Every screen is a View (text + rows of buttons) built from the database, so the whole menu can be
tested without Telegram. The aiogram layer only turns a View into a message with an inline keyboard.
callback_data stays under Telegram's 64-byte limit: product keys are the shop's own codes.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from . import outbox, reports, storage
from .models import SourceConfig

PAGE_SIZE = 8
DENIED = "Доступ заборонено."
STOCK = {"IN_STOCK": "в наявності", "OUT_OF_STOCK": "немає в наявності", "UNKNOWN": "невідомо"}


@dataclass(frozen=True)
class Button:
    label: str
    data: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class View:
    text: str
    rows: list[list[Button]] = field(default_factory=list)
    file: Path | None = None


def about_text(sources: dict[str, SourceConfig]) -> str:
    shops = "\n".join(f"• {s.title or s.id} — {s.description}" for s in sources.values())
    return (
        "Price Monitor стежить за цінами та наявністю товарів в інтернет-каталозі й повідомляє, коли щось змінилося.\n\n"
        "Навіщо: не відкривати сайт вручну й не пропустити зниження ціни чи появу товару. Ви обираєте товари "
        "кнопкою «Стежити», сервіс сам перевіряє каталог за розкладом і пише лише про значущі зміни: ціна впала, "
        "товар закінчився чи з'явився знову, зник з каталогу. Якщо сайт змінився й сторінки не читаються, "
        "сервіс не вигадує зміни, а повідомляє про несправність.\n\n"
        f"Що перевіряється зараз:\n{shops}\n\n"
        "У повідомленні: назва товару, магазин, що змінилося (було → стало), чому ви його отримали й посилання. "
        "Для зниження ціни додається картка з картинкою. За запитом формується XLSX-звіт з історією змін.\n\n"
    )


def source_line(conn: sqlite3.Connection, source_id: str, title: str | None = None) -> str:
    last = storage.last_success_run(conn, source_id)
    health = conn.execute("SELECT * FROM source_health WHERE source_id = ?", (source_id,)).fetchone()
    when = last["finished_at"] if last else "ще не було"
    if health and health["stopped"]:
        state = f"зупинено: {health['last_error']}"
    elif health and health["alerted"]:
        state = f"помилка: {health['last_error']}"
    else:
        state = "працює" if last else "даних немає"
    return f"{title or source_id}: остання успішна перевірка {when}; стан — {state}"


def main_buttons() -> list[list[Button]]:
    return [
        [Button("Магазини", "s"), Button("Відстежувані", "w")],
        [Button("Звіт XLSX", "rm"), Button("Стан сервісу", "st")],
        [Button("Про проєкт", "h")],
    ]


def main_view(sources: dict[str, SourceConfig]) -> View:
    return View("Головне меню. Оберіть розділ або напишіть частину назви товару, щоб його знайти.", main_buttons())


def _back(target: str = "m", label: str = "« Назад") -> list[Button]:
    return [Button(label, target)]


def _price_text(price: str | None, currency: str) -> str:
    return f"{price} {currency}" if price else "ціну не вказано"


def _latest(conn: sqlite3.Connection, source_id: str, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT p.name, p.url, o.price, o.currency, o.availability, o.observed_at, s.removed "
        "FROM products p LEFT JOIN observations o ON o.id = (SELECT MAX(id) FROM observations "
        "WHERE source_id = p.source_id AND product_key = p.product_key) "
        "LEFT JOIN product_state s ON s.source_id = p.source_id AND s.product_key = p.product_key "
        "WHERE p.source_id = ? AND p.product_key = ?",
        (source_id, key),
    ).fetchone()


def _product_button(source_id: str, key: str, name: str, price: str | None, currency: str | None) -> list[Button] | None:
    data = f"i:{source_id}:{key}"
    if len(data.encode()) > 64:
        return None  # cannot be addressed from a button; still reachable through /track
    label = name if len(name) <= 38 else name[:37] + "…"
    return [Button(f"{label} · {price}" if price else label, data)]


def _product_page(conn: sqlite3.Connection, sources: dict[str, SourceConfig], source_id: str, page: int) -> View:
    last = storage.last_success_run(conn, source_id)
    title = sources[source_id].title or source_id
    if not last:
        return View("Для цього магазину ще немає успішної перевірки.", [_back(f"s:{source_id}")])
    total = conn.execute("SELECT COUNT(*) FROM observations WHERE run_id = ?", (last["id"],)).fetchone()[0]
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    rows = conn.execute(
        "SELECT * FROM observations WHERE run_id = ? ORDER BY name, product_key LIMIT ? OFFSET ?",
        (last["id"], PAGE_SIZE, page * PAGE_SIZE),
    ).fetchall()
    buttons = [b for r in rows if (b := _product_button(source_id, r["product_key"], r["name"], r["price"], r["currency"]))]
    nav = []
    if page > 0:
        nav.append(Button("« Раніше", f"p:{source_id}:{page - 1}"))
    if page < pages - 1:
        nav.append(Button("Далі »", f"p:{source_id}:{page + 1}"))
    text = f"{title}: товари з останньої перевірки, сторінка {page + 1} з {pages} (усього {total}). Оберіть товар."
    return View(text, buttons + ([nav] if nav else []) + [_back(f"s:{source_id}")])


def _product_detail(
    conn: sqlite3.Connection, sources: dict[str, SourceConfig], source_id: str, key: str, note: str = ""
) -> View:
    row = _latest(conn, source_id, key)
    if row is None:
        return View("Такого товару не знайдено.", [_back(f"s:{source_id}")])
    rule = conn.execute(
        "SELECT * FROM tracking_rules WHERE source_id = ? AND product_key = ?", (source_id, key)
    ).fetchone()
    if rule is None:
        tracking = "ні"
    else:
        parts = []
        if rule["threshold"]:
            parts.append(f"ціна нижче {rule['threshold']}")
        if rule["drop_percent"]:
            parts.append(f"зниження ціни від {rule['drop_percent']}%")
        parts.append("наявність і зникнення")
        tracking = "так — " + ", ".join(parts)
    status = "зник з каталогу" if row["removed"] else STOCK.get(row["availability"], "невідомо")
    text = (
        (f"{note}\n\n" if note else "")
        + f"{row['name']}\nМагазин: {sources[source_id].title or source_id} · код {key}\n"
        f"Ціна: {_price_text(row['price'], row['currency'] or '')}\nНаявність: {status}\n"
        f"Остання перевірка: {row['observed_at']}\nВідстежується: {tracking}"
    )
    track = (
        [Button("Перестати стежити", f"u:{source_id}:{key}")]
        if rule
        else [Button("Стежити: ціна впаде на 10%+", f"t:{source_id}:{key}:d")]
    )
    rows = [track]
    if not rule:
        rows.append([Button("Стежити: лише наявність", f"t:{source_id}:{key}:a")])
    # Telegram rejects URL buttons whose host is not public ("http://demo:8080/..." in Docker).
    if "." in (urlparse(row["url"]).hostname or "") and urlparse(row["url"]).scheme in ("http", "https"):
        rows.append([Button("Відкрити в магазині", url=row["url"])])
    rows.append(_back(f"p:{source_id}:0", "« До списку товарів"))
    return View(text, rows)


def search(conn: sqlite3.Connection, user_id: int, query: str, sources: dict[str, SourceConfig], allowed_ids) -> View:
    if user_id not in allowed_ids:
        return View(DENIED)
    q = query.strip()
    if len(q) < 2:
        return View("Напишіть хоча б 2 символи назви або код товару.", [_back()])
    like = f"%{q.replace('%', '').replace('_', '')}%"
    rows = conn.execute(
        "SELECT p.source_id, p.product_key, p.name, o.price, o.currency FROM products p "
        "LEFT JOIN observations o ON o.id = (SELECT MAX(id) FROM observations "
        "WHERE source_id = p.source_id AND product_key = p.product_key) "
        "WHERE p.name LIKE ? OR p.product_key = ? ORDER BY p.name LIMIT ?",
        (like, q, PAGE_SIZE + 1),
    ).fetchall()
    rows = [r for r in rows if r["source_id"] in sources]
    if not rows:
        return View(f"За запитом «{q}» нічого не знайдено.", [_back()])
    buttons = [b for r in rows[:PAGE_SIZE] if (b := _product_button(r["source_id"], r["product_key"], r["name"], r["price"], r["currency"]))]
    more = "\nПоказано перші 8, уточніть запит." if len(rows) > PAGE_SIZE else ""
    return View(f"Знайдено за запитом «{q}»:{more}", buttons + [_back()])


def handle_callback(
    conn: sqlite3.Connection,
    user_id: int,
    data: str,
    *,
    sources: dict[str, SourceConfig],
    allowed_ids: tuple[int, ...],
    reports_dir: Path,
) -> View:
    if user_id not in allowed_ids:
        return View(DENIED)
    kind, _, rest = data.partition(":")

    if kind == "m":
        return main_view(sources)
    if kind == "h":
        return View(about_text(sources), [_back()])
    if kind == "s" and not rest:
        lines = [source_line(conn, s.id, s.title) for s in sources.values()]
        return View("Магазини:\n" + "\n".join(lines), [[Button(s.title or s.id, f"s:{s.id}")] for s in sources.values()] + [_back()])

    if kind == "s" and rest in sources:
        src = sources[rest]
        text = f"{src.title or src.id}\n{src.description}\n\n{source_line(conn, rest, src.title)}"
        return View(
            text,
            [
                [Button("Товари", f"p:{rest}:0")],
                [Button("Звіт за 1 день", f"r:{rest}:1"), Button("Звіт за 7 днів", f"r:{rest}:7")],
                _back("s"),
            ],
        )

    if kind == "p":
        source_id, _, page = rest.rpartition(":")
        if source_id in sources and page.isdigit():
            return _product_page(conn, sources, source_id, int(page))

    if kind in ("i", "t", "u"):
        source_id, _, tail = rest.partition(":")
        if source_id not in sources:
            return main_view(sources)
        if kind == "i":
            return _product_detail(conn, sources, source_id, tail)
        if kind == "t":
            key, _, preset = tail.rpartition(":")
            try:
                storage.set_rule(conn, source_id, key, drop_percent=Decimal("10") if preset == "d" else None)
            except ValueError as exc:
                return View(str(exc), [_back()])
            what = "падінні ціни від 10%, а також про зміну наявності й зникнення" if preset == "d" else "зміні наявності й зникненні з каталогу"
            return _product_detail(conn, sources, source_id, key, f"Готово. Я напишу вам при {what}.")
        storage.remove_rule(conn, source_id, tail)
        return _product_detail(conn, sources, source_id, tail, "Відстеження вимкнено.")

    if kind == "w":
        rows = conn.execute(
            "SELECT r.source_id, r.product_key, p.name FROM tracking_rules r "
            "JOIN products p USING (source_id, product_key) ORDER BY r.source_id, p.name"
        ).fetchall()
        rows = [r for r in rows if r["source_id"] in sources]
        if not rows:
            return View("Поки нічого не відстежується. Відкрийте магазин → Товари → товар → «Стежити».", [_back()])
        buttons = [b for r in rows if (b := _product_button(r["source_id"], r["product_key"], r["name"], None, None))]
        return View(f"Відстежується товарів: {len(rows)}. Натисніть товар, щоб побачити деталі.", buttons[:20] + [_back()])

    if kind == "rm":
        return View(
            "XLSX-звіт: поточний стан товарів і історія змін. Оберіть магазин і період.",
            [[Button(s.title or s.id, f"s:{s.id}")] for s in sources.values()] + [_back()],
        )

    if kind == "r":
        source_id, _, days = rest.rpartition(":")
        if source_id in sources and days.isdigit():
            now = datetime.now(timezone.utc)
            try:
                path = reports.write_report(conn, reports_dir, source_id, now - timedelta(days=int(days)), now + timedelta(minutes=1))
            except reports.ReportError as exc:
                return View(f"Звіт не створено: {exc}", [_back(f"s:{source_id}")])
            return View(f"Звіт за {days} дн.: {sources[source_id].title or source_id}.", [_back(f"s:{source_id}")], file=path)

    if kind == "st":
        counts = outbox.counts(conn)
        lines = [source_line(conn, s.id, s.title) for s in sources.values()]
        lines.append(f"Черга сповіщень: очікують {counts['pending']}, надіслано {counts['sent']}, помилок доставки {counts['failed']}.")
        failure = outbox.last_failure(conn)
        if failure:
            lines.append(f"Остання помилка доставки — {failure}")
        return View("\n".join(lines), [_back()])

    return main_view(sources)
