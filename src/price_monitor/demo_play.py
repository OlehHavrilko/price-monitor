"""Plays the demo scenarios in order, with pauses, so the whole story can be screen-recorded.

Everything here is the normal program path: it switches the demo catalog, runs real collections
against it and lets the service deliver the queued Telegram messages. The only extra thing it sends
is a short explanation before each step, so a viewer knows what just happened in the shop.
"""
from __future__ import annotations

import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import demo_site, outbox, reports, storage
from .collector import RunResult, collect_once
from .models import SourceConfig

# (title, scenario, number of collections, explanation sent to Telegram).
# Two collections for "missing": REMOVED needs two full runs in a row.
STORY = [
    ("Prices drop on two tracked products", "price_drop", 1,
     "Магазин знизив ціни: настільна лампа 24.90 → 19.90 USD, бездротова миша 12.50 → 11.00 USD. "
     "Зараз сервіс перевірить каталог і повідомить про те, що ви відстежуєте."),
    ("One tracked item sells out", "out_of_stock", 1,
     "У магазині розпродали USB-хаб. Його ви відстежуєте — очікуйте сповіщення про наявність."),
    ("Stock is restored and prices return to normal", "initial", 1,
     "Склад поповнили, ціни повернулися до звичайних. USB-хаб знову в наявності."),
    ("A product disappears from the catalog", "product_missing", 2,
     "З каталогу зник кабель HDMI. Одна перевірка ще нічого не доводить: сторінка могла просто не завантажитися. "
     "Тому сервіс чекає другої повної перевірки підряд і тільки потім повідомляє про зникнення."),
    ("The product returns", "initial", 1,
     "Кабель HDMI знову з'явився в каталозі."),
    ("The source breaks (page layout changed)", "changed_template", 1,
     "Сайт магазину змінив верстку, і сторінки більше не читаються. Сервіс не вважає каталог порожнім і не псує "
     "історію цін — він повідомляє, що моніторинг тимчасово не працює."),
    ("The source recovers", "initial", 1,
     "Магазин знову відповідає як раніше."),
]

# Rules the story needs, with the explanation shown to the viewer. Set through the same
# storage function the /track command uses.
TRACKED = [
    ("00123", dict(drop_percent=Decimal("10")), "Desk lamp Nord — сповістити, якщо ціна впаде на 10% і більше"),
    ("01003", dict(threshold=Decimal("12")), "Wireless mouse — сповістити, якщо ціна стане нижчою за 12"),
    ("01001", {}, "USB hub 4 ports — сповіщати про зміну наявності"),
    ("01002", {}, "HDMI cable 2 m — сповіщати, якщо товар зникне з каталогу або повернеться"),
]


def _collect(conn: sqlite3.Connection, source: SourceConfig, recipients: Sequence[int], lock_dir: Path | None) -> RunResult:
    for _ in range(60):
        result = collect_once(conn, source, recipients=recipients, lock_dir=lock_dir)
        if result.ok or result.run_id is not None:
            return result
        time.sleep(1)  # the service is collecting right now; wait for it to finish
    return result


def play(
    conn: sqlite3.Connection,
    source: SourceConfig,
    state_file: Path,
    reports_dir: Path,
    recipients: Sequence[int] = (),
    lock_dir: Path | None = None,
    pause: float = 20.0,
    say: Callable[[str], None] = print,
) -> Path:
    run_tag = int(time.time() * 1000)
    counter = iter(range(1000))

    def tell(text: str) -> None:
        """Queue an explanation for Telegram; it is delivered in order with the alerts."""
        if recipients:
            outbox.enqueue(conn, recipients, f"demo-{run_tag}-{next(counter)}", text)

    def wait() -> None:
        if recipients:
            say(f"  (waiting {pause:g} s: the service delivers Telegram messages every 15 s)")
        time.sleep(pause)

    say("Demo story. Source: " + source.id + (" (Telegram messages go to the allowlist)" if recipients else " (Telegram is off)"))
    demo_site.write_scenario(state_file, "initial")
    base = _collect(conn, source, recipients, lock_dir)
    if not base.ok:
        raise SystemExit(f"Cannot start: {base.error}")
    say(f"[0] Baseline: {base.product_count} products collected, nothing to compare with yet.")
    for key, options, description in TRACKED:
        storage.set_rule(conn, source.id, key, **options)
        say(f"    tracking {key}: {description}")
    tell(
        f"Демонстрація. Магазин «{source.title or source.id}» — вигаданий, його ціни й наявність змінюються за сценарієм. "
        f"Сервіс уже перевірив каталог ({base.product_count} товарів) і запам'ятав стан. "
        "Ви відстежуєте чотири товари:\n" + "\n".join(f"• {d}" for _, _, d in TRACKED)
        + "\n\nДалі в магазині відбуватимуться зміни. Перед кожною я пояснюю, що сталося, а сервіс сам пише про це."
    )
    wait()

    for number, (title, scenario, runs, explanation) in enumerate(STORY, start=1):
        say(f"[{number}] {title}  (demo scenario: {scenario})")
        tell(f"Крок {number} з {len(STORY)}. {explanation}")
        demo_site.write_scenario(state_file, scenario)
        for _ in range(runs):
            result = _collect(conn, source, recipients, lock_dir)
            if result.ok:
                counts = Counter(e.type for e in result.events)
                found = ", ".join(f"{t} x{n}" for t, n in sorted(counts.items())) or "no changes yet"
                say(f"    collection ok, events: {found}")
            else:
                say(f"    collection failed, previous snapshot kept: {result.error}")
        wait()

    now = datetime.now(timezone.utc)
    path = reports.write_report(conn, reports_dir, source.id, now - timedelta(hours=1), now + timedelta(minutes=1))
    tell(
        "Демонстрацію завершено. Сервіс помітив кожну зміну, не вигадав жодної зайвої й пережив збій сайту. "
        "Історію цін і подій можна отримати як XLSX-звіт командою /report demo 1."
    )
    say(f"Report with the current state and the history of this run: {path}")
    return path
