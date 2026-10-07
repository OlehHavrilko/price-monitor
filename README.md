# Price Monitor

Monitors prices and stock of products in a few fixed catalogs. It keeps the history in SQLite, sends Telegram alerts for the products you track, and builds XLSX reports.

**Status: release 1 in development.** The code and tests are in place; the demo video, screenshots and a real Telegram delivery check are not done yet (see [Verification](#verification)).

Sources in this release:

- `demo` — a small local catalog that ships with the project, a **test bench**: its prices and stock are changed on purpose to show how the monitor reacts.
- `books` — [books.toscrape.com](https://books.toscrape.com), a public practice site. Its prices and stock never change, so live collection there shows only parsing and pagination. Off by default.

Out of scope: supplier price-list import, ad monitoring, browser automation, CAPTCHA/anti-bot bypass, proxies, a web UI, several currencies.

## Quick start (Docker Compose)

```bash
docker compose up --build
```

This starts the demo catalog (http://127.0.0.1:8080) and the monitor, which collects it every 60 seconds. No Telegram token and no external network are needed. The database is stored in the `data` volume and survives restarts.

Try a change on the demo catalog:

```bash
docker compose exec monitor price-monitor collect demo
docker compose exec demo price-monitor demo-scenario price_drop
docker compose exec monitor price-monitor collect demo
docker compose exec monitor price-monitor report --since 2026-01-01 --until 2027-01-01
docker compose cp monitor:/data/reports ./reports
```

Screenshots of the running demo catalog: [initial](docs/screenshots/shop-initial.png) and [after `price_drop`](docs/screenshots/shop-price-drop.png).

The second `collect` prints `Events: PRICE_CHANGED x2`. `docker compose exec demo price-monitor demo-scenario` without a name lists all scenarios (price drop, stock changes, lost page, pagination loop, missing products, changed page template, and so on).

## Local run without Docker

Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Linux/macOS: .venv/bin/pip
price-monitor demo-serve &                  # catalog on 127.0.0.1:8080
price-monitor collect                       # one collection, no Telegram needed
price-monitor report --since 2026-01-01 --until 2027-01-01
price-monitor run                           # service: schedule + Telegram (if configured)
pytest
```

Files go to `./data` by default (`PRICE_MONITOR_DB`, `PRICE_MONITOR_REPORTS`, `PRICE_MONITOR_DEMO_STATE` change that).

## Telegram

Set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_IDS` (comma-separated numeric user IDs; see `.env.example`). Without both, Telegram stays off. Only allowlisted IDs get answers; everyone else receives "Доступ заборонено." The interface language is Ukrainian.

| Command | Action |
| --- | --- |
| `/sources` | sources, time of the last successful collection, state |
| `/products <source>` | products from the last snapshot |
| `/tracked` | tracked products and their rules |
| `/track <source> <key> [below=PRICE] [drop=PERCENT]` | start or update tracking (repeating it changes nothing) |
| `/untrack <source> <key>` | stop tracking |
| `/report [source] [days]` | XLSX report, 7 days by default, at most 90 |
| `/status` | notification queue and the last delivery error |

Product keys are strings: `00123` and `123` are different products.

Alerts are sent only for tracked products, one message per product and collection, listing every reason that fired: price crossed below `below`, price fell by at least `drop` percent, stock changed between IN_STOCK and OUT_OF_STOCK, the product was confirmed removed, or it came back. A source outage is reported once, and its recovery once.

## How changes are decided

- A collection is stored only if the whole catalog was read: every page answered, no pagination loop, no duplicate keys, the product count matches the count the page declares, the page limit was not hit. Otherwise the run is marked failed and the previous snapshot stays the baseline.
- The first successful collection is a baseline and produces no events.
- `OUT_OF_STOCK` (the page says so) is not the same as a product missing from the catalog, and `UNKNOWN` (no stock information) is neither. Only IN_STOCK ⇄ OUT_OF_STOCK produces a stock event.
- A product is `REMOVED` after two consecutive full successful collections without it. A failed collection in between restarts the count. When it reappears, a `RETURNED` event is created.
- Prices are `Decimal`; percentages are computed exactly. A previous price of 0 never produces a percentage drop.
- Events and queued notifications are written in the same transaction as the snapshot.

## Operating notes

- **One instance.** `price-monitor run` takes a file lock (`service.lock` next to the database); a second service exits. A per-source file lock also stops a manual `collect` from running alongside a scheduled one.
- **After downtime** exactly one fresh collection runs, then the normal interval; missed intervals are not replayed.
- **Delivery is at-least-once.** If Telegram does not answer in time, the message may still have arrived and is sent again, so a rare duplicate is possible. A message is retried up to 6 times with growing delays; a blocked bot or unknown chat is not retried. Failed messages stay in the database and show in `/status`.
- **HTTP 401/403** stops the source. After checking the access terms, resume it with `price-monitor resume-source <source>`.
- **Backup:**
  ```bash
  docker compose exec monitor python -c "import sqlite3; s=sqlite3.connect('/data/price_monitor.db'); s.backup(sqlite3.connect('/data/backup.db'))"
  docker compose cp monitor:/data/backup.db ./backup.db
  ```
- The schema is versioned (`PRAGMA user_version`); a newer database than the program is refused.

## Reports

`price-monitor report --since DATE --until DATE` (UTC, end excluded) writes `Current` (latest observation per product) and `History` (events in the period). Keys stay text, names that start with `=`, `+`, `-` or `@` are stored as text, not formulas. A report larger than 50 000 rows is refused with a request for a shorter period. The file name is generated; a path cannot be chosen.

## Verification

Checked by automated tests (`pytest`, no network, no token): parsing against HTML fixtures, pagination and network failures against a real local HTTP server, comparison rules and boundaries, REMOVED/RETURNED, transactional events + queue, restart of the queue, outage alerts, the file lock across processes, Telegram command logic and the allowlist, XLSX contents.

Run by hand:

- `books` live collection: 1000 products from 50 pages, a repeat gave no events.
- `price-monitor run` without Telegram: scheduled collection works, a second instance is refused.

**Not verified:**

- Real Telegram delivery and polling: no bot token has been used. Only the command logic and error classification are tested, with stand-ins for the network.
- `docker compose up` and the Docker image: Docker was not available where this was written. `Dockerfile`, `compose.yaml` and the CI workflow have not been run.
- The behavior of the `books` site if it changes its markup: a changed template is reported as a failed collection, not as an empty catalog.

## Limitations

- Completeness cannot be proven beyond structural checks (declared count, pagination, duplicates); a source that silently drops a card and updates its declared count would not be noticed.
- `Retry-After` is not honored; retries use fixed exponential delays. Redirects are treated as failures.
- A stock change that passes through `UNKNOWN` between two collections is not reported.
- Stock is only IN_STOCK / OUT_OF_STOCK / UNKNOWN; exact quantities are not read.
- The demo catalog has one currency; `books` prices are GBP. There is no conversion.
- Single process, single machine. No distributed queue.

## License

MIT.
