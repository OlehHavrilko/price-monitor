"""No network: snapshots come from the real demo renderer through MockTransport."""
import sqlite3
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from price_monitor import reports, storage, outbox
from price_monitor.web import queries
from price_monitor.web.app import create_app
from test_web_pages import web_data, TOKEN


@pytest.mark.parametrize("url", ["/", "/sources/demo", "/products/demo/00123", "/api/history/demo/00123", "/events", "/report", "/report/download?source=demo&since=2000-01-01&until=2100-01-01", "/status"])
def test_all_data_endpoints_require_token(web_data, url):
    client = web_data[0]
    client.headers.pop("Authorization")
    assert client.get(url).status_code == 401


def test_requests_do_not_change_any_database_content(web_data):
    client, writer, _, path = web_data
    before = list(writer.iterdump())
    version = writer.execute("PRAGMA user_version").fetchone()[0]
    for url in ["/", "/sources/demo?q=00123", "/products/demo/00123", "/api/history/demo/00123", "/events", "/status", "/report", "/report/download?source=demo&since=2000-01-01&until=2100-01-01"]:
        assert client.get(url).status_code == 200
    assert list(writer.iterdump()) == before
    assert writer.execute("PRAGMA user_version").fetchone()[0] == version
    assert list(path.parent.glob("*.xlsx")) == []
    assert client.post("/sources/demo", data={"track": "00123"}).status_code == 405


def test_no_web_token_prevents_app_start(monkeypatch):
    monkeypatch.delenv("PRICE_MONITOR_WEB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="PRICE_MONITOR_WEB_TOKEN"):
        create_app()


def test_missing_and_empty_database(tmp_path):
    path = tmp_path / "empty.db"
    with TestClient(create_app(db_path=path, token=TOKEN, sources={}), headers={"Authorization":f"Bearer {TOKEN}"}) as client:
        assert client.get("/").status_code == 503
        assert not path.exists()
        storage.connect(path).close()
        assert "Джерел ще немає" in client.get("/").text
        assert "Подій ще немає" in client.get("/events").text
        assert client.get("/status").status_code == 200


def test_escaping_search_and_unsafe_links(web_data):
    client, writer, _, _ = web_data
    evil = '<img src=x onerror=alert(1)> Українська назва'
    writer.execute("UPDATE observations SET name=?, url=? WHERE product_key=?", (evil, 'javascript:alert(1)', '00123'))
    writer.execute("UPDATE products SET name=?, url=? WHERE product_key=?", (evil, 'javascript:alert(1)', '00123'))
    for url in ["/sources/demo", "/products/demo/00123", "/events"]:
        text = client.get(url).text
        assert '<img src=x' not in text
        assert '&lt;img' in text
        assert 'href="javascript:' not in text
    text = client.get("/sources/demo", params={"q":"українська"}).text
    assert "00123" in text and "spare bulb" not in text
    text = client.get("/sources/demo", params={"q":"' OR 1=1 --"}).text
    assert "Товарів за цими умовами не знайдено" in text
    assert "Товарів за цими умовами не знайдено" in client.get("/sources/demo?q=%25").text


def test_pagination_and_status(web_data, monkeypatch):
    client, writer, _, _ = web_data
    monkeypatch.setattr(queries, "PAGE_SIZE", 2)
    first = client.get("/sources/demo").text
    second = client.get("/sources/demo?page=2").text
    assert "Сторінка 1 / 6" in first and "Сторінка 2 / 6" in second
    assert "Desk lamp Nord" in first and "Desk lamp Nord" not in second
    outbox.enqueue(writer, [7], "test-failure", "message")
    writer.execute("UPDATE notification_outbox SET status='failed', last_error=?", ("<delivery failed>",))
    assert "&lt;delivery failed&gt;" in client.get("/status").text
    writer.execute("UPDATE source_health SET stopped=1, last_error='HTTP 403' WHERE source_id='demo'")
    assert "Зупинено" in client.get("/").text
    assert "Desk lamp Nord" in client.get("/sources/demo").text


def test_stock_event_mapping_and_removed_current_semantics(web_data):
    client, _, collect, _ = web_data
    collect("out_of_stock")
    assert "01001" in client.get("/events?kind=OUT_OF_STOCK&source=demo").text
    assert "00789" in client.get("/events?kind=BACK_IN_STOCK").text
    assert "01001" not in client.get("/events?kind=BACK_IN_STOCK").text
    collect("product_missing")
    assert "видалення ще не підтверджене" in client.get("/products/demo/01002").text
    collect("product_missing")
    assert "підтверджено видалено" in client.get("/products/demo/01002").text
    assert "01002" not in client.get("/sources/demo").text
    collect("initial")
    assert "01002" in client.get("/events?kind=RETURNED").text


def test_null_and_exact_decimal_prices(web_data):
    client, writer, _, _ = web_data
    writer.execute("UPDATE observations SET price=NULL WHERE product_key='00123' AND run_id=1")
    writer.execute("UPDATE observations SET price='1234567890.123456789' WHERE product_key='00123' AND run_id=2")
    history = client.get("/api/history/demo/00123").json()["observations"]
    assert [p["price"] for p in history] == [None, "1234567890.123456789"]
    assert "1234567890.123456789" in client.get("/products/demo/00123").text


def test_report_limit_and_safe_source(web_data, monkeypatch):
    client = web_data[0]
    monkeypatch.setattr(reports, "MAX_REPORT_ROWS", 1)
    params = {"source":"demo", "since":"2000-01-01", "until":"2100-01-01"}
    assert client.get("/report/download", params=params).status_code == 400
    params["source"] = "../../escape"
    assert client.get("/report/download", params=params).status_code == 400


def test_login_origin_and_secure_cookie(web_data):
    client = web_data[0]
    assert client.post("/login", data={"token":TOKEN}, headers={"Origin":"https://evil.test"}).status_code == 403
    with TestClient(client.app, base_url="https://testserver") as secure:
        response = secure.post("/login", data={"token":TOKEN}, follow_redirects=False)
        assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize("url", ["/sources/demo?page=0", "/events?page=bad", "/sources/demo?availability=bad", "/events?kind=bad"])
def test_invalid_filters(web_data, url):
    assert web_data[0].get(url).status_code in (400, 422)


def test_wal_reader_sees_new_commits_without_mutating_database(web_data):
    from price_monitor.web.db import read_database
    client, writer, collect, path = web_data
    assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with read_database(path) as reader:
        before = reader.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        collect("out_of_stock")
        # The current request keeps its snapshot, the next request sees the commit.
        assert reader.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == before
    assert len(client.get("/api/history/demo/00123").json()["observations"]) == 3
