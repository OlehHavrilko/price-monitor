# Acceptance criteria of release 1: status and evidence

The criteria release 1 was built against, and what was actually checked for each. "Test" means an
automated test in `tests/` that runs without a network or a Telegram token (`pytest`). Status is
stated as it is, including what is not verified.

| # | Criterion (short) | Status | Evidence |
| --- | --- | --- | --- |
| 1 | First collection creates the database and a short report; a repeat without changes creates no events | Done | `test_history::test_first_run_is_a_baseline_and_unchanged_repeat_creates_no_events`; the `collect` command prints the count and "No changes since the previous successful run" |
| 2 | Price drops and stock changes give the expected events; threshold, equality, percent, zero previous price | Done | `test_comparison` (threshold, exact percent, zero price), `test_history`, `test_delivery` |
| 3 | A page error, missing page, pagination loop, repeated ID or changed template never replace the last good snapshot | Done | `test_collect` (five broken demo scenarios, page limit, redirect, foreign next link), `test_allo_adapter`, `test_xcom_adapter` |
| 4 | Explicit OUT_OF_STOCK differs from absence and UNKNOWN; REMOVED only after two full successful absences; error between runs; return | Done | `test_comparison::test_removed_needs_two_consecutive_absences...`, `test_history::test_removed_after_two_full_runs...`, `test_failed_run_between_absences_restarts_the_series` |
| 5 | Events and outbox saved transactionally; restart continues delivery; ambiguous network outcome and possible duplicate | Done with a limit | Transactional write and restart: `test_delivery`. The lost-acknowledgement case is tested with a stand-in **sender function**, not with a substituted HTTP transport |
| 6 | Parsers tested on HTML fixtures without a network; a local HTTP stand tests real pagination and network failures | Done | `test_demo_adapter`, `test_books_adapter`, `test_allo_adapter`, `test_xcom_adapter` (fixtures); `test_collect` (real local HTTP server) |
| 7 | One broken source does not block another; repeated errors do not flood Telegram | Done | `test_delivery::test_broken_source_does_not_block_another_source`, `test_source_outage_is_reported_once_and_recovery_once` |
| 8 | Two simultaneous runs of one source do not write history concurrently; no flood of missed runs after a restart | Done, partly by configuration | Thread and cross-process file locks: `test_delivery::test_file_lock_keeps_a_second_process_out`. `coalesce`, `max_instances=1` and the single immediate run after a start are set in `scheduler.py` and checked by hand (a second service instance is refused); there is no automated test of the scheduler |
| 9 | An unauthorised Telegram ID changes no rules and gets no reports; a repeated update does not duplicate tracking | Done | `test_telegram`, `test_menu` |
| 10 | XLSX reopens, matches the data, keeps string keys, has no executable formulas from text fields | Done | `test_reports`, `test_null_price` |
| 11 | A clean run by the README builds a first report on demo data without a network or Telegram; the database and tracking survive a container restart | Done on the VPS | `docker compose up --build` on aarch64 Ubuntu 24.04, `down` / `up` kept the history and events. CI also builds the image |
| 12 | Live smoke of a public site and a real delivery with a test bot, if a token was provided | Partly | Live collections: books.toscrape.com (1000 products), xcom.ua (52, from the VPS and locally), allo.ua (164, home connection only). Real Telegram: text alerts, bot profile and polling worked against a real bot. Picture cards and the XLSX file were accepted by the Bot API (`sent`), but the owner has not yet confirmed how they look in the chat |

## Priorities

P0 criteria are done except the limits stated for 5 and 8. Of the P1 items: the frequency limit for
identical source errors and the recovery message are done; `Retry-After` is **not** honoured; the
lost-acknowledgement test uses a stand-in sender; the report size limit (50 000 rows) is done.

## Not covered

- Screenshots from a real Telegram chat and the screen recording (see `docs/video-script.md`).
- The terms of service of xcom.ua and allo.ua were not reviewed.
- No test drives the aiogram dispatcher with real updates; button handling is tested on the menu
  layer (`test_menu`), and the wiring to Telegram was only exercised by hand.
