from decimal import Decimal as D

import pytest

from price_monitor.comparison import (
    AVAILABILITY_CHANGED, BELOW_THRESHOLD, PRICE_CHANGED, PRICE_DROP_PERCENT, REMOVED, RETURNED,
    ProductState, Rule, compare,
)
from price_monitor.models import Availability as A
from price_monitor.models import ProductObservation


def obs(price, availability=A.IN_STOCK, key="p1"):
    return ProductObservation(key, "Name", f"http://x.test/{key}", D(price), "USD", availability)


def run(old, new, rule=None):
    events, _ = compare({"p1": old}, {"p1": new}, {"p1"}, {}, {"p1": rule} if rule else {})
    return [e.type for e in events]


def test_price_change_is_always_recorded_even_without_a_rule():
    assert run(obs("10"), obs("9")) == [PRICE_CHANGED]
    assert run(obs("10"), obs("10.00")) == []  # same value, different spelling


@pytest.mark.parametrize(
    "old, new, expected",
    [
        ("11", "9.99", True),  # crosses from above
        ("10", "9.99", True),  # was exactly at the threshold, now strictly below
        ("10", "10", False),
        ("9.99", "9", False),  # already below: repeat is not a new crossing
        ("11", "10", False),  # lands exactly on the threshold: not below it
    ],
)
def test_threshold_rule(old, new, expected):
    types = run(obs(old), obs(new), Rule(threshold=D("10")))
    assert (BELOW_THRESHOLD in types) is expected


@pytest.mark.parametrize(
    "old, new, expected",
    [
        ("100", "90", True),  # exactly 10%
        ("100", "90.01", False),  # just under 10%
        ("100", "80", True),
        ("100", "110", False),
    ],
)
def test_percent_rule_uses_exact_decimal_arithmetic(old, new, expected):
    types = run(obs(old), obs(new), Rule(drop_percent=D("10")))
    assert (PRICE_DROP_PERCENT in types) is expected


def test_zero_previous_price_has_no_percentage():
    assert run(obs("0"), obs("5"), Rule(drop_percent=D("10"))) == [PRICE_CHANGED]


def test_one_change_can_match_several_rules():
    types = run(obs("12"), obs("8"), Rule(threshold=D("10"), drop_percent=D("20")))
    assert types == [PRICE_CHANGED, BELOW_THRESHOLD, PRICE_DROP_PERCENT]


def test_confirmed_stock_changes_are_events_unknown_is_not():
    assert run(obs("5"), obs("5", A.OUT_OF_STOCK)) == [AVAILABILITY_CHANGED]
    assert run(obs("5", A.OUT_OF_STOCK), obs("5")) == [AVAILABILITY_CHANGED]
    assert run(obs("5"), obs("5", A.UNKNOWN)) == []
    assert run(obs("5", A.UNKNOWN), obs("5", A.OUT_OF_STOCK)) == []


def test_removed_needs_two_consecutive_absences_and_return_is_its_own_event():
    prev = {"p1": obs("5")}
    events, states = compare(prev, {}, {"p1"}, {}, {})
    assert events == [] and states["p1"] == ProductState(1, False)

    events, states = compare({}, {}, {"p1"}, states, {})
    assert [e.type for e in events] == [REMOVED] and states["p1"].removed

    events, states2 = compare({}, {}, {"p1"}, states, {})  # stays removed, no repeat event
    assert events == [] and states2["p1"].removed

    events, states3 = compare({}, {"p1": obs("5")}, {"p1"}, states, {})
    assert [e.type for e in events] == [RETURNED]
    assert states3["p1"] == ProductState()


def test_reappearing_after_one_absence_clears_the_counter_silently():
    events, states = compare({}, {"p1": obs("5")}, {"p1"}, {"p1": ProductState(1, False)}, {})
    assert events == [] and states["p1"] == ProductState()
