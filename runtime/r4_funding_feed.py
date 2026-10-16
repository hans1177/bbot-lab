# 파일명: r4_funding_feed.py
"""Read-only Binance funding-history collection and exact LEG reconciliation."""

from __future__ import annotations

import hashlib
from contextlib import closing
from decimal import Decimal, InvalidOperation


SOURCE = "BINANCE_INCOME_HISTORY"
INCOME_TYPE = "FUNDING_FEE"
PAGE_SIZE = 1000
QUERY_CHUNK_MS = 7 * 24 * 60 * 60 * 1000


def _integer(value, field):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"INVALID_FUNDING_{field.upper()}") from exc
    if result < 0:
        raise ValueError(f"INVALID_FUNDING_{field.upper()}")
    return result


def _income_payload(row):
    if not isinstance(row, dict):
        raise ValueError("INVALID_BINANCE_FUNDING_ROW")
    if str(row.get("incomeType") or "").upper() != INCOME_TYPE:
        return None
    funding_id = str(row.get("tranId") or "").strip()
    symbol = str(row.get("symbol") or "").upper().strip()
    asset = str(row.get("asset") or "").upper().strip()
    raw_amount = row.get("income")
    if not isinstance(raw_amount, str) or not raw_amount.strip():
        raise ValueError("BINANCE_FUNDING_AMOUNT_MUST_BE_DECIMAL_TEXT")
    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INVALID_BINANCE_FUNDING_AMOUNT") from exc
    timestamp_ms = _integer(row.get("time"), "timestamp")
    if not funding_id or not symbol or not asset or not amount.is_finite():
        raise ValueError("INCOMPLETE_BINANCE_FUNDING_ROW")
    return {
        "funding_id": funding_id,
        "symbol": symbol,
        "asset": asset,
        "amount": format(amount, "f"),
        "timestamp_ms": timestamp_ms,
        "timestamp": timestamp_ms // 1000,
        "income_type": INCOME_TYPE,
    }


def fetch_funding_income(exchange, start_time_ms, end_time_ms, page_size=PAGE_SIZE):
    """Fetch all funding rows for the inclusive interval; raise if pagination is uncertain."""
    start = _integer(start_time_ms, "start_time")
    end = _integer(end_time_ms, "end_time")
    limit = _integer(page_size, "page_size")
    if start > end or limit <= 0 or limit > PAGE_SIZE:
        raise ValueError("INVALID_FUNDING_QUERY_RANGE_OR_PAGE_SIZE")

    order = getattr(exchange, "order", None)
    client = getattr(order, "v3", None)
    endpoint = getattr(client, "futures_income_history", None)
    safe_call = getattr(order, "safe_call", None)
    if not callable(endpoint) or not callable(safe_call):
        raise RuntimeError("BINANCE_FUNDING_HISTORY_ENDPOINT_UNAVAILABLE")

    rows_by_id = {}
    cursor = start
    while cursor <= end:
        page = safe_call(
            endpoint,
            startTime=cursor,
            endTime=end,
            incomeType=INCOME_TYPE,
            limit=limit,
        )
        if not isinstance(page, list):
            raise RuntimeError("BINANCE_FUNDING_HISTORY_QUERY_FAILED")
        if len(page) > limit:
            raise RuntimeError("BINANCE_FUNDING_HISTORY_PAGE_OVERFLOW")

        normalized_page = []
        for raw in page:
            item = _income_payload(raw)
            if item is None:
                continue
            if not cursor <= item["timestamp_ms"] <= end:
                raise RuntimeError("BINANCE_FUNDING_ROW_OUTSIDE_QUERY_RANGE")
            normalized_page.append(item)
            previous = rows_by_id.get(item["funding_id"])
            if previous is not None and previous != item:
                raise RuntimeError("BINANCE_FUNDING_TRANSACTION_ID_CONFLICT")
            rows_by_id[item["funding_id"]] = item

        if len(page) < limit:
            break
        if not normalized_page:
            raise RuntimeError("BINANCE_FUNDING_PAGINATION_NO_PROGRESS")
        last_timestamp = max(item["timestamp_ms"] for item in normalized_page)
        same_timestamp_page = safe_call(
            endpoint,
            startTime=last_timestamp,
            endTime=last_timestamp,
            incomeType=INCOME_TYPE,
            limit=limit,
        )
        if not isinstance(same_timestamp_page, list):
            raise RuntimeError("BINANCE_FUNDING_BOUNDARY_QUERY_FAILED")
        if len(same_timestamp_page) >= limit:
            raise RuntimeError("BINANCE_FUNDING_TIMESTAMP_BUCKET_OVERFLOW")
        # 파일명: r4_funding_feed.py / 페이지 경계의 동일 시각 내역도 대사에 포함한다.
        boundary_ids = set()
        for raw in same_timestamp_page:
            item = _income_payload(raw)
            if item is None or item["timestamp_ms"] != last_timestamp:
                raise RuntimeError("BINANCE_FUNDING_BOUNDARY_ROW_INVALID")
            previous = rows_by_id.get(item["funding_id"])
            if previous is not None and previous != item:
                raise RuntimeError("BINANCE_FUNDING_TRANSACTION_ID_CONFLICT")
            rows_by_id[item["funding_id"]] = item
            boundary_ids.add(item["funding_id"])
        expected_boundary_ids = {
            item["funding_id"] for item in normalized_page
            if item["timestamp_ms"] == last_timestamp
        }
        if not expected_boundary_ids.issubset(boundary_ids):
            raise RuntimeError("BINANCE_FUNDING_BOUNDARY_QUERY_INCOMPLETE")
        next_cursor = last_timestamp + 1
        if next_cursor <= cursor:
            raise RuntimeError("BINANCE_FUNDING_PAGINATION_INCOMPLETE")
        if next_cursor > end:
            break
        cursor = next_cursor

    return sorted(
        rows_by_id.values(),
        key=lambda item: (item["timestamp_ms"], item["funding_id"]),
    )


