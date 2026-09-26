import httpx
import pytest

from conftest import make_source
from price_monitor import collector, demo_site, storage
from price_monitor.collector import collect_once


def observations(conn, run_id):
    return {r["product_key"]: r for r in conn.execute("SELECT * FROM observations WHERE run_id = ?", (run_id,))}


def transport(handler, calls):
    def wrapped(request):
        calls.append(request.url.path)
        return handler(request)

    return httpx.MockTransport(wrapped)


def test_first_collection_stores_full_snapshot_over_real_pagination(demo, conn):
    url, _ = demo
    result = collect_once(conn, make_source(url))
    assert result.ok and result.product_count == 12
    obs = observations(conn, result.run_id)
    assert "00123" in obs and "123" in obs  # leading zeros are preserved, keys are different
    assert obs["00789"]["availability"] == "OUT_OF_STOCK"
    assert obs["01006"]["availability"] == "UNKNOWN"
    assert obs["00123"]["price"] == "24.90"


def test_repeat_collection_adds_a_new_run_with_same_products(demo, conn):
    url, _ = demo
    first = collect_once(conn, make_source(url))
    second = collect_once(conn, make_source(url))
    assert second.ok and second.run_id != first.run_id
    assert storage.last_success_run(conn, "demo")["id"] == second.run_id
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 12


@pytest.mark.parametrize(
    "scenario", ["page_lost", "pagination_cycle", "changed_template", "duplicate_key", "count_mismatch"]
)
def test_broken_catalog_fails_the_run_and_keeps_last_good_snapshot(demo, conn, scenario):
    url, state = demo
    good = collect_once(conn, make_source(url))
    demo_site.write_scenario(state, scenario)
    bad = collect_once(conn, make_source(url))
    assert not bad.ok and bad.error
    assert storage.last_success_run(conn, "demo")["id"] == good.run_id
    status = conn.execute("SELECT status FROM collection_runs WHERE id = ?", (bad.run_id,)).fetchone()[0]
    assert status == "failed"
    assert observations(conn, bad.run_id) == {}


def test_page_limit_is_an_error_not_a_truncated_success(demo, conn):
    url, _ = demo
    result = collect_once(conn, make_source(url, max_pages=2))
    assert not result.ok and "Page limit" in result.error
    assert storage.last_success_run(conn, "demo") is None


def test_403_stops_without_retry(conn):
    calls = []
    result = collect_once(conn, make_source("http://shop.test", retries=3), transport(lambda r: httpx.Response(403), calls))
    assert not result.ok and result.fatal
    assert len(calls) == 1


def test_404_is_permanent_and_not_retried(conn):
    calls = []
    result = collect_once(conn, make_source("http://shop.test", retries=3), transport(lambda r: httpx.Response(404), calls))
    assert not result.ok and not result.fatal
    assert len(calls) == 1


def test_5xx_is_retried_a_limited_number_of_times(conn):
    calls = []
    result = collect_once(conn, make_source("http://shop.test", retries=2), transport(lambda r: httpx.Response(503), calls))
    assert not result.ok and "3 attempts" in result.error
    assert len(calls) == 3


def test_timeout_then_success_recovers(conn):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, text=demo_site.render_page(page, "initial"))

    result = collect_once(conn, make_source("http://shop.test", retries=1), httpx.MockTransport(handler))
    assert result.ok and result.product_count == 12


def test_redirect_is_refused(conn):
    t = transport(lambda r: httpx.Response(302, headers={"Location": "http://evil.test/"}), [])
    result = collect_once(conn, make_source("http://shop.test"), t)
    assert not result.ok and "Redirect" in result.error


def test_next_link_outside_the_source_is_refused(conn):
    page = demo_site.render_page(1, "initial").replace('href="/catalog?page=2"', 'href="http://evil.test/catalog"')
    calls = []
    result = collect_once(conn, make_source("http://shop.test"), transport(lambda r: httpx.Response(200, text=page), calls))
    assert not result.ok and "outside the allowed source" in result.error
    assert len(calls) == 1


def test_second_run_of_same_source_is_rejected_while_one_is_active(demo, conn):
    url, _ = demo
    lock = collector._lock_for("demo")
    lock.acquire()
    try:
        result = collect_once(conn, make_source(url))
    finally:
        lock.release()
    assert not result.ok and "already running" in result.error
    assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0


def test_unfinished_run_from_a_crash_is_never_a_baseline(demo, conn):
    url, _ = demo
    storage.ensure_source(conn, "demo", url)
    stale = storage.start_run(conn, "demo")  # simulates a process that died mid-collection
    result = collect_once(conn, make_source(url))
    status = conn.execute("SELECT status FROM collection_runs WHERE id = ?", (stale,)).fetchone()[0]
    assert status == "interrupted"
    assert storage.last_success_run(conn, "demo")["id"] == result.run_id


def test_schema_is_versioned_and_reopening_is_safe(tmp_path):
    path = tmp_path / "v.db"
    storage.connect(path).close()
    again = storage.connect(path)
    assert again.execute("PRAGMA user_version").fetchone()[0] == len(storage.MIGRATIONS)
