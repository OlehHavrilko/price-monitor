import sqlite3

import pytest

from price_monitor import storage
from price_monitor.web.auth import matches, required_token
from price_monitor.web.db import DatabaseUnavailable, read_database


def test_web_token_required(monkeypatch):
    monkeypatch.delenv("PRICE_MONITOR_WEB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="PRICE_MONITOR_WEB_TOKEN"):
        required_token()
    assert matches("test-token", "test-token")
    assert not matches("інший", "test-token")


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(DatabaseUnavailable):
        with read_database(path):
            pass
    assert not path.exists()


def test_web_connection_cannot_write_even_without_query_only(tmp_path):
    path = tmp_path / "db.sqlite"
    writer = storage.connect(path)
    storage.ensure_source(writer, "demo", "http://demo")
    with read_database(path) as reader:
        assert reader.execute("SELECT id FROM sources").fetchone()[0] == "demo"
        reader.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("DELETE FROM sources")
    assert writer.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    writer.close()


def test_unsupported_schema_is_not_migrated(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=1")
    before = path.read_bytes()
    with pytest.raises(DatabaseUnavailable, match="Версія"):
        with read_database(path):
            pass
    assert path.read_bytes() == before
