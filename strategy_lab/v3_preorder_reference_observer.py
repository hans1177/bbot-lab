#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
# filename:
# v3_preorder_reference_observer.py
#
# GREEN7A_PREORDER_REFERENCE_OBSERVER_V1
#
# Read-only observer for Frozen Runner pending-entry metadata.
#
# The authoritative source is:
#   state/runner_entry_pending_meta.json
#
# It preserves the first valid pending snapshot for each
# exact entry_id before the Frozen Runner later clears it.
#
# It never imports Frozen Runner and never modifies the
# authoritative pending source.
# ============================================================

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import hashlib
import json
import math
import os
import time


SCHEMA = (
    "GREEN7A_PREORDER_REFERENCE_OBSERVER_V1"
)

EVIDENCE_SCHEMA = (
    "GREEN7A_PREORDER_REFERENCE_EVIDENCE_V1"
)

IDEMPOTENT_BY_ENTRY_ID = True

RETENTION_POLICY = (
    "MAX_RECORDS_BY_CAPTURE_SEQUENCE"
)

MAX_RECORDS = int(
    os.getenv(
        "GREEN7A_PREORDER_REFERENCE_MAX_RECORDS",
        "5000",
    )
    or 5000
)

if MAX_RECORDS < 1:
    MAX_RECORDS = 5000


BASE = Path(
    os.getenv(
        "BOT_BASE_DIR",
        str(
            Path(__file__)
            .resolve()
            .parents[1]
        ),
    )
)

PENDING_FILE = (
    BASE
    / "state"
    / "runner_entry_pending_meta.json"
)

EVIDENCE_FILE = (
    BASE
    / "state"
    / "green7a_preorder_reference_evidence.json"
)


# ============================================================
# JSON helpers
# ============================================================

