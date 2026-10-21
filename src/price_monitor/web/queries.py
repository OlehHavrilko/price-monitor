"""Read models over the existing schema. All user values are bound parameters."""
from decimal import Decimal

from .. import outbox, storage

PAGE_SIZE = 25
EVENT_TYPES = ("PRICE_CHANGED", "OUT_OF_STOCK", "BACK_IN_STOCK", "REMOVED", "RETURNED",
               "BELOW_THRESHOLD", "PRICE_DROP_PERCENT", "AVAILABILITY_CHANGED")
AVAILABILITIES = ("IN_STOCK", "OUT_OF_STOCK", "UNKNOWN")


def price(value):
    return Decimal(value) if value is not None else None


def source_rows(conn, configured):
    saved = {r["id"]: r for r in conn.execute("SELECT * FROM sources ORDER BY id")}
    result = []
    for key in sorted(saved.keys() | configured.keys()):
        cfg = configured.get(key)
        last = storage.last_success_run(conn, key)
        health = conn.execute("SELECT * FROM source_health WHERE source_id = ?", (key,)).fetchone()
        state = "stopped" if health and health["stopped"] else (
            "failed" if health and health["alerted"] else "ok" if last else "empty")
        result.append(dict(id=key, title=(cfg.title if cfg else "") or key,
                           state=state, last_success=last["finished_at"] if last else None,
                           product_count=last["product_count"] if last else None,
                           error=health["last_error"] if health else None))
    return result


def source_info(conn, configured, source):
    return next((s for s in source_rows(conn, configured) if s["id"] == source), None)


def products(conn, source, search="", availability="", page=1):
    last = storage.last_success_run(conn, source)
    if not last:
        return [], 0
    where = "o.run_id = ?"
    params = [last["id"]]
    if search:
        # instr makes %, _ and quotes literal; casefold supports Ukrainian search.
        where += " AND (instr(casefold(o.name), ?) > 0 OR instr(casefold(o.product_key), ?) > 0)"
        params += [search.casefold(), search.casefold()]
    if availability:
        where += " AND o.availability = ?"
        params.append(availability)
    total = conn.execute(f"SELECT COUNT(*) FROM observations o WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        "SELECT o.*, EXISTS(SELECT 1 FROM tracking_rules t WHERE t.source_id=o.source_id "
        "AND t.product_key=o.product_key) AS tracked FROM observations o WHERE " + where +
        " ORDER BY o.product_key LIMIT ? OFFSET ?", params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]
    ).fetchall()
    return [dict(r, price=price(r["price"])) for r in rows], total


def product(conn, source, key):
    row = conn.execute(
        "SELECT p.*, o.price, o.currency, o.availability, o.observed_at, o.run_id, "
        "COALESCE(s.removed, 0) AS removed FROM products p "
        "LEFT JOIN observations o ON o.id=(SELECT MAX(id) FROM observations "
        "WHERE source_id=p.source_id AND product_key=p.product_key) "
        "LEFT JOIN product_state s ON s.source_id=p.source_id AND s.product_key=p.product_key "
        "WHERE p.source_id=? AND p.product_key=?", (source, key)).fetchone()
    if not row:
        return None
    last = storage.last_success_run(conn, source)
    result = dict(row, price=price(row["price"]), in_snapshot=bool(last and row["run_id"] == last["id"]))
    rule = conn.execute("SELECT * FROM tracking_rules WHERE source_id=? AND product_key=?", (source, key)).fetchone()
    result["rule"] = dict(rule) if rule else None
    return result


def history(conn, source, key):
    rows = conn.execute(
        "SELECT o.observed_at, o.price, o.currency, o.availability FROM observations o "
        "JOIN collection_runs r ON r.id=o.run_id WHERE o.source_id=? AND o.product_key=? "
        "AND r.status='success' ORDER BY o.id", (source, key))
    return [dict(r, price=str(price(r["price"])) if r["price"] is not None else None) for r in rows]


def events(conn, source="", kind="", key=None, page=1):
    where, params = ["1=1"], []
    if source:
        where.append("e.source_id=?")
        params.append(source)
    if key is not None:
        where.append("e.product_key=?")
        params.append(key)
    if kind in ("OUT_OF_STOCK", "BACK_IN_STOCK"):
        where.append("e.type=? AND e.old_value=? AND e.new_value=?")
        params.extend(["AVAILABILITY_CHANGED", "IN_STOCK" if kind == "OUT_OF_STOCK" else "OUT_OF_STOCK",
                       "OUT_OF_STOCK" if kind == "OUT_OF_STOCK" else "IN_STOCK"])
    elif kind:
        where.append("e.type=?")
        params.append(kind)
    clause = " AND ".join(where)
    total = conn.execute("SELECT COUNT(*) FROM events e WHERE " + clause, params).fetchone()[0]
    rows = conn.execute(
        "SELECT e.*, p.name FROM events e JOIN products p ON p.source_id=e.source_id "
        "AND p.product_key=e.product_key WHERE " + clause + " ORDER BY e.id DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["display_type"] = item["type"]
        if item["type"] == "AVAILABILITY_CHANGED":
            if (item["old_value"], item["new_value"]) == ("IN_STOCK", "OUT_OF_STOCK"):
                item["display_type"] = "OUT_OF_STOCK"
            elif (item["old_value"], item["new_value"]) == ("OUT_OF_STOCK", "IN_STOCK"):
                item["display_type"] = "BACK_IN_STOCK"
        result.append(item)
    return result, total


def delivery_status(conn):
    return dict(counts=outbox.counts(conn), last_failure=outbox.last_failure(conn))
