"""Text of Telegram notifications (Ukrainian, as the spec requires for the interface)."""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from .comparison import (
    AVAILABILITY_CHANGED, BELOW_THRESHOLD, PRICE_DROP_PERCENT, RETURNED, REMOVED, Event, Rule,
)

# PRICE_CHANGED stays in the history only; it is not a reason to message the operator.
NOTIFIABLE = {BELOW_THRESHOLD, PRICE_DROP_PERCENT, AVAILABILITY_CHANGED, REMOVED, RETURNED}

_STOCK = {"IN_STOCK": "в наявності", "OUT_OF_STOCK": "немає в наявності"}


def drop_percent(old: str | None, new: str | None) -> Decimal | None:
    """Exact percentage of a price fall, or None when it is not a fall or the old price is 0."""
    try:
        o, n = Decimal(old), Decimal(new)
    except (InvalidOperation, TypeError):
        return None
    if o <= 0 or n >= o:
        return None
    return ((o - n) / o * 100).quantize(Decimal("0.1"))


def _reason(ev: Event, currency: str, rule: Rule | None) -> str:
    if ev.type == BELOW_THRESHOLD:
        limit = f" (ваше правило: нижче {rule.threshold})" if rule and rule.threshold is not None else ""
        return f"ціна нижче заданого порогу{limit}: {ev.old_value} → {ev.new_value} {currency}"
    if ev.type == PRICE_DROP_PERCENT:
        pct = drop_percent(ev.old_value, ev.new_value)
        limit = f" (ваше правило: від {rule.drop_percent}%)" if rule and rule.drop_percent is not None else ""
        return f"ціна знизилась на {pct}%{limit}: {ev.old_value} → {ev.new_value} {currency}"
    if ev.type == AVAILABILITY_CHANGED:
        return f"наявність: {_STOCK.get(ev.old_value, ev.old_value)} → {_STOCK.get(ev.new_value, ev.new_value)}"
    if ev.type == REMOVED:
        return "товар зник з каталогу (підтверджено двома повними перевірками підряд)"
    if ev.type == RETURNED:
        return f"товар знову є в каталозі, ціна {ev.new_value} {currency}"
    raise ValueError(f"Not a notifiable event: {ev.type}")


def _headline(events: Sequence[Event]) -> str:
    types = {e.type for e in events}
    if types & {BELOW_THRESHOLD, PRICE_DROP_PERCENT}:
        return "Ціна знизилась"
    if REMOVED in types:
        return "Товар зник з каталогу"
    if RETURNED in types:
        return "Товар знову в каталозі"
    new_stock = next((e.new_value for e in events if e.type == AVAILABILITY_CHANGED), None)
    return "Товар закінчився" if new_stock == "OUT_OF_STOCK" else "Товар знову в наявності"


def product_message(
    source_id: str, key: str, name: str, url: str, currency: str, events: Sequence[Event],
    rule: Rule | None = None, source_title: str | None = None,
) -> str:
    """One message per product and run: what happened, to what, why you are told, how to stop."""
    notable = [e for e in events if e.type in NOTIFIABLE]
    lines = [_headline(notable), name, f"Магазин: {source_title or source_id} · код {key}", ""]
    lines += [f"• {_reason(e, currency, rule)}" for e in notable]
    lines += ["", f"Ви отримуєте це, бо відстежуєте товар. Вимкнути: /untrack {source_id} {key}", url]
    return "\n".join(lines)


def source_failed_message(source_id: str, error: str, stopped: bool, source_title: str | None = None) -> str:
    text = f"Магазин «{source_title or source_id}» тимчасово недоступний для моніторингу.\nПричина: {error}"
    if stopped:
        text += "\nДоступ відхилено, збір зупинено. Перевірте умови доступу й запустіть збір вручну командою resume-source."
    else:
        text += "\nСервіс не вважає каталог порожнім і не змінює історію. Повторні однакові збої не надсилаються, поки магазин не відновиться."
    return text


def source_recovered_message(source_id: str, source_title: str | None = None) -> str:
    return f"Магазин «{source_title or source_id}» знову працює, моніторинг продовжено."


def product_card(
    source_id: str, key: str, name: str, currency: str, price: str | None, availability: str | None,
    events: Sequence[Event],
) -> dict:
    """Data for the picture attached to a product alert (see cards.render_alert_card)."""
    types = {e.type for e in events}
    old_price = next((e.old_value for e in events if e.type in (BELOW_THRESHOLD, PRICE_DROP_PERCENT)), None)
    if types & {BELOW_THRESHOLD, PRICE_DROP_PERCENT}:
        headline, tone = "Price drop", "drop"
    elif REMOVED in types:
        headline, tone = "Removed from catalog", "removed"
    elif RETURNED in types:
        headline, tone = "Back in catalog", "stock"
    elif availability == "OUT_OF_STOCK":
        headline, tone = "Out of stock", "out"
    else:
        headline, tone = "Back in stock", "stock"
    stock = {"IN_STOCK": "In stock", "OUT_OF_STOCK": "Out of stock"}.get(availability or "")
    return {
        "source": source_id, "key": key, "name": name, "currency": currency, "price": price or "",
        "old_price": old_price, "headline": headline, "tone": tone, "stock": stock,
    }
