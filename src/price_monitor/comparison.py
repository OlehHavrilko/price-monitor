"""Pure comparison of two snapshots. No database access here, so every rule is easy to test."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import Availability, ProductObservation

# A product absent from this many consecutive full successful runs is reported as removed.
REMOVED_AFTER_MISSING_RUNS = 2

PRICE_CHANGED = "PRICE_CHANGED"
BELOW_THRESHOLD = "BELOW_THRESHOLD"
PRICE_DROP_PERCENT = "PRICE_DROP_PERCENT"
AVAILABILITY_CHANGED = "AVAILABILITY_CHANGED"
REMOVED = "REMOVED"
RETURNED = "RETURNED"


@dataclass(frozen=True)
class Rule:
    threshold: Decimal | None = None  # notify when price goes from >= threshold to < threshold
    drop_percent: Decimal | None = None  # notify when price falls by at least this many percent


@dataclass(frozen=True)
class ProductState:
    missing_count: int = 0
    removed: bool = False


@dataclass(frozen=True)
class Event:
    product_key: str
    type: str
    old_value: str | None
    new_value: str | None


def compare(
    previous: dict[str, ProductObservation],
    current: dict[str, ProductObservation],
    known_keys: set[str],
    states: dict[str, ProductState],
    rules: dict[str, Rule],
) -> tuple[list[Event], dict[str, ProductState]]:
    """Compare the new full snapshot with the last successful one.

    `known_keys` is every product ever seen at this source; absence is judged against it, not only
    against `previous`, so a product keeps its missing-run counter across the runs it is not in.
    Returns events and the new state for every known product.
    """
    events: list[Event] = []
    new_states: dict[str, ProductState] = {}

    for key, cur in current.items():
        state = states.get(key, ProductState())
        if state.removed:
            events.append(Event(key, RETURNED, None, str(cur.price)))
        elif key in previous:
            events.extend(_changes(previous[key], cur, rules.get(key)))
        new_states[key] = ProductState()

    for key in known_keys - current.keys():
        state = states.get(key, ProductState())
        if state.removed:
            new_states[key] = state
            continue
        missing = state.missing_count + 1
        if missing >= REMOVED_AFTER_MISSING_RUNS:
            events.append(Event(key, REMOVED, None, None))
            new_states[key] = ProductState(missing_count=missing, removed=True)
        else:
            new_states[key] = ProductState(missing_count=missing)

    return events, new_states


def _changes(old: ProductObservation, new: ProductObservation, rule: Rule | None) -> list[Event]:
    key = new.product_key
    events: list[Event] = []
    if old.price != new.price:
        events.append(Event(key, PRICE_CHANGED, str(old.price), str(new.price)))
        if rule is not None:
            if rule.threshold is not None and old.price >= rule.threshold > new.price:
                events.append(Event(key, BELOW_THRESHOLD, str(old.price), str(new.price)))
            # Decimal arithmetic only. A zero previous price has no percentage, so it never matches.
            if (
                rule.drop_percent is not None
                and old.price > 0
                and new.price < old.price
                and (old.price - new.price) * 100 >= rule.drop_percent * old.price
            ):
                events.append(Event(key, PRICE_DROP_PERCENT, str(old.price), str(new.price)))
    known = {Availability.IN_STOCK, Availability.OUT_OF_STOCK}
    if old.availability != new.availability and old.availability in known and new.availability in known:
        events.append(Event(key, AVAILABILITY_CHANGED, old.availability.value, new.availability.value))
    return events
