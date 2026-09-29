from decimal import Decimal as D

from conftest import make_source
from price_monitor import demo_site, storage
from price_monitor.collector import collect_once


def collect(conn, demo, scenario=None):
    url, state = demo
    if scenario:
        demo_site.write_scenario(state, scenario)
    return collect_once(conn, make_source(url))


def events(conn):
    return [(r["product_key"], r["type"], r["old_value"], r["new_value"]) for r in conn.execute("SELECT * FROM events ORDER BY id")]


def test_first_run_is_a_baseline_and_unchanged_repeat_creates_no_events(demo, conn):
    assert collect(conn, demo).events == ()
    assert collect(conn, demo).events == ()
    assert events(conn) == []


def test_price_drop_and_stock_changes_on_the_demo_catalog(demo, conn):
    storage_first = collect(conn, demo)
    assert storage_first.ok
    storage.ensure_source(conn, "demo", demo[0])
    storage.set_rule(conn, "demo", "00123", threshold=D("20"), drop_percent=D("15"))
    collect(conn, demo, "price_drop")
    got = events(conn)
    assert ("00123", "PRICE_CHANGED", "24.90", "19.90") in got
    assert ("00123", "BELOW_THRESHOLD", "24.90", "19.90") in got
    assert ("00123", "PRICE_DROP_PERCENT", "24.90", "19.90") in got  # 20.08 % >= 15 %
    assert ("01003", "PRICE_CHANGED", "12.50", "11.00") in got  # untracked: history only
    assert not [e for e in got if e[0] == "01003" and e[1] != "PRICE_CHANGED"]

    collect(conn, demo, "out_of_stock")
    got = events(conn)
    assert ("01001", "AVAILABILITY_CHANGED", "IN_STOCK", "OUT_OF_STOCK") in got
    assert ("00789", "AVAILABILITY_CHANGED", "OUT_OF_STOCK", "IN_STOCK") in got


def test_explicit_out_of_stock_is_not_removal(demo, conn):
    collect(conn, demo)
    collect(conn, demo, "out_of_stock")
    collect(conn, demo, "out_of_stock")
    assert not [e for e in events(conn) if e[1] == "REMOVED"]


def test_removed_after_two_full_runs_without_the_product_then_returned(demo, conn):
    collect(conn, demo)
    collect(conn, demo, "product_missing")
    assert not [e for e in events(conn) if e[1] == "REMOVED"]  # one absence is only a candidate
    collect(conn, demo, "product_missing")
    assert ("01002", "REMOVED", None, None) in events(conn)
    collect(conn, demo, "product_missing")
    assert [e for e in events(conn) if e[1] == "REMOVED"].__len__() == 1  # no repeat
    collect(conn, demo, "initial")
    assert ("01002", "RETURNED", None, "7.80") in events(conn)


def test_failed_run_between_absences_restarts_the_series(demo, conn):
    collect(conn, demo)
    collect(conn, demo, "product_missing")
    assert not collect(conn, demo, "page_lost").ok
    collect(conn, demo, "product_missing")
    assert not [e for e in events(conn) if e[1] == "REMOVED"]  # first absence after the break
    collect(conn, demo, "product_missing")
    assert ("01002", "REMOVED", None, None) in events(conn)


def test_failed_run_creates_no_events_and_does_not_replace_the_baseline(demo, conn):
    collect(conn, demo)
    for scenario in ("changed_template", "pagination_cycle", "count_mismatch"):
        assert not collect(conn, demo, scenario).ok
    assert events(conn) == []
    # the baseline is still the first run, so the price drop is compared with it
    collect(conn, demo, "price_drop")
    assert ("00123", "PRICE_CHANGED", "24.90", "19.90") in events(conn)


def test_events_of_one_run_are_saved_together_with_the_run(demo, conn):
    collect(conn, demo)
    result = collect(conn, demo, "price_drop")
    run_ids = {r[0] for r in conn.execute("SELECT run_id FROM events")}
    assert run_ids == {result.run_id}
    assert len(result.events) == conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def test_rule_is_idempotent_and_validated(demo, conn):
    collect(conn, demo)
    for _ in range(2):
        storage.set_rule(conn, "demo", "00123", drop_percent=D("10"))
    assert conn.execute("SELECT COUNT(*) FROM tracking_rules").fetchone()[0] == 1
    for bad in ({"drop_percent": D("0")}, {"threshold": D("-1")}):
        try:
            storage.set_rule(conn, "demo", "00123", **bad)
        except ValueError:
            continue
        raise AssertionError("invalid rule accepted")
    try:
        storage.set_rule(conn, "demo", "999", threshold=D("1"))
    except ValueError:
        pass
    else:
        raise AssertionError("unknown product accepted")
