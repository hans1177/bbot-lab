#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
# filename:
# v3_preorder_reference_watcher.py
#
# GREEN7A_PREORDER_RUNTIME_WATCHER_V1
#
# Linux directory-event watcher for the Frozen Runner's
# atomic pending metadata file.
#
# This process:
# - never imports runner.py
# - never modifies runner pending metadata
# - never calls an exchange API
# - never places/cancels/modifies an order
# ============================================================

from __future__ import annotations

from pathlib import Path
from typing import Any
import ctypes
import errno
import json
import os
import struct
import sys
import time

from v3_preorder_reference_observer import (
    capture_pending_snapshot,
)


SCHEMA = (
    "GREEN7A_PREORDER_RUNTIME_WATCHER_V1"
)

STARTUP_CAPTURE = True
QUEUE_OVERFLOW_RESCAN = True

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

WATCH_DIRECTORY = (
    BASE / "state"
)

TARGET_BASENAME = (
    "runner_entry_pending_meta.json"
)

PENDING_FILE = (
    WATCH_DIRECTORY
    / TARGET_BASENAME
)

EVIDENCE_FILE = (
    WATCH_DIRECTORY
    / "green7a_preorder_reference_evidence.json"
)


# ============================================================
# Linux inotify constants
# ============================================================

IN_CLOSE_WRITE = 0x00000008
IN_MOVED_TO    = 0x00000080
IN_CREATE      = 0x00000100

IN_Q_OVERFLOW  = 0x00004000
IN_IGNORED     = 0x00008000

WATCH_MASK = (
    IN_CLOSE_WRITE
    | IN_MOVED_TO
    | IN_CREATE
)

TARGET_EVENT_MASK = (
    IN_CLOSE_WRITE
    | IN_MOVED_TO
    | IN_CREATE
)

EVENT_STRUCT = struct.Struct(
    "iIII"
)

READ_SIZE = 65536


# ============================================================
# Helpers
# ============================================================

def _text(
    value: Any,
) -> str:

    return str(
        value
        if value is not None
        else ""
    ).strip()


def _result(
    *,
    ok: bool,
    status: str,
    **extra,
) -> dict[str, Any]:

    output = {
        "schema":
            SCHEMA,

        "ok":
            bool(ok),

        "status":
            str(status),
    }

    output.update(
        extra
    )

    return output


# ============================================================
# Inotify event parser
# ============================================================

def decode_inotify_events(
    payload: bytes,
) -> list[dict[str, Any]]:

    if not isinstance(
        payload,
        (bytes, bytearray),
    ):

        return []

    data = bytes(
        payload
    )

    offset = 0
    events = []

    header_size = (
        EVENT_STRUCT.size
    )

    while (
        offset + header_size
        <= len(data)
    ):

        (
            wd,
            mask,
            cookie,
            name_len,
        ) = EVENT_STRUCT.unpack_from(
            data,
            offset,
        )

        offset += (
            header_size
        )

        if name_len < 0:
            break

        end = (
            offset
            + int(name_len)
        )

        if end > len(data):
            break

        raw_name = (
            data[offset:end]
        )

        offset = end

        name = (
            raw_name
            .split(
                b"\x00",
                1,
            )[0]
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        events.append(
            {
                "wd":
                    int(wd),

                "mask":
                    int(mask),

                "cookie":
                    int(cookie),

                "name":
                    name,
            }
        )

    return events


# ============================================================
# Event classification
# ============================================================

def classify_event(
    event: Any,
) -> str:

    if not isinstance(
        event,
        dict,
    ):

        return "IGNORE"

    try:
        mask = int(
            event.get(
                "mask"
            )
            or 0
        )
    except Exception:
        mask = 0

    name = _text(
        event.get(
            "name"
        )
    )

    if (
        mask
        & IN_Q_OVERFLOW
    ):

        return (
            "QUEUE_OVERFLOW_RESCAN"
        )

    if (
        name
        == TARGET_BASENAME
        and (
            mask
            & TARGET_EVENT_MASK
        )
    ):

        return (
            "TARGET_PENDING_EVENT"
        )

    return "IGNORE"


# ============================================================
# Observer bridge
# ============================================================

def capture_authoritative_source(
    pending_path: Path | str = PENDING_FILE,
    evidence_path: Path | str = EVIDENCE_FILE,
) -> dict[str, Any]:

    return (
        capture_pending_snapshot(
            pending_path,
            evidence_path,
        )
    )


def startup_capture(
    pending_path: Path | str = PENDING_FILE,
    evidence_path: Path | str = EVIDENCE_FILE,
) -> dict[str, Any]:

    if not STARTUP_CAPTURE:

        return _result(
            ok=True,
            status=
                "STARTUP_CAPTURE_DISABLED",
        )

    result = (
        capture_authoritative_source(
            pending_path,
            evidence_path,
        )
    )

    return _result(
        ok=bool(
            result.get(
                "ok"
            )
        ),
        status=
            "STARTUP_CAPTURE",
        observer_result=
            result,
    )


def handle_event(
    event: Any,
    pending_path: Path | str = PENDING_FILE,
    evidence_path: Path | str = EVIDENCE_FILE,
) -> dict[str, Any]:

    action = classify_event(
        event
    )

    if (
        action
        == "QUEUE_OVERFLOW_RESCAN"
    ):

        # QUEUE_OVERFLOW_RESCAN
        if not QUEUE_OVERFLOW_RESCAN:

            return _result(
                ok=False,
                status=
                    "QUEUE_OVERFLOW_FAIL_CLOSED",
            )

        result = (
            capture_authoritative_source(
                pending_path,
                evidence_path,
            )
        )

        return _result(
            ok=bool(
                result.get(
                    "ok"
                )
            ),
            status=
                "QUEUE_OVERFLOW_RESCAN",
            observer_result=
                result,
        )

    if (
        action
        == "TARGET_PENDING_EVENT"
    ):

        result = (
            capture_authoritative_source(
                pending_path,
                evidence_path,
            )
        )

        return _result(
            ok=bool(
                result.get(
                    "ok"
                )
            ),
            status=
                "TARGET_PENDING_CAPTURE",
            observer_result=
                result,
        )

    return _result(
        ok=True,
        status=
            "IGNORED_EVENT",
    )


# ============================================================
# libc inotify
# ============================================================

def _load_libc_inotify():

    if not sys.platform.startswith(
        "linux"
    ):

        raise RuntimeError(
            "INOTIFY_UNAVAILABLE:"
            "FAIL_CLOSED_NO_FALSE_EVIDENCE"
        )

    libc = ctypes.CDLL(
        None,
        use_errno=True,
    )

    try:
        inotify_init1 = (
            libc.inotify_init1
        )

        inotify_add_watch = (
            libc.inotify_add_watch
        )

    except AttributeError as exc:

        raise RuntimeError(
            "INOTIFY_UNAVAILABLE:"
            "FAIL_CLOSED_NO_FALSE_EVIDENCE"
        ) from exc

    inotify_init1.argtypes = [
        ctypes.c_int,
    ]

    inotify_init1.restype = (
        ctypes.c_int
    )

    inotify_add_watch.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]

    inotify_add_watch.restype = (
        ctypes.c_int
    )

    return (
        libc,
        inotify_init1,
        inotify_add_watch,
    )


