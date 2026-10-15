# 파일명: standard_run_with_progress.py
# STANDARD 계산 로직은 수정하지 않고
# 실행 상태만 30초마다 표시하는 wrapper.

from __future__ import annotations

import os
import sys
import time
import queue
import threading
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "standard_runner.py"
RESULT_DIR = ROOT / "results"
RESULT_FILE = RESULT_DIR / "standard_latest.txt"

HEARTBEAT_SEC = 30


def fmt_elapsed(sec: float) -> str:
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def reader_thread(pipe, q):
    try:
        for line in iter(pipe.readline, ""):
            q.put(line)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def main():
    RESULT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not RUNNER.exists():
        raise SystemExit(
            f"standard_runner.py NOT_FOUND: {RUNNER}"
        )

    cmd = [
        sys.executable,
        "-u",
        str(RUNNER),
    ]

    print("=" * 70)
    print("STANDARD V1 RUN")
    print("progress heartbeat = 30 sec")
    print("runner =", RUNNER)
    print("result =", RESULT_FILE)
    print("=" * 70)

    start = time.time()

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    print(
        f"[STANDARD][START] "
        f"pid={proc.pid}"
    )

    q = queue.Queue()

    t = threading.Thread(
        target=reader_thread,
        args=(proc.stdout, q),
        daemon=True,
    )
    t.start()

    next_heartbeat = (
        time.time()
        + HEARTBEAT_SEC
    )

    last_output_time = time.time()
    output_lines = 0

    with RESULT_FILE.open(
        "w",
        encoding="utf-8",
        buffering=1,
    ) as out:

        while True:

            got_output = False

            while True:
                try:
                    line = q.get_nowait()
                except queue.Empty:
                    break

                got_output = True
                output_lines += 1
                last_output_time = time.time()

                print(
                    line,
                    end="",
                    flush=True,
                )

                out.write(line)
                out.flush()

            now = time.time()

            if now >= next_heartbeat:

                elapsed = fmt_elapsed(
                    now - start
                )

                quiet = int(
                    now
                    - last_output_time
                )

                size = (
                    RESULT_FILE.stat().st_size
                    if RESULT_FILE.exists()
                    else 0
                )

                hb = (
                    f"[STANDARD][RUNNING] "
                    f"elapsed={elapsed} "
                    f"pid={proc.pid} "
                    f"output_lines={output_lines} "
                    f"quiet={quiet}s "
                    f"log={size}B"
                )

                print(
                    hb,
                    flush=True,
                )

                # heartbeat도 결과파일에 남겨서
                # 나중에 실제 진행시간 확인 가능.
                out.write(
                    hb + "\n"
                )
                out.flush()

                next_heartbeat = (
                    now
                    + HEARTBEAT_SEC
                )

            rc = proc.poll()

            if (
                rc is not None
                and q.empty()
            ):
                break

            time.sleep(0.25)

        # 종료 직후 남은 stdout 배출
        while True:
            try:
                line = q.get_nowait()
            except queue.Empty:
                break

            print(
                line,
                end="",
                flush=True,
            )

            out.write(line)

        elapsed = fmt_elapsed(
            time.time() - start
        )

        end_msg = (
            f"[STANDARD][END] "
            f"exit_code={proc.returncode} "
            f"elapsed={elapsed}"
        )

        print(
            end_msg,
            flush=True,
        )

        out.write(
            end_msg + "\n"
        )

    if proc.returncode != 0:
        raise SystemExit(
            proc.returncode
        )


if __name__ == "__main__":
    main()
