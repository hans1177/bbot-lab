#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded, best-effort R4 signal-evaluation observations; never routes orders.

Only the R4 writer process creates this separate JSON snapshot. The GitHub
feedback publisher reads it without importing trading modules or writing DB.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

SCHEMA = "R4_EVALUATION_OBSERVATIONS_V1"
ALLOWED_STATUS = frozenset({
    "NO_SIGNAL", "SIGNAL_PRESENT", "SIGNAL_EVALUATION_ERROR",
    "NO_OPPOSITE", "OPPOSITE_SIGNAL", "OPPOSITE_EXIT_ATTEMPTED",
    "TRAIL_EXIT_ATTEMPTED", "MANAGEMENT_ERROR",
    "SKIPPED_UNSETTLED", "SKIPPED_PENDING_EXIT", "SKIPPED_PRICE",
    "BLOCKED_BASKET_MISMATCH",
})
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
SYMBOL = re.compile(r"^[A-Z0-9]{2,30}$")
FRAMES = frozenset({"30M", "1H", "6H", "12H", "1D", "1W"})
MAX_ROWS = 256
MAX_BYTES = 128 * 1024


class R4EvaluationObserver:
    """Never read this snapshot as authorization to trade or exit."""

    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}
        self.exits = {}
        self.dirty = False
        self.started = int(time.time())

    def note(self, kind, instance_id, frame, symbol, status, leg_id="", side=""):
        # Observability does not accept free-form errors, signals, or credentials.
        kind = str(kind)
        iid = str(instance_id)
        frame = str(frame).upper()
        symbol = str(symbol).upper()
        status = str(status)
        leg_id = str(leg_id)
        side = str(side).upper()
        if (kind not in {"ENTRY", "EXIT"} or status not in ALLOWED_STATUS
                or not IDENTIFIER.fullmatch(iid) or frame not in FRAMES
                or not SYMBOL.fullmatch(symbol)):
            return False
        if kind == "EXIT" and (not IDENTIFIER.fullmatch(leg_id)
                               or side not in {"LONG", "SHORT"}):
            return False
        row = {"instance_id": iid, "frame": frame, "symbol": symbol,
               "status": status, "evaluated_at_epoch": int(time.time())}
        if kind == "EXIT":
            row.update(leg_id=leg_id, side=side)
        bucket = self.entries if kind == "ENTRY" else self.exits
        bucket[iid + "|" + symbol if kind == "ENTRY" else leg_id] = row
        if len(bucket) > MAX_ROWS:
            oldest = min(bucket, key=lambda key: bucket[key]["evaluated_at_epoch"])
            del bucket[oldest]
        self.dirty = True
        return True

    def flush(self):
        """Atomically replace a small observation-only JSON file, not the DB."""
        if not self.dirty:
            return False
        path = self.path
        if path.is_symlink() or path.parent.is_symlink():
            raise RuntimeError("EVAL_OBSERVATION_SYMLINK")
        if not path.parent.is_dir():
            raise RuntimeError("EVAL_OBSERVATION_DIR_MISSING")
        if path.exists() and not path.is_file():
            raise RuntimeError("EVAL_OBSERVATION_PATH_UNSAFE")
        now = int(time.time())
        # Retain recent facts only, never turn an old evaluation into a success.
        cutoff = now - 7 * 86400
        for bucket in (self.entries, self.exits):
            for key in list(bucket):
                if bucket[key]["evaluated_at_epoch"] < cutoff:
                    del bucket[key]
        payload = {"schema": SCHEMA, "updated_at_epoch": now,
                   "process_started_at_epoch": self.started,
                   "entry_evaluations": sorted(self.entries.values(),
                                               key=lambda x: (x["instance_id"], x["symbol"])),
                   "exit_evaluations": sorted(self.exits.values(),
                                              key=lambda x: x["leg_id"])}
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True) + "\n").encode("ascii")
        if len(raw) > MAX_BYTES:
            raise RuntimeError("EVAL_OBSERVATION_OVERSIZE")
        temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, path)
            self.dirty = False
            return True
        finally:
            if temp.exists():
                temp.unlink()