def open_directory_watch(
    watch_directory: Path | str = WATCH_DIRECTORY,
) -> tuple[int, int]:

    directory = Path(
        watch_directory
    )

    if not directory.is_dir():

        raise RuntimeError(
            "WATCH_DIRECTORY_MISSING:"
            + str(directory)
        )

    (
        _libc,
        inotify_init1,
        inotify_add_watch,
    ) = _load_libc_inotify()

    fd = int(
        inotify_init1(
            0
        )
    )

    if fd < 0:

        err = ctypes.get_errno()

        raise OSError(
            err,
            os.strerror(
                err
            ),
        )

    wd = int(
        inotify_add_watch(
            fd,
            os.fsencode(
                str(directory)
            ),
            WATCH_MASK,
        )
    )

    if wd < 0:

        err = ctypes.get_errno()

        try:
            os.close(fd)
        except Exception:
            pass

        raise OSError(
            err,
            os.strerror(
                err
            ),
        )

    return (
        fd,
        wd,
    )


# ============================================================
# Blocking runtime loop
# ============================================================

def watch_loop(
    *,
    watch_directory: Path | str = WATCH_DIRECTORY,
    pending_path: Path | str = PENDING_FILE,
    evidence_path: Path | str = EVIDENCE_FILE,
) -> int:

    # Capture once before event loop in case pending already
    # exists when this separate service starts/restarts.
    initial = startup_capture(
        pending_path,
        evidence_path,
    )

    print(
        json.dumps(
            initial,
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )

    fd = -1

    try:

        fd, _wd = (
            open_directory_watch(
                watch_directory
            )
        )

        while True:

            try:
                payload = os.read(
                    fd,
                    READ_SIZE,
                )

            except InterruptedError:
                continue

            except OSError as exc:

                if exc.errno == errno.EINTR:
                    continue

                raise

            if not payload:

                raise RuntimeError(
                    "INOTIFY_STREAM_CLOSED"
                )

            for event in (
                decode_inotify_events(
                    payload
                )
            ):

                outcome = (
                    handle_event(
                        event,
                        pending_path,
                        evidence_path,
                    )
                )

                if (
                    outcome.get(
                        "status"
                    )
                    != "IGNORED_EVENT"
                ):

                    print(
                        json.dumps(
                            outcome,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        flush=True,
                    )

    finally:

        if fd >= 0:

            try:
                os.close(fd)
            except Exception:
                pass


# ============================================================
# CLI
# ============================================================

def main() -> int:

    if not sys.platform.startswith(
        "linux"
    ):

        print(
            json.dumps(
                _result(
                    ok=False,
                    status=
                        "INOTIFY_UNAVAILABLE:"
                        "FAIL_CLOSED_NO_FALSE_EVIDENCE",
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        )

        return 2

    try:

        return watch_loop()

    except KeyboardInterrupt:

        return 0

    except Exception as exc:

        print(
            json.dumps(
                _result(
                    ok=False,
                    status=
                        "WATCHER_RUNTIME_FAIL",
                    error_type=
                        type(exc).__name__,
                    error=
                        str(exc),
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
