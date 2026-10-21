import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import httpx

from conftest import make_source
from price_monitor import demo_site, outbox, storage
from price_monitor.collector import collect_once
from price_monitor.locking import FileLock

CHAT = 111


def collect(conn, demo, scenario=None, **kwargs):
    url, state = demo
    if scenario:
        demo_site.write_scenario(state, scenario)
    return collect_once(conn, make_source(url), recipients=(CHAT,), **kwargs)


def messages(conn):
    return [r["text"] for r in conn.execute("SELECT * FROM notification_outbox ORDER BY id")]


def test_tracked_product_alert_is_one_message_listing_all_reasons(demo, conn):
    collect(conn, demo)
    storage.set_rule(conn, "demo", "00123", threshold=D("20"), drop_percent=D("15"))
    collect(conn, demo, "price_drop")
    texts = messages(conn)
    assert len(texts) == 1  # 01003 also dropped, but nobody tracks it
    assert "Desk lamp Nord" in texts[0] and "нижче заданого порогу" in texts[0] and "знизилась" in texts[0]
    assert "24.90 → 19.90" in texts[0]
    assert "ваше правило: від 15%" in texts[0] and "ваше правило: нижче 20" in texts[0]
    assert "Магазин: Demo Shop" in texts[0] and "/untrack demo 00123" in texts[0]
    assert collect(conn, demo, "price_drop").events == ()  # unchanged repeat: no new messages
    assert len(messages(conn)) == 1


def test_price_change_without_a_matching_rule_does_not_notify(demo, conn):
    collect(conn, demo)
    storage.set_rule(conn, "demo", "00123", threshold=D("10"))  # price stays above 10
    collect(conn, demo, "price_drop")
    assert messages(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2  # but history has both changes


def test_removed_and_returned_are_notified_for_tracked_products(demo, conn):
    collect(conn, demo)
    storage.set_rule(conn, "demo", "01002")
    collect(conn, demo, "product_missing")
    collect(conn, demo, "product_missing")
    collect(conn, demo, "initial")
    texts = messages(conn)
    assert len(texts) == 2 and "зник" in texts[0] and "знову є" in texts[1]


def test_events_and_queue_are_written_together(demo, conn):
    collect(conn, demo)
    storage.set_rule(conn, "demo", "00123", drop_percent=D("10"))
    run = collect(conn, demo, "price_drop")
    row = conn.execute("SELECT * FROM notification_outbox").fetchone()
    assert row["dedupe_key"] == f"{run.run_id}:00123" and row["status"] == "pending"


def test_source_outage_is_reported_once_and_recovery_once(demo, conn):
    collect(conn, demo)
    for _ in range(3):
        assert not collect(conn, demo, "changed_template").ok
    assert len(messages(conn)) == 1 and "недоступний" in messages(conn)[0]
    assert collect(conn, demo, "initial").ok
    assert len(messages(conn)) == 2 and "знову працює" in messages(conn)[1]
    assert collect(conn, demo).ok
    assert len(messages(conn)) == 2


def test_refused_access_stops_the_source_until_resumed(conn):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403)

    source = make_source("http://shop.test")
    first = collect_once(conn, source, httpx.MockTransport(handler), recipients=(CHAT,))
    assert first.fatal
    again = collect_once(conn, source, httpx.MockTransport(handler), recipients=(CHAT,))
    assert "stopped" in again.error and len(calls) == 1
    storage.resume_source(conn, "demo")
    collect_once(conn, source, httpx.MockTransport(handler), recipients=(CHAT,))
    assert len(calls) == 2


def test_broken_source_does_not_block_another_source(demo, conn):
    good = make_source(demo[0], id="good")
    broken = make_source("http://down.test", id="broken", retries=0)

    def refuse(request):
        raise httpx.ConnectError("down", request=request)

    assert not collect_once(conn, broken, httpx.MockTransport(refuse)).ok
    assert collect_once(conn, good).ok
    assert storage.last_success_run(conn, "good") and storage.last_success_run(conn, "broken") is None


# --- delivery ---------------------------------------------------------------------------------

def queued(conn, n=1):
    for i in range(n):
        outbox.enqueue(conn, [CHAT], f"k{i}", f"text {i}")


def test_successful_delivery_marks_sent_and_is_not_repeated(conn):
    queued(conn)
    sent = []
    assert outbox.deliver_pending(conn, lambda c, t, card=None: sent.append((c, t))).sent == 1
    assert outbox.deliver_pending(conn, lambda c, t, card=None: sent.append((c, t))).sent == 0
    assert sent == [(CHAT, "text 0")]


