import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from price_monitor import scheduler
from price_monitor.locking import FileLock


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICE_MONITOR_DB", str(tmp_path / "pm.db"))
    monkeypatch.setenv("PRICE_MONITOR_INTERVAL_SECONDS", "1")
    for name in ("BOOKS", "ALLO", "XCOM"):
        monkeypatch.delenv(f"PRICE_MONITOR_ENABLE_{name}", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    return tmp_path


def fake_collections(monkeypatch, duration: float):
    """Replace the real collection with one that only records when it ran and how many ran at once."""
    state = {"starts": [], "running": 0, "peak": 0}
    guard = threading.Lock()

    def job(source_id):
        with guard:
            state["starts"].append(time.monotonic())
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(duration)
        with guard:
            state["running"] -= 1

    monkeypatch.setattr(scheduler, "_collect_job", job)
    return state


def run_for(sched, seconds: float, block_loop_for: float = 0.0) -> float:
    async def main():
        sched.start()
        started = time.monotonic()
        if block_loop_for:
            time.sleep(block_loop_for)  # the process is "asleep": no job can be started
        await asyncio.sleep(seconds)
        sched.shutdown(wait=True)
        return started

    return asyncio.run(main())


def test_one_job_per_source_with_its_interval_and_an_immediate_first_run(env, monkeypatch):
    monkeypatch.setenv("PRICE_MONITOR_ENABLE_BOOKS", "1")
    before = datetime.now(timezone.utc)

    async def started_jobs(deliver):
        # Job defaults are applied when the scheduler starts; paused, nothing runs.
        sched = scheduler.build_scheduler(deliver)
        sched.start(paused=True)
        jobs = {job.id: job for job in sched.get_jobs()}
        sched.shutdown(wait=False)
        return jobs

    jobs = asyncio.run(started_jobs(None))
    assert set(jobs) == {"collect-demo", "collect-books"}  # no delivery job without Telegram
    assert jobs["collect-demo"].trigger.interval == timedelta(seconds=1)
    assert jobs["collect-books"].trigger.interval == timedelta(hours=1)  # the public site keeps its own minimum
    for job in jobs.values():
        assert job.coalesce is True and job.max_instances == 1
        assert before <= job.next_run_time <= datetime.now(timezone.utc) + timedelta(seconds=1)

    async def deliver():
        pass

    jobs = asyncio.run(started_jobs(deliver))
    assert jobs["deliver"].trigger.interval == timedelta(seconds=scheduler.DELIVERY_INTERVAL_SECONDS)


def test_first_collection_starts_right_away_and_runs_never_overlap(env, monkeypatch):
    state = fake_collections(monkeypatch, duration=1.6)  # longer than the 1-second interval
    started = run_for(scheduler.build_scheduler(deliver=None), 4.0)
    assert state["starts"] and state["starts"][0] - started < 0.5
    assert state["peak"] == 1  # max_instances=1: a slow collection is never joined by the next one
    assert 2 <= len(state["starts"]) <= 3


def test_missed_runs_are_not_replayed_after_a_pause(env, monkeypatch):
    state = fake_collections(monkeypatch, duration=0.0)
    # The loop is blocked for 4 s right after the start, so about four runs are missed.
    started = run_for(scheduler.build_scheduler(deliver=None), 0.6, block_loop_for=4.0)
    late = [t for t in state["starts"] if t - started >= 4.0]
    assert len(late) == 1  # coalesce: one run instead of a burst of the missed ones


def test_a_second_service_instance_is_refused(env):
    lock = FileLock(env / "service.lock")
    assert lock.try_acquire()
    try:
        assert asyncio.run(scheduler.run_service()) == 1
    finally:
        lock.release()
    assert not (env / "pm.db").exists()  # the refused instance did not touch the database
