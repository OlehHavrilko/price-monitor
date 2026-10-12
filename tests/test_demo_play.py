from conftest import make_source
from price_monitor import demo_play


def test_story_produces_the_expected_alerts_and_a_report(demo, conn, tmp_path):
    url, state = demo
    lines = []
    report = demo_play.play(
        conn, make_source(url), state, tmp_path / "r", recipients=(9,), pause=0, say=lines.append
    )
    assert report.exists() and report.suffix == ".xlsx"
    texts = [r["text"] for r in conn.execute("SELECT text FROM notification_outbox ORDER BY id")]
    joined = "\n".join(texts)
    assert len(texts) == 8 + 9  # 9 explanation messages (intro, 7 steps, end); the hub sells out and, after the reset to the base catalog, is back
    for expected in ("Desk lamp Nord", "Wireless mouse", "USB hub 4 ports", "зник з каталогу", "знову є в каталозі", "недоступний", "знову працює"):
        assert expected in joined, expected
    assert any("REMOVED" not in line and "failed" in line for line in lines)  # the broken source step is shown
