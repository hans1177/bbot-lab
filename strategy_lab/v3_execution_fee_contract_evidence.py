# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))

CONSTITUTION = ROOT / "ROADMAP_CONSTITUTION_V3.json"

OUT = (
    ROOT
    / "strategy_lab"
    / "results"
    / "v3"
    / "execution_fee_contract_evidence.json"
)

SSH_EXE = Path(
    os.getenv(
        "BBOT_SSH_EXE",
        r"C:\Windows\System32\OpenSSH\ssh.exe",
    )
)

SSH_KEY = Path(
    os.getenv(
        "BBOT_SSH_KEY",
        r"C:\my\han.pem",
    )
)

SSH_TARGET = os.getenv(
    "BBOT_SSH_TARGET",
    "ec2-user@13.159.128.120",
)

SCHEMA = "B_BOT_V3_EXECUTION_FEE_CONTRACT_EVIDENCE_V1"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(obj: Any) -> str:
    raw = json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

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

    os.replace(tmp, path)


def run_once() -> dict[str, Any]:
    constitution = json.loads(
        CONSTITUTION.read_text(
            encoding="utf-8-sig"
        )
    )

    contract = constitution.get(
        "validated_execution_cost_liquidity_policy_v1"
    )

    if not isinstance(contract, dict):
        raise RuntimeError(
            "COST_POLICY_CONSTITUTION_CONTRACT_MISSING"
        )

    fee_contract = (
        contract.get(
            "fee_validation_contract"
        )
        or {}
    )

    authoritative_source = (
        fee_contract.get(
            "authoritative_source"
        )
    )

    symbol_scope = (
        fee_contract.get(
            "symbol_scope"
        )
    )

    if (
        authoritative_source
        !=
        "AUTHENTICATED_EXCHANGE_ACCOUNT_COMMISSION_CONTRACT"
    ):
        raise RuntimeError(
            "FEE_AUTHORITY_CONTRACT_INVALID"
        )

    if (
        symbol_scope
        !=
        "CURRENT_ACTIVE_DYNAMIC_UNIVERSE"
    ):
        raise RuntimeError(
            "FEE_SYMBOL_SCOPE_CONTRACT_INVALID"
        )

    if not SSH_EXE.is_file():
        raise RuntimeError(
            "SSH_EXE_MISSING"
        )

    if not SSH_KEY.is_file():
        raise RuntimeError(
            "SSH_KEY_MISSING"
        )

    remote_code = r"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from binance.um_futures import UMFutures

base = Path("/home/ec2-user/bot")
cfg_path = base / "config.json"

raw = cfg_path.read_bytes()
cfg = json.loads(
    raw.decode("utf-8-sig")
)

symbols_raw = cfg.get("SYMBOLS")

if isinstance(symbols_raw, dict):
    symbols = sorted(
        str(x).upper().strip()
        for x in symbols_raw.keys()
        if str(x).strip()
    )

elif isinstance(symbols_raw, list):
    symbols = sorted(
        str(x).upper().strip()
        for x in symbols_raw
        if str(x).strip()
    )

else:
    raise RuntimeError(
        "ACTIVE_DYNAMIC_UNIVERSE_INVALID"
    )

if not symbols:
    raise RuntimeError(
        "ACTIVE_DYNAMIC_UNIVERSE_EMPTY"
    )

load_dotenv(
    "/etc/bbot/runner.env",
    override=False,
)

key = os.getenv(
    "BINANCE_API_KEY"
)

secret = os.getenv(
    "BINANCE_API_SECRET"
)

if not key or not secret:
    raise RuntimeError(
        "BINANCE_CREDENTIALS_MISSING"
    )

client = UMFutures(
    key=key,
    secret=secret,
)

maker = {}
taker = {}

for sym in symbols:
    row = client.commission_rate(sym)

    returned = str(
        row.get("symbol")
        or ""
    ).upper().strip()

    if returned and returned != sym:
        raise RuntimeError(
            "COMMISSION_SYMBOL_BINDING_MISMATCH="
            + sym
        )

    maker[sym] = (
        float(
            row["makerCommissionRate"]
        )
        * 10000.0
    )

    taker[sym] = (
        float(
            row["takerCommissionRate"]
        )
        * 10000.0
    )

out = {
    "retrieved_at":
        datetime.now(
            timezone.utc
        ).isoformat(),

    "universe_source":
        "/home/ec2-user/bot/config.json",

    "universe_config_sha256":
        hashlib.sha256(
            raw
        ).hexdigest(),

    "universe_count":
        len(symbols),

    "symbols":
        symbols,

    "maker_commission_bps_by_symbol":
        maker,

    "taker_commission_bps_by_symbol":
        taker,

    "api_endpoint":
        "GET /fapi/v1/commissionRate",

    "authenticated":
        True,

    "server_write":
        False,

    "order_write":
        False,

    "real_order":
        False,
}

