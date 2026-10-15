#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import struct
import time
from pathlib import Path
from typing import Any

from binance.um_futures import UMFutures


BASE = Path(
    os.getenv(
        "BBOT_BASE",
        "/home/ec2-user/bot",
    )
)

STATE = BASE / "state"

REFERENCE_FILE = (
    STATE
    / "green7a_preorder_reference_evidence.json"
)

EVIDENCE_FILE = (
    STATE
    / "green7a_preorder_microstructure_evidence.json"
)

TARGET_BASENAME = REFERENCE_FILE.name

SCHEMA = (
    "GREEN7A_PREORDER_MICROSTRUCTURE_EVIDENCE_V1"
)

SERVICE_SCHEMA = (
    "GREEN7A_PREORDER_MICROSTRUCTURE_WATCHER_V1"
)

REFERENCE_TIMING = (
    "AFTER_FINAL_ENTRY_RISK_BEFORE_MARKET_ORDER"
)

DEPTH_LIMIT = 1000
RETENTION_LIMIT = 2000

IN_CLOSE_WRITE = 0x00000008
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_Q_OVERFLOW = 0x00004000

WATCH_MASK = (
    IN_CLOSE_WRITE
    | IN_MOVED_TO
    | IN_CREATE
    | IN_Q_OVERFLOW
)