def _leg_intervals(ledger, symbols):
    if not symbols:
        return []
    placeholders = ",".join("?" for _ in symbols)
    with closing(ledger.connect()) as con:
        legs = con.execute(
            f"SELECT * FROM r4_active_legs WHERE UPPER(symbol) IN ({placeholders}) "
            "AND status IN ('ACTIVE','CLOSED')",
            tuple(symbols),
        ).fetchall()
        out = []
        for leg_row in legs:
            leg = dict(leg_row)
            evidence = con.execute(
                "SELECT kind,exchange_ts FROM r4_trade_evidence WHERE leg_id=? "
                "AND kind IN ('ENTRY_FILL','EXIT_FILL') ORDER BY exchange_ts,id",
                (leg["leg_id"],),
            ).fetchall()
            entries = [
                int(row["exchange_ts"] or 0)
                for row in evidence
                if row["kind"] == "ENTRY_FILL"
            ]
            exits = [
                int(row["exchange_ts"] or 0)
                for row in evidence
                if row["kind"] == "EXIT_FILL"
            ]
            leg["funding_start_ts"] = min(entries) if entries and min(entries) > 0 else 0
            leg["funding_end_ts"] = max(exits) if exits else 0
            out.append(leg)
    return out


def _attribution_key(item, leg_id):
    payload = f"{item['funding_id']}|{item['symbol']}|{item['timestamp_ms']}|{leg_id}"
    return "BINANCE_FUNDING_ATTR_" + hashlib.sha256(payload.encode()).hexdigest()


