from price_monitor.cli import main


def test_web_cli_missing_token(monkeypatch, capsys):
    monkeypatch.delenv("PRICE_MONITOR_WEB_TOKEN", raising=False)
    assert main(["web"]) == 2
    assert "PRICE_MONITOR_WEB_TOKEN" in capsys.readouterr().err


def test_web_cli_host_port(monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setenv("PRICE_MONITOR_WEB_TOKEN", "test-token")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert main(["web", "--host", "127.0.0.1", "--port", "8123"]) == 0
    assert calls == [dict(host="127.0.0.1", port=8123, proxy_headers=False)]