print(
    json.dumps(
        out,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
)
"""

    creationflags = 0

    if os.name == "nt":
        creationflags = getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0,
        )

    proc = subprocess.run(
        [
            str(SSH_EXE),
            "-i",
            str(SSH_KEY),
            SSH_TARGET,
            (
                "env PYTHONDONTWRITEBYTECODE=1 "
                "/home/ec2-user/bot/venv/bin/python -"
            ),
        ],
        input=remote_code,
        text=True,
        capture_output=True,
        timeout=180,
        creationflags=creationflags,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            "REMOTE_FEE_CONTRACT_READ_FAILED:"
            + proc.stderr[-1000:]
        )

    remote = json.loads(
        proc.stdout.strip()
    )

    symbols = remote.get(
        "symbols"
    )

    maker = remote.get(
        "maker_commission_bps_by_symbol"
    )

    taker = remote.get(
        "taker_commission_bps_by_symbol"
    )

    if (
        not isinstance(symbols, list)
        or not symbols
        or not isinstance(maker, dict)
        or not isinstance(taker, dict)
    ):
        raise RuntimeError(
            "FEE_CONTRACT_PAYLOAD_INVALID"
        )

    if (
        set(symbols) != set(maker)
        or set(symbols) != set(taker)
    ):
        raise RuntimeError(
            "FEE_CONTRACT_UNIVERSE_BINDING_INVALID"
        )

    for sym in symbols:
        if (
            float(maker[sym]) < 0
            or float(taker[sym]) < 0
        ):
            raise RuntimeError(
                "NEGATIVE_COMMISSION_RATE="
                + sym
            )

    identity = {
        "authoritative_source":
            authoritative_source,

        "symbol_scope":
            symbol_scope,

        "source_retrieved_at":
            remote.get(
                "retrieved_at"
            ),

        "universe_config_sha256":
            remote.get(
                "universe_config_sha256"
            ),

        "symbols":
            symbols,

        "maker_commission_bps_by_symbol":
            maker,

        "taker_commission_bps_by_symbol":
            taker,
    }

    result = {
        "schema":
            SCHEMA,

        "generated_at":
            now(),

        "status":
            "PASS",

        "validated":
            True,

        "independent_validation":
            True,

        "policy_candidate_allowed":
            False,

        "live_effect":
            False,

        "authoritative_source":
            authoritative_source,

        "symbol_scope":
            symbol_scope,

        "source_endpoint":
            remote.get(
                "api_endpoint"
            ),

        "source_authenticated":
            remote.get(
                "authenticated"
            )
            is True,

        "source_retrieved_at":
            remote.get(
                "retrieved_at"
            ),

        "universe_source":
            remote.get(
                "universe_source"
            ),

        "universe_config_sha256":
            remote.get(
                "universe_config_sha256"
            ),

        "universe_count":
            len(symbols),

        "symbols":
            symbols,

        "maker_commission_bps_by_symbol":
            maker,

        "taker_commission_bps_by_symbol":
            taker,

        "evidence_identity_sha256":
            stable_hash(identity),

        "threshold_selected":
            False,

        "numeric_policy_created":
            False,

        "server_write":
            False,

        "order_write":
            False,

        "real_order":
            False,
    }

    atomic(
        OUT,
        result,
    )

    return result


if __name__ == "__main__":
    r = run_once()

    maker = sorted(
        {
            round(float(x), 12)
            for x in (
                r.get(
                    "maker_commission_bps_by_symbol"
                )
                or {}
            ).values()
        }
    )

    taker = sorted(
        {
            round(float(x), 12)
            for x in (
                r.get(
                    "taker_commission_bps_by_symbol"
                )
                or {}
            ).values()
        }
    )

    print(
        "STATUS="
        + str(r.get("status"))
    )

    print(
        "VALIDATED="
        + str(r.get("validated"))
    )

    print(
        "UNIVERSE_COUNT="
        + str(r.get("universe_count"))
    )

    print(
        "UNIQUE_MAKER_BPS="
        + repr(maker)
    )

    print(
        "UNIQUE_TAKER_BPS="
        + repr(taker)
    )

    print(
        "EVIDENCE_IDENTITY_SHA256="
        + str(
            r.get(
                "evidence_identity_sha256"
            )
        )
    )

    print(
        "THRESHOLD_SELECTED=False"
    )

    print(
        "NUMERIC_POLICY_CREATED=False"
    )

    print("SERVER_WRITE=0")
    print("ORDER_WRITE=0")
    print("REAL_ORDER=0")