def _load_json(
    path: Path,
    default: Any,
) -> Any:

    try:
        return json.loads(
            Path(path).read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception:
        return deepcopy(
            default
        )


def _atomic_write_json(
    path: Path,
    obj: Any,
) -> None:

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_name(
        path.name
        + ".tmp."
        + str(
            os.getpid()
        )
        + "."
        + str(
            time.time_ns()
        )
    )

    try:

        tmp.write_text(
            json.dumps(
                obj,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )

        os.replace(
            tmp,
            path,
        )

    finally:

        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


# ============================================================
# Primitive validation
# ============================================================

def _text(
    value: Any,
) -> str:

    return str(
        value
        if value is not None
        else ""
    ).strip()


def _positive_price(
    value: Any,
) -> float | None:

    try:
        number = float(
            value
        )
    except Exception:
        return None

    if (
        not math.isfinite(number)
        or number <= 0.0
    ):
        return None

    return number


def _source_sha256(
    path: Path,
) -> str:

    try:
        return hashlib.sha256(
            Path(path).read_bytes()
        ).hexdigest()

    except Exception:
        return ""


# ============================================================
# Evidence document
# ============================================================

def _empty_evidence() -> dict[str, Any]:

    return {
        "schema":
            EVIDENCE_SCHEMA,

        "observer_schema":
            SCHEMA,

        "idempotency":
            "IDEMPOTENT_BY_ENTRY_ID",

        "retention": {
            "policy":
                RETENTION_POLICY,

            "max_records":
                int(MAX_RECORDS),
        },

        "capture_sequence":
            0,

        "records":
            {},
    }


def _normalize_evidence(
    raw: Any,
) -> dict[str, Any]:

    if not isinstance(
        raw,
        dict,
    ):
        return _empty_evidence()

    if raw.get(
        "schema"
    ) != EVIDENCE_SCHEMA:
        return _empty_evidence()

    records = raw.get(
        "records"
    )

    if not isinstance(
        records,
        dict,
    ):
        records = {}

    try:
        sequence = int(
            raw.get(
                "capture_sequence"
            )
            or 0
        )
    except Exception:
        sequence = 0

    if sequence < 0:
        sequence = 0

    return {
        "schema":
            EVIDENCE_SCHEMA,

        "observer_schema":
            SCHEMA,

        "idempotency":
            "IDEMPOTENT_BY_ENTRY_ID",

        "retention": {
            "policy":
                RETENTION_POLICY,

            "max_records":
                int(MAX_RECORDS),
        },

        "capture_sequence":
            sequence,

        "records":
            dict(records),
    }


# ============================================================
# Pending validation
# ============================================================

def _build_reference_record(
    pending_key: str,
    meta: Any,
    *,
    source_sha256: str,
    capture_sequence: int,
) -> tuple[
    dict[str, Any] | None,
    str,
]:

    if not isinstance(
        meta,
        dict,
    ):
        return (
            None,
            "PENDING_META_NOT_OBJECT",
        )

    entry_id = _text(
        meta.get(
            "entry_id"
        )
    )

    if not entry_id:
        return (
            None,
            "ENTRY_ID_MISSING",
        )

    strategy_version = _text(
        meta.get(
            "strategy_version"
        )
    )

    if not strategy_version:
        return (
            None,
            "STRATEGY_VERSION_MISSING",
        )

    reference_price = (
        _positive_price(
            meta.get(
                "entry_price"
            )
        )
    )

    if reference_price is None:
        return (
            None,
            "PREORDER_REFERENCE_PRICE_INVALID",
        )

    symbol = _text(
        meta.get(
            "symbol"
        )
    ).upper()

    side = _text(
        meta.get(
            "side"
        )
    ).upper()

    entry_tf = _text(
        meta.get(
            "entry_tf"
        )
    ).upper()

    entry_strategy = _text(
        meta.get(
            "entry_strategy"
        )
    ).upper()

    entry_ts = meta.get(
        "entry_ts"
    )

    signal_id = _text(
        meta.get(
            "signal_id"
        )
    )

    # Exact deep copy of the pending record as observed.
    pending_snapshot = deepcopy(
        meta
    )

    return (
        {
            "entry_id":
                entry_id,

            "strategy_version":
                strategy_version,

            "symbol":
                symbol,

            "side":
                side,

            "entry_tf":
                entry_tf,

            "entry_strategy":
                entry_strategy,

            "signal_id":
                signal_id,

            "entry_ts":
                entry_ts,

            "preorder_reference_price":
                float(
                    reference_price
                ),

            "reference_price_source":
                "RUNNER_PENDING_ENTRY_PRICE",

            "reference_timing":
                "AFTER_FINAL_ENTRY_RISK_BEFORE_MARKET_ORDER",

            "source_pending_key":
                _text(
                    pending_key
                ),

            "source_pending_file":
                "runner_entry_pending_meta.json",

            "source_file_sha256":
                _text(
                    source_sha256
                ),

            "capture_sequence":
                int(
                    capture_sequence
                ),

            "captured_at_ns":
                int(
                    time.time_ns()
                ),

            "pending_snapshot":
                pending_snapshot,
        },
        "",
    )


# ============================================================
# Immutable identity comparison
# ============================================================

def _reference_identity(
    record: Any,
) -> tuple[Any, ...]:

    row = (
        record
        if isinstance(
            record,
            dict,
        )
        else {}
    )

    return (
        _text(
            row.get(
                "entry_id"
            )
        ),
        _text(
            row.get(
                "strategy_version"
            )
        ),
        _text(
            row.get(
                "symbol"
            )
        ).upper(),
        _text(
            row.get(
                "side"
            )
        ).upper(),
        _text(
            row.get(
                "entry_tf"
            )
        ).upper(),
        _text(
            row.get(
                "entry_strategy"
            )
        ).upper(),
        _positive_price(
            row.get(
                "preorder_reference_price"
            )
        ),
    )


# ============================================================
# Retention
# ============================================================

def _apply_retention(
    evidence: dict[str, Any],
    max_records: int,
) -> list[str]:

    records = (
        evidence.get(
            "records"
        )
        or {}
    )

    if not isinstance(
        records,
        dict,
    ):
        evidence[
            "records"
        ] = {}

        return []

    try:
        limit = int(
            max_records
        )
    except Exception:
        limit = MAX_RECORDS

    if limit < 1:
        limit = MAX_RECORDS

    if len(records) <= limit:
        return []

    ordered = sorted(
        records.items(),
        key=lambda item: (
            int(
                (
                    item[1]
                    if isinstance(
                        item[1],
                        dict,
                    )
                    else {}
                ).get(
                    "capture_sequence"
                )
                or 0
            ),
            str(
                item[0]
            ),
        ),
    )

    remove_count = (
        len(records)
        - limit
    )

    removed = []

    for entry_id, _ in ordered[
        :remove_count
    ]:

        records.pop(
            entry_id,
            None,
        )

        removed.append(
            str(entry_id)
        )

    evidence[
        "records"
    ] = records

    return removed


# ============================================================
# Public capture API
# ============================================================

def capture_pending_snapshot(
    pending_path: Path | str = PENDING_FILE,
    evidence_path: Path | str = EVIDENCE_FILE,
    *,
    max_records: int = MAX_RECORDS,
) -> dict[str, Any]:

    pending_path = Path(
        pending_path
    )

    evidence_path = Path(
        evidence_path
    )

    if not pending_path.is_file():

        return {
            "schema":
                SCHEMA,

            "ok":
                True,

            "status":
                "PENDING_SOURCE_MISSING",

            "captured_count":
                0,

            "duplicate_count":
                0,

            "conflict_count":
                0,

            "rejected":
                [],

            "retention_removed":
                [],
        }

    # Authoritative pending source is read only.
    pending_raw = _load_json(
        pending_path,
        {},
    )

    if not isinstance(
        pending_raw,
        dict,
    ):

        return {
            "schema":
                SCHEMA,

            "ok":
                False,

            "status":
                "PENDING_SOURCE_INVALID",

            "captured_count":
                0,

            "duplicate_count":
                0,

            "conflict_count":
                0,

            "rejected":
                [{
                    "reason":
                        "PENDING_SOURCE_NOT_OBJECT"
                }],

            "retention_removed":
                [],
        }

    existing_raw = _load_json(
        evidence_path,
        _empty_evidence(),
    )

    evidence = _normalize_evidence(
        existing_raw
    )

    records = evidence[
        "records"
    ]

    source_sha = _source_sha256(
        pending_path
    )

    captured = []
    duplicates = []
    conflicts = []
    rejected = []

    changed = False

    for pending_key, meta in pending_raw.items():

        next_sequence = (
            int(
                evidence.get(
                    "capture_sequence"
                )
                or 0
            )
            + 1
        )

        record, reason = (
            _build_reference_record(
                str(
                    pending_key
                ),
                meta,
                source_sha256=
                    source_sha,
                capture_sequence=
                    next_sequence,
            )
        )

        if record is None:

            rejected.append(
                {
                    "pending_key":
                        str(
                            pending_key
                        ),

                    "reason":
                        reason,
                }
            )

            continue

        entry_id = record[
            "entry_id"
        ]

        prior = records.get(
            entry_id
        )

        if isinstance(
            prior,
            dict,
        ):

            if (
                _reference_identity(
                    prior
                )
                ==
                _reference_identity(
                    record
                )
            ):

                duplicates.append(
                    entry_id
                )

                continue

            conflicts.append(
                entry_id
            )

            # First durable capture wins.
            # Never rewrite a conflicting historical reference.
            continue

        evidence[
            "capture_sequence"
        ] = next_sequence

        records[
            entry_id
        ] = record

        captured.append(
            entry_id
        )

        changed = True

    evidence[
        "records"
    ] = records

    removed = _apply_retention(
        evidence,
        max_records,
    )

    if removed:
        changed = True

    if changed:

        _atomic_write_json(
            evidence_path,
            evidence,
        )

    return {
        "schema":
            SCHEMA,

        "ok":
            not conflicts,

        "status":
            (
                "ENTRY_ID_CONFLICT"
                if conflicts
                else
                (
                    "CAPTURED"
                    if captured
                    else
                    "NO_NEW_REFERENCE"
                )
            ),

        "captured_count":
            len(
                captured
            ),

        "captured_entry_ids":
            captured,

        "duplicate_count":
            len(
                duplicates
            ),

        "duplicate_entry_ids":
            duplicates,

        "conflict_count":
            len(
                conflicts
            ),

        "conflict_entry_ids":
            conflicts,

        "rejected":
            rejected,

        "retention_removed":
            removed,

        "evidence_file":
            str(
                evidence_path
            ),
    }


# ============================================================
# Public lookup API
# ============================================================

def lookup_preorder_reference(
    entry_id: Any,
    evidence_path: Path | str = EVIDENCE_FILE,
) -> dict[str, Any]:

    exact_entry_id = _text(
        entry_id
    )

    if not exact_entry_id:

        return {
            "ok":
                False,

            "reason":
                "ENTRY_ID_MISSING",

            "record":
                None,
        }

    raw = _load_json(
        Path(
            evidence_path
        ),
        {},
    )

    evidence = _normalize_evidence(
        raw
    )

    record = (
        evidence.get(
            "records",
            {}
        )
        .get(
            exact_entry_id
        )
    )

    if not isinstance(
        record,
        dict,
    ):

        return {
            "ok":
                False,

            "reason":
                "PREORDER_REFERENCE_NOT_FOUND",

            "entry_id":
                exact_entry_id,

            "record":
                None,
        }

    if (
        _text(
            record.get(
                "entry_id"
            )
        )
        != exact_entry_id
    ):

        return {
            "ok":
                False,

            "reason":
                "ENTRY_ID_BINDING_MISMATCH",

            "entry_id":
                exact_entry_id,

            "record":
                None,
        }

    strategy_version = _text(
        record.get(
            "strategy_version"
        )
    )

    if not strategy_version:

        return {
            "ok":
                False,

            "reason":
                "STRATEGY_VERSION_MISSING",

            "entry_id":
                exact_entry_id,

            "record":
                None,
        }

    reference_price = (
        _positive_price(
            record.get(
                "preorder_reference_price"
            )
        )
    )

    if reference_price is None:

        return {
            "ok":
                False,

            "reason":
                "PREORDER_REFERENCE_PRICE_INVALID",

            "entry_id":
                exact_entry_id,

            "record":
                None,
        }

    return {
        "ok":
            True,

        "reason":
            "OK",

        "entry_id":
            exact_entry_id,

        "strategy_version":
            strategy_version,

        "reference_price":
            float(
                reference_price
            ),

        "record":
            deepcopy(
                record
            ),
    }


# ============================================================
# Single-iteration CLI
# ============================================================

def main() -> int:

    result = (
        capture_pending_snapshot()
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    return (
        0
        if result.get(
            "ok"
        )
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
