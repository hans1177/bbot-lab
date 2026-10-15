from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(
    os.environ.get(
        "BBOT_LOCAL_ROOT"
    )
    or r"C:\my\bottest"
)

PYTHON = (
    ROOT
    / ".venv"
    / "Scripts"
    / "python.exe"
)

PYTHONW = (
    ROOT
    / ".venv"
    / "Scripts"
    / "pythonw.exe"
)

COLLECTOR = (
    ROOT
    / "collector.py"
)

UNIVERSE = (
    ROOT
    / "strategy_lab"
    / "v3_collector_required_universe.py"
)

REQUIRED_UNIVERSE = (
    ROOT
    / "strategy_lab"
    / "results"
    / "v3"
    / "collector_required_universe.json"
)


def collector_processes():

    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.CommandLine -and "
        "$_.CommandLine -match 'collector\\.py' -and "
        "$_.CommandLine -notmatch "
        "'v3_candle_feed_supervisor\\.py' "
        "} | "
        "Select-Object "
        "ProcessId,ParentProcessId,CreationDate,CommandLine | "
        "ConvertTo-Json -Compress"
    )

    no_window = getattr(
        subprocess,
        "CREATE_NO_WINDOW",
        0,
    )

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=no_window,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "PROCESS_QUERY_FAIL"
        )

    raw = result.stdout.strip()

    if not raw:
        return []

    obj = json.loads(raw)

    if isinstance(obj, dict):
        return [obj]

    if isinstance(obj, list):
        return obj

    return []


def main():

    env = os.environ.copy()

    env[
        "BBOT_LOCAL_ROOT"
    ] = str(ROOT)

    # LOCAL_COLLECTOR_ENV_BINDING_V1
    env[
        "BOT_BASE_DIR"
    ] = str(ROOT)

    env[
        "BBOT_CANDLE_DATA_DIR"
    ] = str(
        ROOT
        / "data"
        / "candles"
    )

    resolver = subprocess.run(
        [
            str(PYTHONW),
            str(UNIVERSE),
        ],
        cwd=str(ROOT),
        env=env,
        timeout=60,
    
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),)

    if resolver.returncode != 0:
        raise RuntimeError(
            "REQUIRED_UNIVERSE_FAIL"
        )

    running = collector_processes()

    if running:
        collector_mtime = COLLECTOR.stat().st_mtime

        creation_times = []
        for item in running:
            raw = str(item.get("CreationDate") or "")
            try:
                if raw.startswith("/Date("):
                    epoch_ms = int(
                        raw[6:].split(")", 1)[0]
                    )
                    creation_times.append(
                        epoch_ms / 1000.0
                    )
            except Exception:
                pass

        if not creation_times:
            print("COLLECTOR=ALREADY_RUNNING")
            return

        oldest_start = min(creation_times)

        if collector_mtime <= oldest_start:
            print("COLLECTOR=ALREADY_RUNNING")
            return

        stale_pids = sorted({
            int(item["ProcessId"])
            for item in running
            if item.get("ProcessId") is not None
        })

        command = (
            "$ids=@("
            + ",".join(str(x) for x in stale_pids)
            + "); "
            + "foreach($id in $ids){"
            + "Stop-Process -Id $id -Force "
            + "-ErrorAction SilentlyContinue"
            + "}"
        )

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        if result.returncode != 0:
            raise RuntimeError(
                "STALE_COLLECTOR_STOP_FAIL"
            )

        time.sleep(2)

        if collector_processes():
            raise RuntimeError(
                "STALE_COLLECTOR_STILL_RUNNING"
            )

        print("COLLECTOR=STALE_RESTART")

    flags = getattr(
        subprocess,
        "CREATE_NO_WINDOW",
        0,
    )

    process = subprocess.Popen(
        [
            str(PYTHONW),
            str(COLLECTOR),
        ],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )

    time.sleep(2)

    if process.poll() is not None:
        raise RuntimeError(
            "COLLECTOR_START_FAIL"
        )

    print(
        "COLLECTOR=STARTED"
    )

    print(
        "ProcessId="
        + str(process.pid)
    )

    print(
        "REQUIRED_UNIVERSE="
        + str(REQUIRED_UNIVERSE)
    )


if __name__ == "__main__":
    main()