def reconcile_funding_income(
    exchange,
    ledger,
    target_instance_ids,
    start_time_ms,
    end_time_ms,
    page_size=PAGE_SIZE,
):
    """Collect funding and attach it only when one exact LEG spans the event."""
    targets = {str(value) for value in target_instance_ids if str(value)}
    if not targets:
        return {"complete": True, "fetched": 0, "attributed": 0, "ambiguous": 0, "held_legs": []}

    start_ms = _integer(start_time_ms, "start_time")
    end_ms = _integer(end_time_ms, "end_time")
    items_by_id = {}
    cursor = start_ms
    while cursor <= end_ms:
        chunk_end = min(end_ms, cursor + QUERY_CHUNK_MS - 1)
        for item in fetch_funding_income(exchange, cursor, chunk_end, page_size):
            previous = items_by_id.get(item["funding_id"])
            if previous is not None and previous != item:
                raise RuntimeError("BINANCE_FUNDING_TRANSACTION_ID_CONFLICT")
            items_by_id[item["funding_id"]] = item
        cursor = chunk_end + 1
    items = sorted(
        items_by_id.values(),
        key=lambda item: (item["timestamp_ms"], item["funding_id"]),
    )
    intervals = _leg_intervals(ledger, {item["symbol"] for item in items})
    by_symbol = {}
    for leg in intervals:
        by_symbol.setdefault(str(leg["symbol"]).upper(), []).append(leg)

    ambiguous_legs = set()
    assigned_by_leg = {}
    legs_by_id = {str(leg["leg_id"]): leg for leg in intervals}
    attributed = 0
    ambiguous_count = 0
    for item in items:
        timestamp = item["timestamp"]
        candidates = []
        uncertain = []
        for leg in by_symbol.get(item["symbol"], []):
            start = int(leg.get("funding_start_ts") or 0)
            end = int(leg.get("funding_end_ts") or 0)
            if start and start <= timestamp and (not end or timestamp <= end):
                candidates.append(leg)
            elif not start and int(leg.get("created_ts") or 0) <= timestamp <= int(leg.get("updated_ts") or 0):
                uncertain.append(leg)

        if len(candidates) != 1 or uncertain:
            ambiguous_count += 1
            ambiguous_legs.update(
                str(leg["leg_id"]) for leg in candidates + uncertain
                if str(leg["instance_id"]) in targets
            )
            continue

        leg = candidates[0]
        if str(leg["instance_id"]) not in targets:
            continue
        attribution = {
            "leg_id": str(leg["leg_id"]),
            "basis": "UNIQUE_LEG_FILL_INTERVAL",
            "verified": True,
            "candidate_leg_ids": [str(leg["leg_id"])],
            "entry_ts": int(leg["funding_start_ts"]),
            "exit_ts": int(leg["funding_end_ts"]),
            "evidence_key": _attribution_key(item, str(leg["leg_id"])),
        }
        saved = ledger.record_funding_evidence(
            str(leg["leg_id"]),
            item["funding_id"],
            item["amount"],
            item["asset"],
            SOURCE,
            timestamp,
            attribution,
            value_usdt_text=item["amount"] if item["asset"] == "USDT" else "",
            valuation_source="BINANCE_FUNDING_ASSET_IS_QUOTE" if item["asset"] == "USDT" else "",
        )
        assigned_by_leg.setdefault(str(leg["leg_id"]), []).append(saved)
        attributed += 1

    held_legs = set(ambiguous_legs)
    coverage_rows = []
    for instance_id in targets:
        cohort = ledger.performance_tier_cohort(instance_id)
        for entry in cohort["legs"]:
            leg = entry["leg"]
            leg_id = str(leg["leg_id"])
            if entry.get("funding_coverage") and entry["funding_coverage"].get("complete"):
                continue
            start_ts = int(leg.get("created_ts") or 0)
            exit_ts = max(
                (
                    int(row.get("exchange_ts") or 0)
                    for row in entry["evidence"]
                    if row.get("kind") == "EXIT_FILL"
                ),
                default=0,
            )
            if not start_ts or not exit_ts or leg_id in held_legs:
                held_legs.add(leg_id)
                continue
            if int(start_time_ms) > start_ts * 1000 or int(end_time_ms) < exit_ts * 1000:
                held_legs.add(leg_id)
                continue
            if any(not row.get("evidence_complete") for row in assigned_by_leg.get(leg_id, [])):
                held_legs.add(leg_id)
                continue
            with closing(ledger.connect()) as con:
                count = int(con.execute(
                    "SELECT COUNT(*) FROM r4_trade_evidence WHERE leg_id=? AND kind='FUNDING' "
                    "AND evidence_complete=1 AND exchange_ts>=? AND exchange_ts<=?",
                    (leg_id, start_ts, exit_ts),
                ).fetchone()[0] or 0)
            coverage_key = "BINANCE_FUNDING_COVERAGE_" + hashlib.sha256(
                f"{leg_id}|{start_ts}|{exit_ts}|{len(items)}".encode()
            ).hexdigest()
            ledger.confirm_funding_coverage(
                leg_id,
                SOURCE,
                start_ts,
                exit_ts,
                coverage_key,
                count,
            )
            coverage_rows.append(leg_id)

    return {
        "complete": not held_legs,
        "fetched": len(items),
        "attributed": attributed,
        "ambiguous": ambiguous_count,
        "covered_legs": sorted(coverage_rows),
        "held_legs": sorted(held_legs),
        "held_instances": sorted({
            str(legs_by_id[leg_id]["instance_id"])
            for leg_id in held_legs
            if leg_id in legs_by_id
        }),
    }
