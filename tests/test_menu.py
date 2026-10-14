import pytest

from conftest import make_source
from price_monitor import menu
from price_monitor.collector import collect_once

OWNER, STRANGER = 1001, 2002


@pytest.fixture
def ui(demo, conn, tmp_path):
    collect_once(conn, make_source(demo[0]))
    sources = {"demo": make_source(demo[0])}

    def press(data, user=OWNER):
        return menu.handle_callback(conn, user, data, sources=sources, allowed_ids=(OWNER,), reports_dir=tmp_path / "r")

    def find(query, user=OWNER):
        return menu.search(conn, user, query, sources, (OWNER,))

    return press, find, conn


def labels(view):
    return [b.label for row in view.rows for b in row]


def datas(view):
    return [b.data for row in view.rows for b in row if b.data]


def test_stranger_gets_nothing_from_any_button_or_search(ui, tmp_path):
    press, find, conn = ui
    for data in ("s", "s:demo", "p:demo:0", "i:demo:00123", "t:demo:00123:d", "u:demo:00123", "w", "r:demo:7", "st"):
        view = press(data, STRANGER)
        assert view.text == menu.DENIED and not view.rows and view.file is None
    assert find("lamp", STRANGER).text == menu.DENIED
    assert conn.execute("SELECT COUNT(*) FROM tracking_rules").fetchone()[0] == 0
    assert not (tmp_path / "r").exists()


def test_browsing_shops_and_paged_product_list(ui):
    press, _, _ = ui
    assert "Demo Shop" in press("s").text and "s:demo" in datas(press("s"))
    shop = press("s:demo")
    assert {"p:demo:0", "r:demo:1", "r:demo:7"} <= set(datas(shop))
    first, second = press("p:demo:0"), press("p:demo:1")
    assert "сторінка 1 з 2" in first.text and "p:demo:1" in datas(first) and "p:demo:0" not in datas(first)
    assert "p:demo:0" in datas(second) and "p:demo:1" not in datas(second)
    items = [d for d in datas(first) + datas(second) if d.startswith("i:")]
    assert len(items) == 12 and "i:demo:00123" in items and "i:demo:123" in items  # leading zeros kept
    assert all(len(d.encode()) <= 64 for d in items)
    assert "сторінка 2 з 2" in press("p:demo:99").text  # out of range pages are clamped


def test_track_from_the_product_card_is_idempotent_and_reversible(ui):
    press, _, conn = ui
    detail = press("i:demo:00123")
    assert "Desk lamp Nord" in detail.text and "24.90 USD" in detail.text and "Відстежується: ні" in detail.text
    assert any(b.url for row in detail.rows for b in row)  # link to the shop

    for _ in range(2):
        tracked = press("t:demo:00123:d")
    assert "Готово" in tracked.text and "зниження ціни від 10" in tracked.text
    assert [tuple(r) for r in conn.execute("SELECT product_key, drop_percent FROM tracking_rules")] == [("00123", "10")]
    assert "u:demo:00123" in datas(tracked) and "t:demo:00123:d" not in datas(tracked)

    assert "00123" in " ".join(datas(press("w")))
    assert "Відстеження вимкнено" in press("u:demo:00123").text
    assert conn.execute("SELECT COUNT(*) FROM tracking_rules").fetchone()[0] == 0
    assert "Поки нічого не відстежується" in press("w").text


def test_availability_only_preset_and_unknown_product(ui):
    press, _, conn = ui
    press("t:demo:00789:a")
    assert [tuple(r) for r in conn.execute("SELECT product_key, drop_percent, threshold FROM tracking_rules")] == [("00789", None, None)]
    assert "не знайдено" in press("i:demo:nope").text


def test_report_button_returns_a_file_and_status_shows_the_queue(ui):
    press, _, _ = ui
    view = press("r:demo:7")
    assert view.file and view.file.exists() and view.file.suffix == ".xlsx"
    assert "Черга сповіщень" in press("st").text


def test_search_by_name_or_code(ui):
    _, find, _ = ui
    lamps = find("lamp")
    assert {"i:demo:00123", "i:demo:123"} <= set(datas(lamps))
    assert "i:demo:01003" in datas(find("01003"))  # exact code
    assert "нічого не знайдено" in find("zzzz").text
    assert "хоча б 2 символи" in find("a").text


def test_unknown_callback_falls_back_to_the_main_menu(ui):
    press, _, _ = ui
    assert labels(press("garbage")) == labels(press("m")) == [b.label for row in menu.main_buttons() for b in row]