EVENT_STRUCT = struct.Struct("iIII")


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except FileNotFoundError:
        return default


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    os.replace(
        tmp,
        path,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def _positive(value: Any) -> float | None:
    try:
        x = float(value)
    except Exception:
        return None

    if x <= 0:
        return None

    return x


def _reference_records() -> dict[str, dict[str, Any]]:
    obj = _load_json(
        REFERENCE_FILE,
        {},
    )

    records = obj.get("records")

    if not isinstance(records, dict):
        return {}

    return {
        str(k): v
        for k, v in records.items()
        if isinstance(v, dict)
    }


def _validate_reference(
    entry_id: str,
    row: dict[str, Any],
) -> dict[str, Any]:

    if str(
        row.get("entry_id")
        or ""
    ) != entry_id:
        raise RuntimeError(
            "ENTRY_ID_BINDING_MISMATCH"
        )

    if (
        row.get("reference_timing")
        != REFERENCE_TIMING
    ):
        raise RuntimeError(
            "REFERENCE_TIMING_INVALID"
        )

    snap = row.get(
        "pending_snapshot"
    )

    if not isinstance(snap, dict):
        raise RuntimeError(
            "PENDING_SNAPSHOT_MISSING"
        )

    symbol = str(
        snap.get("symbol")
        or row.get("symbol")
        or ""
    ).upper().strip()

    side = str(
        snap.get("side")
        or row.get("side")
        or ""
    ).upper().strip()

    qty = _positive(
        snap.get("qty")
    )

    ref = _positive(
        row.get(
            "preorder_reference_price"
        )
    )

    captured_at_ns = int(
        row.get("captured_at_ns")
        or 0
    )

    if not symbol:
        raise RuntimeError(
            "SYMBOL_MISSING"
        )

    if side not in {"BUY", "SELL"}:
        raise RuntimeError(
            "SIDE_INVALID"
        )

    if qty is None:
        raise RuntimeError(
            "QTY_INVALID"
        )

    if ref is None:
        raise RuntimeError(
            "PREORDER_REFERENCE_PRICE_INVALID"
        )

    if captured_at_ns <= 0:
        raise RuntimeError(
            "CAPTURE_TIME_INVALID"
        )

    return {
        "entry_id": entry_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "preorder_reference_price": ref,
        "captured_at_ns": captured_at_ns,
        "strategy_version": str(
            row.get("strategy_version")
            or snap.get("strategy_version")
            or ""
        ),
        "timeframe": str(
            row.get("entry_tf")
            or snap.get("entry_tf")
            or ""
        ).upper(),
        "source_pending_key": str(
            row.get("source_pending_key")
            or ""
        ),
    }


def _execution_from_levels(
    side: str,
    qty: float,
    levels: list,
    reference_price: float,
) -> dict[str, Any]:

    needed = float(qty)
    filled = 0.0
    quote = 0.0
    consumed = []

    for raw in levels:
        if (
            not isinstance(raw, (list, tuple))
            or len(raw) < 2
        ):
            continue

        price = _positive(raw[0])
        available = _positive(raw[1])

        if (
            price is None
            or available is None
        ):
            continue

        take = min(
            available,
            needed - filled,
        )

        if take <= 0:
            break

        quote += take * price
        filled += take

        consumed.append(
            [
                str(raw[0]),
                str(raw[1]),
                take,
            ]
        )

        if filled >= needed:
            break

    complete = (
        filled + 1e-12
        >= needed
    )

    vwap = (
        quote / filled
        if filled > 0
        else None
    )

    signed_cost_bps = None
    abs_impact_bps = None

    if complete and vwap is not None:
        if side == "BUY":
            signed_cost_bps = (
                vwap / reference_price
                - 1.0
            ) * 10000.0
        else:
            signed_cost_bps = (
                1.0
                - vwap / reference_price
            ) * 10000.0

        abs_impact_bps = abs(
            signed_cost_bps
        )

    return {
        "requested_qty": needed,
        "fillable_qty": filled,
        "fully_executable": complete,
        "executable_vwap": vwap,
        "signed_cost_bps_vs_preorder_reference":
            signed_cost_bps,
        "abs_impact_bps_vs_preorder_reference":
            abs_impact_bps,
        "consumed_levels": consumed,
    }


def capture_one(
    entry_id: str,
    row: dict[str, Any],
    client: Any | None = None,
) -> dict[str, Any]:

    ref = _validate_reference(
        entry_id,
        row,
    )

    if client is None:
        client = UMFutures()

    symbol = ref["symbol"]
    side = ref["side"]

    request_start_ns = time.time_ns()

    book = client.book_ticker(
        symbol
    )

    book_receive_ns = time.time_ns()

    depth = client.depth(
        symbol,
        limit=DEPTH_LIMIT,
    )

    depth_receive_ns = time.time_ns()

    returned_symbol = str(
        book.get("symbol")
        or ""
    ).upper()

    if (
        returned_symbol
        and returned_symbol != symbol
    ):
        raise RuntimeError(
            "BOOK_SYMBOL_BINDING_MISMATCH"
        )

    levels = (
        depth.get("asks")
        if side == "BUY"
        else depth.get("bids")
    )

    if not isinstance(levels, list):
        raise RuntimeError(
            "DEPTH_LEVELS_MISSING"
        )

    execution = _execution_from_levels(
        side,
        ref["qty"],
        levels,
        ref["preorder_reference_price"],
    )

    bid = _positive(
        book.get("bidPrice")
    )

    ask = _positive(
        book.get("askPrice")
    )

    spread_bps = None

    if (
        bid is not None
        and ask is not None
        and ask >= bid
    ):
        mid = (
            bid + ask
        ) / 2.0

        if mid > 0:
            spread_bps = (
                ask - bid
            ) / mid * 10000.0

    return {
        "entry_id":
            entry_id,

        "symbol":
            symbol,

        "side":
            side,

        "qty":
            ref["qty"],

        "strategy_version":
            ref["strategy_version"],

        "timeframe":
            ref["timeframe"],

        "reference_timing":
            REFERENCE_TIMING,

        "preorder_reference_price":
            ref["preorder_reference_price"],

        "preorder_captured_at_ns":
            ref["captured_at_ns"],

        "micro_request_start_ns":
            request_start_ns,

        "book_receive_ns":
            book_receive_ns,

        "depth_receive_ns":
            depth_receive_ns,

        "book_time_ms":
            book.get("time"),

        "book_last_update_id":
            book.get("lastUpdateId"),

        "depth_event_time_ms":
            depth.get("E"),

        "depth_transaction_time_ms":
            depth.get("T"),

        "depth_last_update_id":
            depth.get("lastUpdateId"),

        "depth_limit":
            DEPTH_LIMIT,

        "best_bid":
            bid,

        "best_ask":
            ask,

        "best_bid_qty":
            _positive(
                book.get("bidQty")
            ),

        "best_ask_qty":
            _positive(
                book.get("askQty")
            ),

        "spread_bps":
            spread_bps,

        "execution_from_depth":
            execution,

        "point_in_time_candidate":
            True,

        "first_fill_before_snapshot_checked":
            False,

        "validated":
            False,

        "independent_validation":
            False,

        "policy_candidate_allowed":
            False,

        "threshold_derivation_performed":
            False,

        "live_effect":
            False,

        "api_contract":
            "READ_ONLY_PUBLIC_MARKET_DATA_GET",

        "order_api_used":
            False,

        "real_order_created":
            False,
    }


def _empty_output() -> dict[str, Any]:
    return {
        "schema":
            SCHEMA,

        "records":
            {},

        "retention_limit":
            RETENTION_LIMIT,

        "threshold_derivation_performed":
            False,

        "policy_candidate_allowed":
            False,

        "live_effect":
            False,
    }


def _store_record(
    entry_id: str,
    record: dict[str, Any],
) -> None:

    obj = _load_json(
        EVIDENCE_FILE,
        _empty_output(),
    )

    if not isinstance(obj, dict):
        obj = _empty_output()

    records = obj.setdefault(
        "records",
        {},
    )

    if not isinstance(records, dict):
        raise RuntimeError(
            "MICROSTRUCTURE_RECORDS_INVALID"
        )

    if entry_id in records:
        return

    records[entry_id] = record

    if len(records) > RETENTION_LIMIT:
        ordered = sorted(
            records.items(),
            key=lambda kv: int(
                (
                    kv[1]
                    if isinstance(kv[1], dict)
                    else {}
                ).get(
                    "preorder_captured_at_ns"
                )
                or 0
            ),
        )

        remove_count = (
            len(records)
            - RETENTION_LIMIT
        )

        for old_id, _ in ordered[
            :remove_count
        ]:
            records.pop(
                old_id,
                None,
            )

    obj["updated_at_ns"] = (
        time.time_ns()
    )

    obj["source_preorder_evidence_sha256"] = (
        _sha256(
            REFERENCE_FILE
        )
    )

    _atomic_json(
        EVIDENCE_FILE,
        obj,
    )


def capture_new(
    candidate_ids: list[str],
    client: Any | None = None,
) -> dict[str, Any]:

    refs = _reference_records()

    existing = _load_json(
        EVIDENCE_FILE,
        {},
    )

    existing_records = (
        existing.get("records")
        if isinstance(existing, dict)
        else {}
    )

    if not isinstance(
        existing_records,
        dict,
    ):
        existing_records = {}

    captured = []
    failed = []

    for raw_id in candidate_ids:
        entry_id = str(
            raw_id
            or ""
        ).strip()

        if not entry_id:
            continue

        if entry_id in existing_records:
            continue

        row = refs.get(
            entry_id
        )

        if not isinstance(row, dict):
            failed.append(
                [
                    entry_id,
                    "REFERENCE_NOT_FOUND",
                ]
            )
            continue

        try:
            record = capture_one(
                entry_id,
                row,
                client=client,
            )

            _store_record(
                entry_id,
                record,
            )

            captured.append(
                entry_id
            )

        except Exception as exc:
            failed.append(
                [
                    entry_id,
                    type(exc).__name__,
                ]
            )

    return {
        "ok":
            not failed,

        "captured_entry_ids":
            captured,

        "failed":
            failed,
    }


def _current_reference_ids() -> set[str]:
    return set(
        _reference_records().keys()
    )


def watch_loop() -> None:
    # Critical PIT rule:
    # Existing references at service startup are baseline only.
    # They are NEVER backfilled with current order-book data.
    seen = _current_reference_ids()

    libc = ctypes.CDLL(
        None,
        use_errno=True,
    )

    init = libc.inotify_init1
    init.argtypes = [
        ctypes.c_int
    ]
    init.restype = ctypes.c_int

    add = libc.inotify_add_watch
    add.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    add.restype = ctypes.c_int

    fd = init(0)

    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(
            err,
            os.strerror(err),
        )

    wd = add(
        fd,
        os.fsencode(
            str(STATE)
        ),
        WATCH_MASK,
    )

    if wd < 0:
        err = ctypes.get_errno()
        raise OSError(
            err,
            os.strerror(err),
        )

    print(
        "MICROSTRUCTURE_WATCHER_READY=1",
        flush=True,
    )

    while True:
        try:
            data = os.read(
                fd,
                65536,
            )

        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise

        offset = 0

        while (
            offset
            + EVENT_STRUCT.size
            <= len(data)
        ):
            (
                event_wd,
                mask,
                cookie,
                name_len,
            ) = EVENT_STRUCT.unpack_from(
                data,
                offset,
            )

            offset += (
                EVENT_STRUCT.size
            )

            raw_name = data[
                offset:
                offset + name_len
            ]

            offset += name_len

            name = raw_name.split(
                b"\0",
                1,
            )[0].decode(
                "utf-8",
                errors="ignore",
            )

            if mask & IN_Q_OVERFLOW:
                # No historical/current-market reconstruction.
                # Reset baseline and fail closed on missed PIT data.
                seen = (
                    _current_reference_ids()
                )
                continue

            if name != TARGET_BASENAME:
                continue

            current = (
                _current_reference_ids()
            )

            new_ids = sorted(
                current - seen
            )

            seen = current

            if not new_ids:
                continue

            result = capture_new(
                new_ids
            )

            print(
                json.dumps(
                    {
                        "schema":
                            SERVICE_SCHEMA,

                        "captured_entry_ids":
                            result.get(
                                "captured_entry_ids"
                            ),

                        "failed":
                            result.get(
                                "failed"
                            ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )


def main() -> None:
    watch_loop()


if __name__ == "__main__":
    main()