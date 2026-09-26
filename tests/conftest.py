import pytest

from price_monitor import demo_site, storage
from price_monitor.models import SourceConfig


@pytest.fixture
def demo(tmp_path):
    state = tmp_path / "state.json"
    server, url = demo_site.serve_in_thread(state)
    yield url, state
    server.shutdown()
    server.server_close()


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(tmp_path / "test.db")
    yield c
    c.close()


def make_source(url: str, **overrides) -> SourceConfig:
    params = dict(id="demo", base_url=url, adapter="demo", page_delay=0, retries=1, retry_backoff=0)
    params.update(overrides)
    return SourceConfig(**params)
