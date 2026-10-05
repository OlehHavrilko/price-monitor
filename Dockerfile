FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PRICE_MONITOR_DB=/data/price_monitor.db \
    PRICE_MONITOR_REPORTS=/data/reports \
    PRICE_MONITOR_DEMO_STATE=/demo-state/state.json

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

# Fixed UID so both containers can write to the shared named volumes.
RUN useradd --uid 10001 --create-home monitor && mkdir -p /data /demo-state && chown monitor /data /demo-state
USER monitor
VOLUME ["/data"]

CMD ["price-monitor", "run"]