def test_temporary_failure_backs_off_then_gives_up(conn):
    queued(conn)

    def fail(chat, text, card=None):
        raise outbox.TemporaryDeliveryError("timeout")

    now = datetime.now(timezone.utc)
    assert outbox.deliver_pending(conn, fail, now).retry_later == 1
    assert outbox.deliver_pending(conn, fail, now).retry_later == 0  # not due yet: no hammering
    for attempt in range(1, outbox.MAX_ATTEMPTS):
        now += timedelta(hours=2)
        report = outbox.deliver_pending(conn, fail, now)
    assert report.failed == 1
    row = conn.execute("SELECT * FROM notification_outbox").fetchone()
    assert row["status"] == "failed" and row["attempts"] == outbox.MAX_ATTEMPTS
    assert outbox.counts(conn)["failed"] == 1 and "gave up" in outbox.last_failure(conn)


def test_rate_limit_waits_as_told_and_does_not_use_up_attempts(conn):
    queued(conn, 3)
    calls = []

    def limited(chat, text, card=None):
        calls.append(text)
        raise outbox.RateLimitedError("Too Many Requests", retry_after=40)

    now = datetime.now(timezone.utc)
    report = outbox.deliver_pending(conn, limited, now)
    assert report.retry_later == 1 and calls == ["text 0"]  # the batch stops at the first 429
    row = conn.execute("SELECT * FROM notification_outbox ORDER BY id").fetchone()
    assert row["attempts"] == 0 and "retry after 40 s" in row["last_error"]
    assert row["next_attempt_at"] == (now + timedelta(seconds=40)).isoformat(timespec="seconds")

    for _ in range(outbox.MAX_ATTEMPTS + 2):  # rate limits never make it give up
        now += timedelta(seconds=41)
        outbox.deliver_pending(conn, limited, now)
    assert outbox.counts(conn)["failed"] == 0

    sent = []
    now += timedelta(seconds=41)
    assert outbox.deliver_pending(conn, lambda c, t, card=None: sent.append(t), now).sent == 3


def test_permanent_failure_is_not_retried(conn):
    queued(conn)
    calls = []

    def blocked(chat, text, card=None):
        calls.append(1)
        raise outbox.PermanentDeliveryError("bot was blocked by the user")

    assert outbox.deliver_pending(conn, blocked).failed == 1
    outbox.deliver_pending(conn, blocked, datetime.now(timezone.utc) + timedelta(days=1))
    assert len(calls) == 1


def test_queue_survives_restart(tmp_path):
    path = tmp_path / "q.db"
    first = storage.connect(path)
    queued(first, 2)
    first.close()  # "restart"
    second = storage.connect(path)
    sent = []
    assert outbox.deliver_pending(second, lambda c, t, card=None: sent.append(t)).sent == 2
    assert sent == ["text 0", "text 1"]


def test_lost_acknowledgement_causes_a_duplicate_but_never_loses_the_message(conn):
    queued(conn)
    delivered = []
    state = {"first": True}

    def flaky(chat, text, card=None):
        delivered.append(text)  # reaches the user...
        if state.pop("first", False):
            raise outbox.TemporaryDeliveryError("timeout waiting for the answer")  # ...but we never learn it

    now = datetime.now(timezone.utc)
    outbox.deliver_pending(conn, flaky, now)
    outbox.deliver_pending(conn, flaky, now + timedelta(hours=1))
    assert delivered == ["text 0", "text 0"]  # documented possible duplicate
    assert conn.execute("SELECT status FROM notification_outbox").fetchone()[0] == "sent"


def test_enqueue_with_same_key_is_ignored(conn):
    queued(conn)
    queued(conn)
    assert conn.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 1


# --- locks ------------------------------------------------------------------------------------

def test_file_lock_keeps_a_second_process_out(demo, conn, tmp_path):
    code = (
        "import sys, time\nfrom pathlib import Path\nfrom price_monitor.locking import FileLock\n"
        "l = FileLock(Path(sys.argv[1]))\nassert l.try_acquire()\nprint('locked', flush=True)\ntime.sleep(30)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path / "collect-demo.lock")], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        result = collect_once(conn, make_source(demo[0]), lock_dir=tmp_path)
        assert not result.ok and "already running" in result.error
        assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0
    finally:
        holder.kill()
        holder.wait()
    assert collect_once(conn, make_source(demo[0]), lock_dir=tmp_path).ok  # lock vanished with the process


def test_file_lock_is_exclusive_and_reusable(tmp_path):
    a, b = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    assert a.try_acquire() and not b.try_acquire()
    a.release()
    assert b.try_acquire()
    b.release()
