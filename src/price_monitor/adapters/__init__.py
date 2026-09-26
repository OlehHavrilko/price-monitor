from __future__ import annotations

from collections.abc import Callable

import httpx

from ..models import CollectionError, ProductObservation, SourceConfig
from . import demo

ADAPTERS: dict[str, Callable[[httpx.Client, SourceConfig], list[ProductObservation]]] = {
    "demo": demo.fetch_snapshot,
}


def fetch_snapshot(client: httpx.Client, source: SourceConfig) -> list[ProductObservation]:
    try:
        adapter = ADAPTERS[source.adapter]
    except KeyError:
        raise CollectionError(f"Unknown adapter '{source.adapter}' for source '{source.id}'", fatal=True)
    return adapter(client, source)
