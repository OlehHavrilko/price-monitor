"""Text of Telegram notifications (Ukrainian, as the spec requires for the interface)."""
from __future__ import annotations

from collections.abc import Sequence

from .comparison import (
    AVAILABILITY_CHANGED, BELOW_THRESHOLD, PRICE_DROP_PERCENT, RETURNED, REMOVED, Event,
)

# PRICE_CHANGED stays in the history only; it is not a reason to message the operator.
NOTIFIABLE = {BELOW_THRESHOLD, PRICE_DROP_PERCENT, AVAILABILITY_CHANGED, REMOVED, RETURNED}

_STOCK = {"IN_STOCK": "в наявності", "OUT_OF_STOCK": "немає в наявності"}


def _reason(ev: Event, currency: str) -> str:
    if ev.type == BELOW_THRESHOLD:
        return f"ціна нижче заданого порогу: {ev.old_value} → {ev.new_value} {currency}"
    if ev.type == PRICE_DROP_PERCENT:
        return f"ціна знизилась не менше ніж на задану межу: {ev.old_value} → {ev.new_value} {currency}"
    if ev.type == AVAILABILITY_CHANGED:
        return f"наявність: {_STOCK.get(ev.old_value, ev.old_value)} → {_STOCK.get(ev.new_value, ev.new_value)}"
    if ev.type == REMOVED:
        return "товар зник з каталогу (підтверджено двома повними обходами)"
    if ev.type == RETURNED:
        return f"товар знову є в каталозі, ціна {ev.new_value} {currency}"
    raise ValueError(f"Not a notifiable event: {ev.type}")


def product_message(source_id: str, key: str, name: str, url: str, currency: str, events: Sequence[Event]) -> str:
    """One message per product and run, listing every reason that fired."""
    lines = [f"{name} ({source_id}, код {key})"]
    lines += [f"• {_reason(e, currency)}" for e in events if e.type in NOTIFIABLE]
    lines.append(url)
    return "\n".join(lines)


def source_failed_message(source_id: str, error: str, stopped: bool) -> str:
    text = f"Джерело «{source_id}» недоступне: {error}"
    if stopped:
        text += "\nДоступ відхилено, збір зупинено. Перевірте умови доступу й запустіть збір вручну командою resume-source."
    else:
        text += "\nНаступні однакові збої не надсилаються, поки джерело не відновиться."
    return text


def source_recovered_message(source_id: str) -> str:
    return f"Джерело «{source_id}» знову працює."
