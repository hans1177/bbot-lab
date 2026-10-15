#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import subprocess
from pathlib import Path


LAB = Path(
    r"C:\my\bottest\strategy_lab"
)

STRATEGIES = (
    LAB / "strategies"
)

RUNNER = (
    LAB / "run_strategy.py"
)

seen = {}


def signature(path):

    st = path.stat()

    return (
        st.st_mtime_ns,
        st.st_size
    )


print("=" * 72)
print(" STRATEGY LAB AUTO WATCHER")
print("=" * 72)
print("전략 파일 저장/수정 -> 자동 테스트")
print("folder =", STRATEGIES)
print("stop   = Ctrl+C")
print("=" * 72)


while True:

    try:

        for path in sorted(
            STRATEGIES.glob("*.py")
        ):

            if path.name.startswith("_"):
                continue

            try:
                sig = signature(path)
            except FileNotFoundError:
                continue

            key = str(path)

            if seen.get(key) == sig:
                continue

            seen[key] = sig

            # editor save 완료 대기
            time.sleep(0.5)

            print()
            print("=" * 72)
            print(
                "DETECTED:",
                path.name
            )
            print("=" * 72)

            subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    str(path)
                ],
                cwd=str(LAB),
                check=False
            )

        time.sleep(1)

    except KeyboardInterrupt:

        print()
        print("WATCHER STOPPED")
        break

    except Exception as e:

        print(
            "WATCHER_ERROR:",
            repr(e)
        )

        time.sleep(2)
