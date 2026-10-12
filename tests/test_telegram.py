import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage

from conftest import make_source
from price_monitor import outbox, storage
from price_monitor.collector import collect_once
from price_monitor.telegram import classify_send_error, handle_command

OWNER, STRANGER = 1001, 2002


@pytest.fixture
def bot(demo, conn, tmp_path):
    collect_once(conn, make_source(demo[0]))

    def ask(user, text):
        return handle_command(
            conn, user, text, sources={"demo": make_source(demo[0])}, allowed_ids=(OWNER,), reports_dir=tmp_path / "r"
        )

    return ask


def rules(conn):
    return [(r["product_key"], r["threshold"], r["drop_percent"]) for r in conn.execute("SELECT * FROM tracking_rules")]


def test_stranger_cannot_change_rules_or_get_reports(bot, conn, tmp_path):
    for text in ("/track demo 00123", "/untrack demo 00123", "/report demo 7", "/products demo", "/sources", "/status"):
        reply = bot(STRANGER, text)
        assert reply.text == "Доступ заборонено." and reply.file is None
    assert rules(conn) == [] and not (tmp_path / "r").exists()


def test_repeated_track_command_does_not_duplicate_the_rule(bot, conn):
    for _ in range(3):
        assert "Відстежується" in bot(OWNER, "/track demo 00123 below=20 drop=10").text
    assert rules(conn) == [("00123", "20", "10")]
    assert "00123" in bot(OWNER, "/tracked").text
    bot(OWNER, "/untrack demo 00123")
    assert rules(conn) == []


def test_keys_keep_leading_zeros_and_unknown_input_is_rejected(bot, conn):
    bot(OWNER, "/track demo 123")
    bot(OWNER, "/track demo 00123")
    assert sorted(r[0] for r in rules(conn)) == ["00123", "123"]
    for text in ("/track demo nope", "/track other 00123", "/track demo 00123 drop=0", "/track demo 00123 below=abc",
                 "/track demo 00123 color=red", "/track demo 00123 below=NaN"):
        assert "Відстежується" not in bot(OWNER, text).text
    assert len(rules(conn)) == 2


def test_lists_show_last_success_time_and_state(bot):
    assert "працює" in bot(OWNER, "/sources").text
    products = bot(OWNER, "/products demo").text
    assert "00123 — Desk lamp Nord — 24.90 USD — IN_STOCK" in products and "останній успішний збір" in products


def test_report_command_returns_a_file_and_validates_days(bot):
    reply = bot(OWNER, "/report 3")
    assert reply.file and reply.file.suffix == ".xlsx" and reply.file.exists()
    assert bot(OWNER, "/report demo 0").file is None
    assert bot(OWNER, "/report demo 999").file is None
    assert bot(OWNER, "/report demo x").file is None


def test_status_shows_queue_and_delivery_errors(bot, conn):
    outbox.enqueue(conn, [5], "a", "hello")
    outbox.deliver_pending(conn, lambda c, t, card=None: (_ for _ in ()).throw(outbox.PermanentDeliveryError("chat not found")))
    text = bot(OWNER, "/status").text
    assert "помилок доставки 1" in text and "chat not found" in text


def test_send_errors_are_classified_for_the_outbox():
    method = SendMessage(chat_id=1, text="x")
    assert isinstance(classify_send_error(TelegramForbiddenError(method, "Forbidden: bot was blocked")), outbox.PermanentDeliveryError)
    assert isinstance(classify_send_error(TelegramBadRequest(method, "Bad Request: chat not found")), outbox.PermanentDeliveryError)
    assert isinstance(classify_send_error(TelegramBadRequest(method, "Bad Request: message is too long")), outbox.TemporaryDeliveryError)
    assert isinstance(classify_send_error(TelegramNetworkError(method, "timeout")), outbox.TemporaryDeliveryError)
    assert isinstance(classify_send_error(TimeoutError()), outbox.TemporaryDeliveryError)


def test_start_explains_the_project_to_anyone_but_only_the_owner_can_manage(bot):
    stranger = bot(STRANGER, "/start").text
    assert "Demo Shop" in stranger and "Навіщо" in stranger and "доступне лише власнику" in stranger
    assert "/track" in stranger and "Доступ заборонено" not in stranger  # explains, does not slam the door
    owner = bot(OWNER, "/start").text
    assert "Навіщо" in owner and "/report [магазин] [днів]" in owner
