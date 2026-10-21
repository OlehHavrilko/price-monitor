# Design notes

Why the project exists, what shape it took and why. What it does and how to run it is in the
[README](../README.md); what was actually verified is in [ACCEPTANCE.md](ACCEPTANCE.md).

## The problem

Prices and stock in online shops change without notice. The cases worth automating are narrow and
concrete: wait for a price to cross a number, learn when a sold-out item returns, notice that a
supplier quietly dropped a product from the catalog. Doing this by hand means opening the same page
every day and comparing it with your memory.

So the useful part is not the parsing. It is deciding *when a difference is real*. A page that fails
to load, a layout that changed, a pagination that silently stops after the first page — all of them
look like "everything disappeared" to a naive scraper, and a monitor that reports that is worse than
no monitor. Most of the work in this project is in refusing to record changes it cannot stand behind.

## Scope of release 1

In: collection of a few fixed catalogs over HTTP with pagination, history in SQLite, comparison with
the last complete successful pass, tracking rules, Telegram alerts from a persistent queue, an XLSX
report, a scheduler, a local demo shop to play every scenario, Docker Compose, CI.

Out, deliberately: importing a supplier's price list, comparing several shops with each other,
monitoring classified ads, a parser for an arbitrary URL, browser automation, CAPTCHA or anti-bot
bypass, proxies, a web UI, several currencies per product, payments, LLM features. A read-only web
dashboard was added after release 1 (see [Web dashboard](#web-dashboard)); editing from the web
is still out.

The excluded list is not modesty. Each item changes the shape of the product: an arbitrary-URL parser
kills the guarantee that a snapshot is complete, and cross-shop comparison needs a matching table
between catalogs, which is a different project. Keeping the source list fixed and small is what makes
"the catalog was read fully" a checkable claim.

## What it watches

Four sources, each with a fixed configuration (id, base URL, adapter, interval, timeouts, page limit):

- `xcom` — one category of xcom.ua, a real Ukrainian shop. The only real shop that answers from a
  datacenter address; the larger shops tried first return HTTP 403 to a server IP.
- `allo` — one category of allo.ua, a real shop that refuses datacenter addresses, so it works from a
  home connection only. Kept because it proves the adapter contract survives a shop with cards that
  sometimes have no price at all.
- `books` — books.toscrape.com, a public practice site. Off by default. Nothing changes there, so it
  demonstrates parsing and pagination over 1000 products, not monitoring.
- `demo` — a small catalog that ships with the project and whose prices are changed by a script. It
  is a test bench, not a product, and the README says so.

Telegram cannot add a source and cannot pass a URL: it only accepts an id of a configured source and
a product key. Otherwise the bot becomes a way to make the server fetch arbitrary addresses,
including internal ones.

## Completeness before correctness

A collection is stored only as a whole snapshot, and only if every page answered, pagination did not
loop, no product key repeated, the page limit was not hit, and the number of cards matches the number
the page declares when it declares one. Any page error fails the whole pass; the previous snapshot
stays the baseline. This is why a failed collection never looks like an empty catalog.

The honest limit: without a contract from the shop itself, completeness cannot be proven. A source
that drops a card *and* updates its own declared count would pass. The structural checks are what is
available, and they are listed as such in the README.

Two more small decisions belong here. All pages of a source are fetched and validated before the
transaction opens, so no network call happens inside a database transaction. And a source that
answers 401/403 is stopped instead of retried: it is a statement about access, not a temporary fault,
and the operator has to look at the terms before resuming.

## Data model

SQLite, schema version in `PRAGMA user_version`, a database from a newer version is refused. Tables:
`sources`, `collection_runs`, `products`, `observations`, `product_state` (consecutive absences
and the removed flag), `tracking_rules`, `events`, `notification_outbox`, `source_health` (an outage
already reported, a source stopped after 401/403).

Prices are `Decimal` in code and a canonical decimal string in the database; percentages are computed
exactly. Nothing about money goes through float.

`availability` has three values, and the third one matters: `IN_STOCK`, `OUT_OF_STOCK`, `UNKNOWN`.
Unknown stock is not zero and not "out of stock"; a card that does not state it gets `UNKNOWN`, and
no stock event is built from `UNKNOWN`.

`product_key` is stable within a source: an explicit SKU when the page has one, otherwise a documented
key derived from the canonical product path. Never the title, never the price. Keys are strings, so
`00123` and `123` are different products. Two sources have separate key spaces and equal titles do not
link anything.

Time is stored in UTC and the report names its timezone. The last successful collection and the time
of the last attempt are stored and shown separately, because "no data" and "no fresh data" are
different states.

## When a change counts

Four rules:

1. the price fell by at least N percent since the previous successful observation (N positive);
2. the price crossed below a threshold — from above or from the threshold itself, strictly lower;
   repeating the same value never fires;
3. `IN_STOCK` → `OUT_OF_STOCK` and back;
4. the product was confirmed removed, and later returned.

`REMOVED` is the interesting one. A key missing from a whole catalog is a *candidate*, not an absence:
the product may be on a page that failed. It becomes confirmed only after two complete successful
passes in a row without it, and a failed pass in between restarts the count. A product that appears
again clears the counter and, if it had been confirmed removed, produces a `RETURNED` event. Tracking
state for missing products lives separately from the snapshot, so a product is not forgotten the first
time it is lost.

Event uniqueness is a (run, product, event type) triple, not a pair of values. A drop from 10 to 9
after a return from 9 to 10 is a new real event, and must not be swallowed as a duplicate.

Price changes are stored in history whether or not they reach a notification threshold. Several rules
firing on one change produce one message that lists the reasons, so a price drop that also crosses a
threshold is not two alerts.

## Delivery: at least once, never exactly once

Events and outbox rows are written in the same transaction as the snapshot, and delivery happens
afterwards. A restart continues from the queue. A message is retried up to six times with growing
delays; a blocked bot or an unknown chat is not retried at all, and the failure stays visible in
`/status`.

If Telegram times out, the message may have arrived and still be sent again. That is documented
instead of being promised away: exactly-once delivery over a network the program does not control is
not achievable, and pretending otherwise would be a lie in the documentation. The guarantee offered is
weaker and true: nothing is lost from the database, and a rare duplicate is possible.

## Schedule and one instance

APScheduler in the same process, with `max_instances=1` and `coalesce` so a slow collection does not
stack runs. After downtime exactly one fresh collection runs, then the normal interval: replaying
missed intervals would invent observations that were never made.

A file lock next to the database keeps a second service instance out, and a per-source lock keeps a
manual `collect` from overlapping a scheduled one. One process, one machine — no distributed queue,
because there is nothing here that needs one.

## Demo shop

Real shops do not change on demand, so a demo of "the price dropped" would require waiting for a real
price drop. The demo shop is a tiny HTTP catalog whose state is switched by a CLI command on the
demo container, never over the network: `price_drop`, `out_of_stock`, `page_lost`, `pagination_cycle`,
`product_missing`, `two_products_missing`, `changed_template`, `duplicate_key`, `count_mismatch` and
others. It is called a test bench in the README and in the messages the bot sends, because a scripted
catalog shown as a real shop would be dishonest.

`demo-play` walks the scenarios in order with pauses, which is how the video was recorded.

## Reports

`price-monitor report --since DATE --until DATE` writes a `Current` sheet (latest observation per
product) and a `History` sheet (events in the period). Keys are written as text, and titles starting
with `=`, `+`, `-` or `@` are written as text too — a spreadsheet is a program, and a product name must
not become a formula. The file name is generated and the path is not accepted from the outside. A
report above 50 000 rows is refused with a request for a shorter period instead of silently producing
something enormous.

## Web dashboard

`price-monitor web` serves six pages over the same database: sources, products of the last
successful snapshot (search, stock and tracking filters), the price history of one product with a
chart, events, an XLSX report and the service status. It is a window, not a second control panel:
tracking rules are still changed in Telegram and collections still run in the monitor, so there is
one place where state changes.

Read-only is enforced by SQLite, not by convention. The dashboard never calls `storage.connect()`,
which switches the journal mode and migrates; it opens the file with `mode=ro` and `PRAGMA
query_only`, refuses a missing database instead of creating one, and refuses a schema version other
than its own. The data volume is still mounted writable in Compose: a reader of a WAL database has to
create the shared-memory index file, and with a `:ro` mount the database cannot be opened whenever
the monitor has no connection open, which is most of the time between collections.

Stock events are stored as `AVAILABILITY_CHANGED` with the old and new value. "Sold out" and "back in
stock" are filters over that, not new event types, so the stored history and the report do not
change. Prices reach the page as the stored decimal strings; the chart turns them into numbers only
to place the points.

Access is one shared token (`PRICE_MONITOR_WEB_TOKEN`, at least 16 characters, no default). A browser
signs in with a form and gets an HttpOnly, SameSite=Strict cookie that holds an HMAC of the token, not
the token; scripts send `Authorization: Bearer`. The token is never in a URL. Compose publishes the
port on 127.0.0.1 only, and the dashboard is behind a profile, so `docker compose up` without a token
starts exactly what it started before. Pages send a strict Content-Security-Policy; htmx and Chart.js
are served from the package, so the page works without a CDN. The XLSX is built by the same
`reports.write_report()` as the CLI and the bot, in a temporary directory removed after the download.

## Technical choices worth naming

- **SQLite** — the whole dataset is one operator's catalog history, a few thousand rows per week. A
  server database would add deployment and backup work for no gain. The report and the bot read the
  same file.
- **HTTPX + BeautifulSoup** — the sources serve server-rendered HTML, so a browser engine or a
  JS-capable scraper would be weight without purpose. Pagination is followed link by link, with
  delays, bounded retries and an identifying User-Agent.
- **aiogram 3** — long polling, no inbound port, which is all a small bot needs on a VPS.
- **openpyxl** — it gives exact control over cell types, which is what the formula guard above relies
  on.
- **Pillow for alert cards** — the alert picture is generated from the same numbers as the caption, so
  the two cannot disagree. It is a small PNG, not a dashboard.
- **FastAPI + Jinja2 + htmx** for the dashboard — server-rendered pages that also work without
  JavaScript; htmx only swaps the result table when a filter changes. No build step, no SPA.
- **APScheduler inside the process** — a separate worker plus a queue would be the right shape for
  many sources and several machines. Here it would only add moving parts.

## Known limits

Stated as they are in the README, and not repeated here: completeness beyond structural checks,
`Retry-After` not honoured, redirects treated as failures, a stock change that passes through
`UNKNOWN` between two collections going unreported, no exact quantities, single process, and the
shop terms of service of the two real shops not having been reviewed.
