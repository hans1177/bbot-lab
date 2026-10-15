# 파일명: strategy_lab/v3_auto_runtime_release.py
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# V3_MINIMAL_SHADOW_RUNTIME_GATE_V1
import sys as _bbot_shadow_sys

_bbot_shadow_lab = str(
    Path(__file__).resolve().parent
)

if (
    _bbot_shadow_lab
    not in _bbot_shadow_sys.path
):
    _bbot_shadow_sys.path.insert(
        0,
        _bbot_shadow_lab,
    )

import v3_shadow_engine
import v3_deployment


try:
    import msvcrt
except ImportError:
    msvcrt = None


ROOT = Path(__file__).resolve().parents[1]

RESULTS = (
    ROOT
    / "strategy_lab"
    / "results"
    / "v3"
)

REQUEST = (
    RESULTS
    / "runtime_sync_request.json"
)

# HOLD_RECOVERY_VALIDATED_LAST_APPLIED_V1
LAST_APPLIED = (
    RESULTS
    / "runtime_sync_last_applied.json"
)

STATE = (
    RESULTS
    / "auto_runtime_release_state.json"
)

LOCK = (
    RESULTS
    / "auto_runtime_release.lock"
)

LOG = (
    ROOT
    / "logs"
    / "runner.log"
)

HOLD = (
    ROOT
    / "SERVER_PUSH_HOLD.flag"
)

ROADMAP_CANDIDATES = [
    ROOT / "ROADMAP_CONSTITUTION_V3.json",
    ROOT
    / "strategy_lab"
    / "ROADMAP_CONSTITUTION_V3.json",
]

SYNC_FROM = (
    ROOT
    / "sync_from_server.ps1"
)

RUNTIME_SYNC = (
    ROOT
    / "strategy_runtime_sync.ps1"
)

VERIFY_V3 = (
    ROOT
    / "verify_v3.py"
)

VERIFY_OVERFIT = (
    ROOT
    / "verify_overfit_validation.py"
)

VERIFY_LOGIC = (
    ROOT
    / "verify_logic_validation.py"
)

VERIFY_R4_SYNC = (
    ROOT
    / "verify_r4_sync.py"
)

VERIFY_POS = (
    ROOT
    / "verify_open_position_version_policy.py"
)

VERIFY_AUTO = (
    ROOT
    / "verify_autonomous_runtime_release.py"
)

ORDER = (
    ROOT
    / "order.py"
)

ORDER_HASH = (
    "513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c"
)

ALLOWED_TIERS = {
    "CANARY",
    "SMALL",
    "NORMAL",
    "ROLLBACK",
}

ALLOWED_KINDS = {
    "STRATEGY",
    "STRATEGY_INSTANCE_POLICY_BUNDLE",
}

FORBIDDEN_PATH_PARTS = (
    "order.py",
    "runner.py",
    "gate.py",
    "risk_engine.py",
    "strategy_lab/",
    "verify_",
)

_lock_handle = None
_log_dedupe = {}


def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def log(msg):

    # 반복되는 동일 이벤트/실패는 60초 동안 한 번만 기록한다.
    # 장기 실행 worker의 runner.log 폭증을 막되 서로 다른 메시지는 보존한다.
    key = str(msg)
    current = time.monotonic()
    last = _log_dedupe.get(key)

    if (
        last is not None
        and current - last < 60.0
    ):
        return

    _log_dedupe[key] = current

    # 오래된 dedupe 키를 주기적으로 제거해 메모리 증가를 제한한다.
    if len(_log_dedupe) > 2048:
        cutoff = current - 600.0
        stale = [
            item
            for item, stamp in _log_dedupe.items()
            if stamp < cutoff
        ]
        for item in stale:
            _log_dedupe.pop(item, None)

    LOG.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    text = (
        now()
        + " "
        + key
    )

    with LOG.open(
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            text
            + "\n"
        )


def atomic_json(path, obj):

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
        )
        + "\n",
        encoding="utf-8",
    )

    os.replace(
        tmp,
        path,
    )



HOLD_RECOVERY_REQUEST = (
    RESULTS
    / "hold_recovery_request.json"
)

VERIFY_MULTITF_LATE_ENTRY = (
    ROOT
    / "verify_multitf_late_entry_fallback_policy.py"
)

VERIFY_MULTITF_CANDIDATE_SELECTION = (
    ROOT
    / "verify_multitf_candidate_selection_policy.py"
)


def state(status, **extra):

    row = {
        "schema":
            "B_BOT_V3_AUTO_RUNTIME_RELEASE_STATE_V1",

        "updated_at":
            now(),

        "status":
            status,
    }

    row.update(extra)

    atomic_json(
        STATE,
        row,
    )


def sha256(path):

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def roadmap_policy():

    road = next(
        (
            p
            for p in ROADMAP_CANDIDATES
            if p.is_file()
        ),
        None,
    )

    if road is None:
        raise RuntimeError(
            "ROADMAP_NOT_FOUND"
        )

    obj = json.loads(
        road.read_text(
            encoding="utf-8-sig"
        )
    )

    p = (
        obj.get(
            "autonomous_runtime_release_policy"
        )
        or {}
    )

    if p.get(
        "policy_id"
    ) != (
        "AUTONOMOUS_RULE_GATED_RUNTIME_RELEASE_V1"
    ):
        raise RuntimeError(
            "AUTO_RELEASE_POLICY_ID_INVALID"
        )

    if p.get("enabled") is not True:
        raise RuntimeError(
            "AUTO_RELEASE_POLICY_DISABLED"
        )

    if p.get(
        "per_release_user_approval_required"
    ) is not False:
        raise RuntimeError(
            "PER_RELEASE_APPROVAL_STILL_REQUIRED"
        )

    return p


def acquire_lock():

    global _lock_handle

    LOCK.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    _lock_handle = open(
        LOCK,
        "a+b",
    )

    if msvcrt is None:
        return

    _lock_handle.seek(0)

    if _lock_handle.tell() == 0:
        _lock_handle.write(b"0")
        _lock_handle.flush()

    _lock_handle.seek(0)

    try:
        msvcrt.locking(
            _lock_handle.fileno(),
            msvcrt.LK_NBLCK,
            1,
        )
    except OSError:
        raise RuntimeError(
            "AUTO_RELEASE_ALREADY_RUNNING"
        )


def run(cmd, timeout):
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"

    p = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
        capture_output=True,
        timeout=timeout,
    )

    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out

def run_python(path, timeout=900):

    return run(
        [
            str(
                ROOT
                / ".venv"
                / "Scripts"
                / "pythonw.exe"
            ),
            str(path),
        ],
        timeout,
    )


def run_ps(path, timeout=900):

    # WINDOWS_NO_CONSOLE_CHILD_PROCESS_V1
    #
    # Worker parent is pythonw.exe.
    # CREATE_NO_WINDOW prevents powershell.exe from creating a
    # visible console window. STARTUPINFO/SW_HIDE is a second layer.

    import os
    import subprocess

    creationflags = 0
    startupinfo = None

    if os.name == "nt":

        creationflags = getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0x08000000,
        )

        startupinfo = subprocess.STARTUPINFO()

        startupinfo.dwFlags |= (
            subprocess.STARTF_USESHOWWINDOW
        )

        startupinfo.wShowWindow = 0

    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )

    return (
        int(proc.returncode),
        proc.stdout or "",
    )




def validate_request(req):

    if not isinstance(req, dict):
        raise RuntimeError(
            "RUNTIME_REQUEST_NOT_OBJECT"
        )

    if req.get("schema") != (
        "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1"
    ):
        raise RuntimeError(
            "RUNTIME_REQUEST_SCHEMA_INVALID"
        )

    if req.get(
        "autonomous_rule_passed"
    ) is not True:
        raise RuntimeError(
            "AUTONOMOUS_RULE_NOT_PASSED"
        )

    if req.get(
        "core_mutation"
    ) is not False:
        raise RuntimeError(
            "CORE_MUTATION_NOT_FALSE"
        )

    tier = str(
        req.get("tier")
        or ""
    ).upper()

    if tier not in ALLOWED_TIERS:
        raise RuntimeError(
            "RUNTIME_TIER_NOT_ALLOWED="
            + tier
        )

    kind = str(
        req.get("version_kind")
        or ""
    ).upper()

    if kind not in ALLOWED_KINDS:
        raise RuntimeError(
            "VERSION_KIND_INVALID="
            + kind
        )

    # R4_LOCAL_RELEASE_BOUNDARY_V1
    # 새 Strategy Instance의 최초 서버 반영은 LOCAL_SHADOW를 통과한
    # exact CANARY만 허용한다. CANARY 이후 Tier writer는 서버 단일 writer다.
    if tier != "CANARY":
        raise RuntimeError(
            "LOCAL_RELEASE_TIER_MUST_BE_CANARY="
            + tier
        )

    if req.get("approved_for_server_release") is not True:
        raise RuntimeError(
            "APPROVED_FOR_SERVER_RELEASE_REQUIRED"
        )

    if str(
        req.get("local_qualification_tier")
        or ""
    ).upper() != "CANARY":
        raise RuntimeError(
            "LOCAL_QUALIFICATION_CANARY_REQUIRED"
        )

    if str(req.get("shadow_gate") or "").upper() != "PASS":
        raise RuntimeError(
            "LOCAL_SHADOW_PASS_REQUIRED"
        )

    if kind == "STRATEGY":
        instance_ids = [
            str(req.get("strategy_instance_id") or "").strip()
        ]
    else:
        targets = req.get("strategy_instances") or []
        if not isinstance(targets, list) or not targets:
            raise RuntimeError(
                "STRATEGY_INSTANCE_POLICY_TARGETS_REQUIRED"
            )
        instance_ids = []
        for target in targets:
            if not isinstance(target, dict):
                raise RuntimeError(
                    "STRATEGY_INSTANCE_POLICY_TARGET_INVALID"
                )
            if target.get("approved_for_server_release") is not True:
                raise RuntimeError(
                    "STRATEGY_INSTANCE_POLICY_TARGET_NOT_APPROVED"
                )
            instance_ids.append(
                str(target.get("strategy_instance_id") or "").strip()
            )

    if (
        not all(instance_ids)
        or len(instance_ids) != len(set(instance_ids))
    ):
        raise RuntimeError(
            "EXACT_STRATEGY_INSTANCE_ID_REQUIRED"
        )

    rid = str(
        req.get("request_id")
        or ""
    )

    if not rid:
        raise RuntimeError(
            "REQUEST_ID_MISSING"
        )

    cfg = str(
        req.get(
            "strategy_config_file"
        )
        or ""
    ).replace("\\", "/").lstrip("./")

    # SAFE_PENDING_CONFIG_SOURCE_V1
    #
    # Autonomous deployment deliberately generates immutable pending
    # strategy-config snapshots under strategy_lab/results/v3.
    # Accept only the root config or a direct generated pending JSON
    # in that exact directory. Arbitrary paths remain forbidden.
    pending_prefix = (
        "strategy_lab/results/v3/"
        "strategy_config_pending_"
    )

    cfg_is_root = (
        cfg == "strategy_config.json"
    )

    pending_name = (
        cfg[len(pending_prefix):]
        if cfg.startswith(pending_prefix)
        else ""
    )

    cfg_is_pending = (
        bool(pending_name)
        and cfg.startswith(pending_prefix)
        and cfg.endswith(".json")
        and "/" not in pending_name
        and "\\" not in pending_name
        and ".." not in pending_name
    )

    if not (
        cfg_is_root
        or cfg_is_pending
    ):
        raise RuntimeError(
            "STRATEGY_CONFIG_FILE_INVALID="
            + cfg
        )

    cfg_path = ROOT / cfg

    if not cfg_path.is_file():
        raise RuntimeError(
            "STRATEGY_CONFIG_FILE_MISSING="
            + cfg
        )

    if cfg_is_pending:
        try:
            pending_cfg = json.loads(
                cfg_path.read_text(
                    encoding="utf-8-sig"
                )
            )
        except Exception as error:
            raise RuntimeError(
                "PENDING_CONFIG_JSON_INVALID="
                + type(error).__name__
            )

        if not isinstance(
            pending_cfg,
            dict,
        ):
            raise RuntimeError(
                "PENDING_CONFIG_NOT_OBJECT"
            )

        if (
            str(
                pending_cfg.get("schema")
                or ""
            )
            != "STRATEGY_CONFIG_V3"
        ):
            raise RuntimeError(
                "PENDING_CONFIG_SCHEMA_INVALID"
            )

        req_constitution = str(
            req.get(
                "constitution_version"
            )
            or ""
        )

        cfg_constitution = str(
            pending_cfg.get(
                "roadmap_constitution"
            )
            or ""
        )

        if (
            req_constitution
            and cfg_constitution
            != req_constitution
        ):
            raise RuntimeError(
                "PENDING_CONFIG_CONSTITUTION_MISMATCH"
            )

        # R4_PENDING_INSTANCE_BINDING_V1
        pending_instances = (
            ((pending_cfg.get("r4_runtime") or {}).get("strategy_instances") or {})
        )
        if not isinstance(pending_instances, dict):
            raise RuntimeError(
                "PENDING_R4_STRATEGY_INSTANCES_INVALID"
            )
        for instance_id in instance_ids:
            instance_row = pending_instances.get(instance_id)
            if not isinstance(instance_row, dict):
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_INSTANCE_MISSING="
                    + instance_id
                )
            if instance_row.get("approved_for_server_release") is not True:
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_INSTANCE_NOT_APPROVED="
                    + instance_id
                )
            if str(instance_row.get("tier") or "").upper() != "CANARY":
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_INSTANCE_NOT_CANARY="
                    + instance_id
                )
            required_identity = (
                instance_row.get("strategy_id"),
                instance_row.get("timeframe"),
                instance_row.get("exact_strategy_version"),
                instance_row.get("strategy_policy_bundle_version"),
                instance_row.get("policy_snapshot"),
            )
            if not all(required_identity):
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_INSTANCE_IDENTITY_INCOMPLETE="
                    + instance_id
                )

        if kind == "STRATEGY_INSTANCE_POLICY_BUNDLE":
            track = str(req.get("policy_track") or "").upper()
            policy_version = str(req.get("policy_version") or "")
            if not track or not policy_version:
                raise RuntimeError(
                    "STRATEGY_INSTANCE_POLICY_BINDING_MISSING"
                )
            if str(
                ((pending_cfg.get("policy_versions") or {}).get(track))
                or ""
            ) != policy_version:
                raise RuntimeError(
                    "PENDING_POLICY_VERSION_BINDING_MISMATCH"
                )

        kind = str(
            req.get(
                "version_kind"
            )
            or "STRATEGY"
        ).upper()

        if kind == "STRATEGY":
            # MULTI_CANARY_SI_SOURCE_OF_TRUTH_V1
            #
            # frames[TF] is a legacy/representative index.
            # Exact runtime ownership is r4_runtime.strategy_instances[SI].
            tf = str(
                req.get("timeframe")
                or ""
            ).upper()

            instance_id = str(
                req.get("strategy_instance_id")
                or ""
            ).strip()

            instance_row = (
                pending_instances.get(
                    instance_id
                )
                or {}
            )

            if not isinstance(
                instance_row,
                dict,
            ):
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_INSTANCE_MISSING="
                    + instance_id
                )

            if str(
                instance_row.get(
                    "exact_strategy_version"
                )
                or ""
            ) != str(
                req.get(
                    "strategy_version"
                )
                or ""
            ):
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_VERSION_MISMATCH"
                )

            if str(
                instance_row.get(
                    "strategy_id"
                )
                or ""
            ).upper() != str(
                req.get(
                    "strategy"
                )
                or ""
            ).upper():
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_ID_MISMATCH"
                )

            if str(
                instance_row.get(
                    "timeframe"
                )
                or ""
            ).upper() != tf:
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_TIMEFRAME_MISMATCH"
                )

            if str(
                instance_row.get(
                    "tier"
                )
                or ""
            ).upper() != tier:
                raise RuntimeError(
                    "PENDING_R4_STRATEGY_TIER_MISMATCH"
                )

            # Frame remains required only as timeframe index.
            frame = (
                (
                    pending_cfg.get(
                        "frames"
                    )
                    or {}
                ).get(tf)
                or {}
            )

            if not frame:
                raise RuntimeError(
                    "PENDING_CONFIG_FRAME_MISSING="
                    + tf
                )

    strategy_file = str(
        req.get(
            "strategy_file"
        )
        or ""
    ).replace("\\", "/").lstrip("./")

    if strategy_file:

        # SAFE_VALIDATED_GENERATED_STRATEGY_SOURCE_V1
        #
        # strategy_file is a LOCAL validated-source reference.
        # strategy_runtime_sync.ps1 never publishes this research path.
        # It compiles the source, resolves the runtime filename from
        # strategy_config.strategy_registry, and stages only the copied
        # artifact under assist/strategies.
        #
        # Therefore a generated research source may be accepted only when
        # it is the exact source bound to this validated Strategy Version.

        first_part = (
            strategy_file.split("/", 1)[0]
            if strategy_file
            else ""
        )

        parts = strategy_file.split("/")

        if (
            strategy_file.startswith("/")
            or ":" in first_part
            or any(
                part in ("", ".", "..")
                for part in parts
            )
        ):
            raise RuntimeError(
                "STRATEGY_SOURCE_PATH_INVALID="
                + strategy_file
            )

        generated_prefixes = (
            "strategy_lab/results/v3/"
            "generated_combinations/",
            "strategy_lab/results/v3/"
            "generated_safe_grammar/",
            "strategy_lab/deploy_ready/",
        )

        generated_source = False

        for prefix in generated_prefixes:

            if not strategy_file.startswith(
                prefix
            ):
                continue

            tail = strategy_file[
                len(prefix):
            ]

            generated_source = (
                bool(tail)
                and "/" not in tail
                and "\\" not in tail
                and tail.lower().endswith(
                    ".py"
                )
            )

            if generated_source:
                break

        if generated_source:

            if kind != "STRATEGY":
                raise RuntimeError(
                    "FORBIDDEN_RUNTIME_PAYLOAD="
                    + strategy_file
                )

            source_path = (
                ROOT
                / strategy_file
            )

            if not source_path.is_file():
                raise RuntimeError(
                    "STRATEGY_SOURCE_FILE_MISSING="
                    + strategy_file
                )

            registry_path = (
                RESULTS
                / "version_registry.json"
            )

            if not registry_path.is_file():
                raise RuntimeError(
                    "VERSION_REGISTRY_MISSING"
                )

            try:
                registry = json.loads(
                    registry_path.read_text(
                        encoding="utf-8-sig"
                    )
                )
            except Exception as error:
                raise RuntimeError(
                    "VERSION_REGISTRY_INVALID="
                    + type(error).__name__
                )

            version_id = str(
                req.get(
                    "strategy_version"
                )
                or ""
            )

            version_row = (
                (
                    registry.get(
                        "strategy_versions"
                    )
                    or {}
                ).get(version_id)
                or {}
            )

            if not version_row:
                raise RuntimeError(
                    "STRATEGY_SOURCE_VERSION_NOT_REGISTERED="
                    + version_id
                )

            if str(
                version_row.get(
                    "kind"
                )
                or ""
            ).upper() != "STRATEGY":
                raise RuntimeError(
                    "STRATEGY_SOURCE_VERSION_KIND_INVALID"
                )

            if str(
                version_row.get(
                    "status"
                )
                or ""
            ).upper() not in (
                "VALIDATED",
                "CANARY",
                "SMALL",
                "NORMAL",
            ):
                raise RuntimeError(
                    "STRATEGY_SOURCE_VERSION_STATUS_INVALID"
                )

            if (
                version_row.get(
                    "core_mutation"
                )
                is not False
            ):
                raise RuntimeError(
                    "STRATEGY_SOURCE_CORE_MUTATION_FORBIDDEN"
                )

            payload = (
                version_row.get(
                    "payload"
                )
                or {}
            )

            registered_source = str(
                payload.get(
                    "source_file"
                )
                or ""
            ).replace(
                "\\",
                "/",
            ).lstrip("./")

            if (
                registered_source
                != strategy_file
            ):
                raise RuntimeError(
                    "STRATEGY_SOURCE_VERSION_REGISTRY_MISMATCH"
                )

            if str(
                payload.get(
                    "strategy"
                )
                or ""
            ).upper() != str(
                req.get(
                    "strategy"
                )
                or ""
            ).upper():
                raise RuntimeError(
                    "STRATEGY_SOURCE_STRATEGY_MISMATCH"
                )

            if str(
                payload.get(
                    "timeframe"
                )
                or ""
            ).upper() != str(
                req.get(
                    "timeframe"
                )
                or ""
            ).upper():
                raise RuntimeError(
                    "STRATEGY_SOURCE_TIMEFRAME_MISMATCH"
                )

        else:

            low = (
                strategy_file
                .lower()
            )

            for bad in FORBIDDEN_PATH_PARTS:

                if bad in low:
                    raise RuntimeError(
                        "FORBIDDEN_RUNTIME_PAYLOAD="
                        + strategy_file
                    )

    if kind == "STRATEGY":

        version = str(
            req.get(
                "strategy_version"
            )
            or ""
        )

        if tier != "ROLLBACK" and not version:
            raise RuntimeError(
                "STRATEGY_VERSION_MISSING"
            )

    if tier == "ROLLBACK":

        if not str(
            req.get(
                "rollback_target"
            )
            or ""
        ):
            raise RuntimeError(
                "ROLLBACK_TARGET_MISSING"
            )

    return rid


def verify_local(logic_verify_mode="RELEASE_CANDIDATE"):

    if sha256(ORDER) != ORDER_HASH:
        raise RuntimeError(
            "ORDER_FROZEN_HASH_FAIL"
        )

    # R4_RELEASE_VALIDATION_GATES_V1
    # 서버 Runtime 동기화 전에 헌법이 요구하는 OVERFIT / LOGIC /
    # R4 동기화 계약을 실제 검증기로 실행한다. 모든 검증은
    # RUNNER_DRY_RUN=1 상태의 읽기 전용 검증이며 실패 시 즉시 차단한다.
    verifiers = (
        (VERIFY_V3, "VERIFY_V3", "POST_MIGRATION_RUNTIME", None),
        (VERIFY_OVERFIT, "OVERFIT_VALIDATION", None, None),
        (VERIFY_LOGIC, "LOGIC_VALIDATION", None, logic_verify_mode),
        (VERIFY_R4_SYNC, "R4_SYNC_VALIDATION", None, None),
        (VERIFY_POS, "OPEN_POSITION_VERSION_POLICY", None, None),
        (VERIFY_AUTO, "AUTONOMOUS_RELEASE_VERIFY", None, None),
    )

    for path, label, verify_mode, logic_mode in verifiers:

        if not path.is_file():
            raise RuntimeError(
                "LOCAL_VERIFIER_MISSING="
                + str(path)
            )

        old_verify_mode = os.environ.get(
            "B_BOT_V3_VERIFY_MODE"
        )
        had_verify_mode = (
            "B_BOT_V3_VERIFY_MODE"
            in os.environ
        )
        old_logic_mode = os.environ.get(
            "B_BOT_V3_LOGIC_VERIFY_MODE"
        )
        had_logic_mode = (
            "B_BOT_V3_LOGIC_VERIFY_MODE"
            in os.environ
        )
        old_dry_run = os.environ.get(
            "RUNNER_DRY_RUN"
        )
        had_dry_run = (
            "RUNNER_DRY_RUN"
            in os.environ
        )

        try:
            os.environ["RUNNER_DRY_RUN"] = "1"

            if verify_mode:
                os.environ[
                    "B_BOT_V3_VERIFY_MODE"
                ] = verify_mode
            else:
                os.environ.pop(
                    "B_BOT_V3_VERIFY_MODE",
                    None,
                )

            if logic_mode:
                os.environ[
                    "B_BOT_V3_LOGIC_VERIFY_MODE"
                ] = logic_mode
            else:
                os.environ.pop(
                    "B_BOT_V3_LOGIC_VERIFY_MODE",
                    None,
                )

            rc, out = run_python(
                path,
                timeout=1200,
            )

        finally:
            if had_verify_mode:
                os.environ[
                    "B_BOT_V3_VERIFY_MODE"
                ] = old_verify_mode
            else:
                os.environ.pop(
                    "B_BOT_V3_VERIFY_MODE",
                    None,
                )

            if had_logic_mode:
                os.environ[
                    "B_BOT_V3_LOGIC_VERIFY_MODE"
                ] = old_logic_mode
            else:
                os.environ.pop(
                    "B_BOT_V3_LOGIC_VERIFY_MODE",
                    None,
                )

            if had_dry_run:
                os.environ[
                    "RUNNER_DRY_RUN"
                ] = old_dry_run
            else:
                os.environ.pop(
                    "RUNNER_DRY_RUN",
                    None,
                )

        log(
            label
            + "_RC="
            + str(rc)
        )

        if rc != 0:
            log(out[-12000:])
            raise RuntimeError(
                label
                + "_FAIL"
            )


def pull(label):

    rc, out = run_ps(
        SYNC_FROM,
        timeout=900,
    )

    log(
        label
        + "_RC="
        + str(rc)
    )

    if rc != 0:
        log(out[-12000:])
        raise RuntimeError(
            label
            + "_FAIL"
        )

    if (
        "SERVER_TO_LOCAL_READ_ONLY_SYNC=PASS"
        not in out
    ):
        log(out[-12000:])
        raise RuntimeError(
            label
            + "_PASS_MARKER_MISSING"
        )

    return out


def apply_runtime_sync():

    rc, out = run_ps(
        RUNTIME_SYNC,
        timeout=1200,
    )

    log(
        "RUNTIME_SYNC_RC="
        + str(rc)
    )

    log(
        out[-12000:]
    )

    if rc != 0:
        raise RuntimeError(
            "STRATEGY_RUNTIME_SYNC_FAIL"
        )

    return out


def read_request():

    raw1 = REQUEST.read_bytes()

    time.sleep(0.15)

    raw2 = REQUEST.read_bytes()

    if raw1 != raw2:
        raise RuntimeError(
            "RUNTIME_REQUEST_NOT_STABLE"
        )

    return json.loads(
        raw2.decode(
            "utf-8-sig"
        )
    )


def hard_stop(exc, request_id=""):

    HOLD.touch(
        exist_ok=True
    )

    state(
        "HARD_STOP",
        reason=repr(exc),
        request_id=request_id,
        emergency_hold=True,
    )

    log(
        "HARD_STOP "
        + repr(exc)
    )



def verify_hold_recovery_local(logic_verify_mode="HOLD_RECOVERY"):

    # Existing complete local release verification remains mandatory.
    verify_local(logic_verify_mode=logic_verify_mode)

    for path, label in (
        (
            VERIFY_MULTITF_LATE_ENTRY,
            "MULTITF_LATE_ENTRY_FALLBACK",
        ),
        (
            VERIFY_MULTITF_CANDIDATE_SELECTION,
            "MULTITF_CANDIDATE_SELECTION",
        ),
    ):

        if not path.is_file():
            raise RuntimeError(
                "HOLD_RECOVERY_REQUIRED_VERIFIER_MISSING="
                + str(path)
            )

        rc, out = run_python(
            path,
            timeout=1200,
        )

        log(
            "HOLD_RECOVERY_"
            + label
            + "_RC="
            + str(rc)
        )

        log(
            out[-12000:]
        )

        if rc != 0:
            raise RuntimeError(
                "HOLD_RECOVERY_"
                + label
                + "_FAIL"
            )



# HOLD_RECOVERY_AUTHORIZATION_API_V1
def authorize_hold_recovery_current_request():

    if not HOLD.exists():
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_REQUIRES_HOLD"
        )

    if HOLD_RECOVERY_REQUEST.exists():
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_MARKER_ALREADY_EXISTS"
        )

    request_source = (
        REQUEST
        if REQUEST.is_file()
        else LAST_APPLIED
    )

    if not request_source.is_file():
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_REQUEST_SOURCE_MISSING"
        )

    if not STATE.is_file():
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_STATE_MISSING"
        )

    try:
        current_state = json.loads(
            STATE.read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception as error:
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_STATE_INVALID="
            + type(error).__name__
        )

    if not isinstance(
        current_state,
        dict,
    ):
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_STATE_NOT_OBJECT"
        )

    if current_state.get(
        "schema"
    ) != (
        "B_BOT_V3_AUTO_RUNTIME_RELEASE_STATE_V1"
    ):
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_STATE_SCHEMA_INVALID"
        )

    if current_state.get(
        "status"
    ) != "WAIT_EMERGENCY_HOLD":
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_STATE_NOT_WAIT"
        )

    if current_state.get(
        "emergency_hold"
    ) is not True:
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_EMERGENCY_HOLD_FALSE"
        )

    if request_source == REQUEST:
        request_bytes_before = (
            REQUEST.read_bytes()
        )

        req = read_request()

    else:
        raw1 = request_source.read_bytes()

        time.sleep(0.15)

        raw2 = request_source.read_bytes()

        if raw1 != raw2:
            raise RuntimeError(
                "HOLD_RECOVERY_AUTH_LAST_APPLIED_NOT_STABLE"
            )

        request_bytes_before = raw2

        req = json.loads(
            raw2.decode(
                "utf-8-sig"
            )
        )

    request_id = validate_request(
        req
    )

    if not request_id:
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_REQUEST_ID_MISSING"
        )

    if (
        not request_source.is_file()
        or
        request_source.read_bytes()
        != request_bytes_before
    ):
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_REQUEST_SOURCE_CHANGED"
        )

    state_request_id = str(
        current_state.get(
            "request_id"
        )
        or ""
    )

    if (
        state_request_id
        and
        state_request_id
        != request_id
    ):
        raise RuntimeError(
            "HOLD_RECOVERY_AUTH_STATE_REQUEST_ID_MISMATCH"
        )

    repaired_state = dict(
        current_state
    )

    repaired_state[
        "request_id"
    ] = request_id

    repaired_state[
        "updated_at"
    ] = now()

    repaired_state[
        "recovery_authorized"
    ] = True

    repaired_state[
        "recovery_binding_repaired"
    ] = (
        not bool(
            state_request_id
        )
    )

    atomic_json(
        STATE,
        repaired_state,
    )

    atomic_json(
        HOLD_RECOVERY_REQUEST,
        {
            "schema":
                "B_BOT_V3_HOLD_RECOVERY_REQUEST_V1",

            "request_id":
                request_id,

            "authorized_at":
                now(),

            "authorization":
                (
                    "CURRENT_VALIDATED_RUNTIME_REQUEST"
                    if request_source == REQUEST
                    else "VALIDATED_LAST_APPLIED_REQUEST"
                ),

            "request_source":
                (
                    "ACTIVE_REQUEST"
                    if request_source == REQUEST
                    else "LAST_APPLIED"
                ),
        },
    )

    return {
        "request_id":
            request_id,

        "state_binding_repaired":
            not bool(
                state_request_id
            ),

        "hold_cleared":
            False,

        "release_applied":
            False,
    }




def auto_authorize_request_bound_hold_if_safe():

    if not HOLD.exists():
        return False

    if HOLD_RECOVERY_REQUEST.exists():
        return False

    if (
        not REQUEST.is_file()
        and not LAST_APPLIED.is_file()
    ):
        return False

    if not STATE.is_file():
        return False

    try:
        current_state = json.loads(
            STATE.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception:
        return False

    if not isinstance(current_state, dict):
        return False

    if current_state.get("schema") != (
        "B_BOT_V3_AUTO_RUNTIME_RELEASE_STATE_V1"
    ):
        return False

    if current_state.get("status") != (
        "WAIT_EMERGENCY_HOLD"
    ):
        return False

    if current_state.get("emergency_hold") is not True:
        return False

    # Frozen order core must still be exact before even creating
    # the recovery marker. recover_hold_if_authorized() verifies
    # it again before and after the full verifier chain.
    if sha256(ORDER) != ORDER_HASH:
        return False

    try:
        authorize_hold_recovery_current_request()
    except Exception as exc:
        log(
            "AUTO_REQUEST_BOUND_HOLD_RECOVERY_AUTH=WAIT "
            + repr(exc)
        )
        return False

    log(
        "AUTO_REQUEST_BOUND_HOLD_RECOVERY_AUTHORIZED"
    )

    return True


def auto_authorize_orphan_hold_if_safe():

    if not HOLD.exists():
        return False

    if HOLD_RECOVERY_REQUEST.exists():
        return False

    if REQUEST.exists():
        return False

    try:
        if HOLD.stat().st_size != 0:
            return False
    except Exception:
        return False

    if not STATE.is_file():
        return False

    try:
        current_state = json.loads(
            STATE.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception:
        return False

    if not isinstance(current_state, dict):
        return False

    if current_state.get("schema") != (
        "B_BOT_V3_AUTO_RUNTIME_RELEASE_STATE_V1"
    ):
        return False

    if current_state.get("status") != (
        "WAIT_EMERGENCY_HOLD"
    ):
        return False

    if current_state.get("emergency_hold") is not True:
        return False

    if str(current_state.get("request_id") or ""):
        return False

    if str(current_state.get("reason") or ""):
        return False

    # Previous recovery failures are retryable.
    # Every attempt reruns all fail-closed safety verification.
    # A failed verification never clears HOLD.

    if sha256(ORDER) != ORDER_HASH:
        return False

    repaired_state = dict(current_state)

    repaired_state["updated_at"] = now()
    repaired_state[
        "orphan_recovery_authorized"
    ] = True
    repaired_state[
        "orphan_recovery_authorization"
    ] = "AUTO_STRICT_ORPHAN"

    repaired_state.pop(
        "recovery_rejected",
        None,
    )

    atomic_json(
        STATE,
        repaired_state,
    )

    atomic_json(
        HOLD_RECOVERY_REQUEST,
        {
            "schema":
                "B_BOT_V3_ORPHAN_HOLD_RECOVERY_REQUEST_V1",

            "authorized_at":
                now(),

            "authorization":
                "AUTO_ORPHAN_ZERO_BYTE_NO_RUNTIME_REQUEST",
        },
    )

    log(
        "AUTO_ORPHAN_HOLD_RECOVERY_AUTHORIZED"
    )

    return True

def recover_orphan_hold_if_authorized():

    if not HOLD.exists():
        return "NO_HOLD"

    if not HOLD_RECOVERY_REQUEST.is_file():
        return "WAIT"

    try:
        marker_bytes_before = (
            HOLD_RECOVERY_REQUEST.read_bytes()
        )

        marker = json.loads(
            marker_bytes_before.decode(
                "utf-8-sig"
            )
        )
    except Exception:
        return "NOT_ORPHAN"

    if not isinstance(marker, dict):
        return "NOT_ORPHAN"

    if marker.get("schema") != (
        "B_BOT_V3_ORPHAN_HOLD_RECOVERY_REQUEST_V1"
    ):
        return "NOT_ORPHAN"

    try:
        if marker.get("authorization") != (
            "AUTO_ORPHAN_ZERO_BYTE_NO_RUNTIME_REQUEST"
        ):
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_AUTHORIZATION_INVALID"
            )

        if REQUEST.exists():
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_RUNTIME_REQUEST_PRESENT"
            )

        if not STATE.is_file():
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_STATE_MISSING"
            )

        current_state = json.loads(
            STATE.read_text(
                encoding="utf-8-sig"
            )
        )

        if not isinstance(current_state, dict):
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_STATE_NOT_OBJECT"
            )

        if current_state.get("schema") != (
            "B_BOT_V3_AUTO_RUNTIME_RELEASE_STATE_V1"
        ):
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_STATE_SCHEMA_INVALID"
            )

        if current_state.get("status") != (
            "WAIT_EMERGENCY_HOLD"
        ):
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_STATE_NOT_WAIT"
            )

        if current_state.get("emergency_hold") is not True:
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_EMERGENCY_HOLD_FALSE"
            )

        if str(
            current_state.get("request_id")
            or ""
        ):
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_STATE_REQUEST_ID_PRESENT"
            )

        if current_state.get(
            "orphan_recovery_authorized"
        ) is not True:
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_STATE_NOT_AUTHORIZED"
            )

        if current_state.get(
            "orphan_recovery_authorization"
        ) != "AUTO_STRICT_ORPHAN":
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_STATE_AUTH_INVALID"
            )

        if sha256(ORDER) != ORDER_HASH:
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_ORDER_HASH_FAIL"
            )

        verify_hold_recovery_local(logic_verify_mode="PACKAGE")

        if REQUEST.exists():
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_REQUEST_APPEARED_DURING_VERIFY"
            )

        if sha256(ORDER) != ORDER_HASH:
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_ORDER_HASH_CHANGED_AFTER_VERIFY"
            )

        if not HOLD.exists():
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_HOLD_DISAPPEARED"
            )

        if (
            not HOLD_RECOVERY_REQUEST.is_file()
            or
            HOLD_RECOVERY_REQUEST.read_bytes()
            != marker_bytes_before
        ):
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_MARKER_CHANGED"
            )

        if REQUEST.exists():
            raise RuntimeError(
                "ORPHAN_HOLD_RECOVERY_REQUEST_PRESENT_BEFORE_CLEAR"
            )

        HOLD.unlink()

        HOLD_RECOVERY_REQUEST.unlink(
            missing_ok=True
        )

        state(
            "RECOVERED",
            emergency_hold=False,
            recovery_verified=True,
            release_applied=False,
            orphan_recovery=True,
            recovery_authorization=
                "AUTO_STRICT_ORPHAN",
        )

        log(
            "EMERGENCY_ORPHAN_HOLD_RECOVERY=PASS"
        )

        return "RECOVERED"

    except Exception as exc:

        HOLD_RECOVERY_REQUEST.unlink(
            missing_ok=True
        )

        state(
            "WAIT_EMERGENCY_HOLD",
            emergency_hold=True,
            recovery_rejected=repr(exc),
            orphan_recovery=True,
        )

        log(
            "EMERGENCY_ORPHAN_HOLD_RECOVERY=REJECTED "
            + repr(exc)
        )

        return "RECOVERY_REJECTED"


def recover_hold_if_authorized():

    if not HOLD.exists():
        return "NO_HOLD"

    if not HOLD_RECOVERY_REQUEST.is_file():
        return "WAIT"

    # HOLD_RECOVERY_REQUIRES_WAIT_STATE_V1
    # RUNTIME_REQUEST_TRANSACTION_GUARD_V1

    state_request_id = ""

    try:

        if not STATE.is_file():
            raise RuntimeError(
                "HOLD_RECOVERY_STATE_MISSING"
            )

        try:

            current_state = json.loads(
                STATE.read_text(
                    encoding="utf-8-sig"
                )
            )

        except Exception as error:

            raise RuntimeError(
                "HOLD_RECOVERY_STATE_INVALID="
                + type(error).__name__
            )

        if not isinstance(
            current_state,
            dict,
        ):
            raise RuntimeError(
                "HOLD_RECOVERY_STATE_NOT_OBJECT"
            )

        if current_state.get(
            "schema"
        ) != (
            "B_BOT_V3_AUTO_RUNTIME_RELEASE_STATE_V1"
        ):
            raise RuntimeError(
                "HOLD_RECOVERY_STATE_SCHEMA_INVALID"
            )

        if current_state.get(
            "status"
        ) != "WAIT_EMERGENCY_HOLD":
            raise RuntimeError(
                "HOLD_RECOVERY_STATE_NOT_WAIT_EMERGENCY_HOLD"
            )

        if current_state.get(
            "emergency_hold"
        ) is not True:
            raise RuntimeError(
                "HOLD_RECOVERY_STATE_EMERGENCY_HOLD_FALSE"
            )

        state_request_id = str(
            current_state.get(
                "request_id"
            )
            or ""
        )

        if not state_request_id:
            raise RuntimeError(
                "HOLD_RECOVERY_STATE_REQUEST_ID_MISSING"
            )

        marker = json.loads(
            HOLD_RECOVERY_REQUEST.read_text(
                encoding="utf-8-sig"
            )
        )

        if not isinstance(
            marker,
            dict,
        ):
            raise RuntimeError(
                "HOLD_RECOVERY_REQUEST_NOT_OBJECT"
            )

        if marker.get(
            "schema"
        ) != (
            "B_BOT_V3_HOLD_RECOVERY_REQUEST_V1"
        ):
            raise RuntimeError(
                "HOLD_RECOVERY_REQUEST_SCHEMA_INVALID"
            )

        request_source = (
            REQUEST
            if REQUEST.is_file()
            else LAST_APPLIED
        )

        if not request_source.is_file():
            raise RuntimeError(
                "HOLD_RECOVERY_REQUEST_SOURCE_MISSING"
            )

        if request_source == REQUEST:
            req = read_request()

        else:
            raw1 = request_source.read_bytes()

            time.sleep(0.15)

            raw2 = request_source.read_bytes()

            if raw1 != raw2:
                raise RuntimeError(
                    "HOLD_RECOVERY_LAST_APPLIED_NOT_STABLE"
                )

            req = json.loads(
                raw2.decode(
                    "utf-8-sig"
                )
            )

        request_id = validate_request(
            req
        )

        expected_request_id = str(
            marker.get(
                "request_id"
            )
            or ""
        )

        if (
            not expected_request_id
            or
            expected_request_id
            != request_id
        ):
            raise RuntimeError(
                "HOLD_RECOVERY_REQUEST_ID_MISMATCH"
            )

        if (
            state_request_id
            != request_id
        ):
            raise RuntimeError(
                "HOLD_RECOVERY_STATE_REQUEST_ID_MISMATCH"
            )

        if sha256(
            ORDER
        ) != ORDER_HASH:
            raise RuntimeError(
                "HOLD_RECOVERY_ORDER_HASH_FAIL"
            )

        request_bytes_before = (
            request_source.read_bytes()
        )

        verify_hold_recovery_local()

        request_mutated = (
            not request_source.is_file()
            or
            request_source.read_bytes()
            != request_bytes_before
        )

        if request_mutated:

            if request_source == REQUEST:
                restore = REQUEST.with_suffix(
                    ".json.recovery_restore.tmp"
                )

                restore.write_bytes(
                    request_bytes_before
                )

                restore.replace(
                    REQUEST
                )

                raise RuntimeError(
                    "HOLD_RECOVERY_RUNTIME_REQUEST_MUTATED_DURING_VERIFY"
                )

            raise RuntimeError(
                "HOLD_RECOVERY_LAST_APPLIED_MUTATED_DURING_VERIFY"
            )

        if sha256(
            ORDER
        ) != ORDER_HASH:
            raise RuntimeError(
                "HOLD_RECOVERY_ORDER_HASH_CHANGED_AFTER_VERIFY"
            )

        # Last TOCTOU check immediately before latch mutation.
        if (
            not request_source.is_file()
            or
            request_source.read_bytes()
            != request_bytes_before
        ):

            if request_source == REQUEST:
                restore = REQUEST.with_suffix(
                    ".json.recovery_restore.tmp"
                )

                restore.write_bytes(
                    request_bytes_before
                )

                restore.replace(
                    REQUEST
                )

                raise RuntimeError(
                    "HOLD_RECOVERY_RUNTIME_REQUEST_CHANGED_BEFORE_CLEAR"
                )

            raise RuntimeError(
                "HOLD_RECOVERY_LAST_APPLIED_CHANGED_BEFORE_CLEAR"
            )

        HOLD.unlink()

        HOLD_RECOVERY_REQUEST.unlink(
            missing_ok=True
        )

        state(
            "RECOVERED",
            request_id=request_id,
            emergency_hold=False,
            recovery_verified=True,
            release_applied=(
                request_source == LAST_APPLIED
            ),
        )

        log(
            "EMERGENCY_HOLD_RECOVERY=PASS "
            + request_id
        )

        return "RECOVERED"

    except Exception as exc:

        HOLD_RECOVERY_REQUEST.unlink(
            missing_ok=True
        )

        wait_extra = {
            "emergency_hold":
                True,

            "recovery_rejected":
                repr(exc),
        }

        if state_request_id:

            wait_extra[
                "request_id"
            ] = state_request_id

        state(
            "WAIT_EMERGENCY_HOLD",
            **wait_extra,
        )

        log(
            "EMERGENCY_HOLD_RECOVERY=REJECTED "
            + repr(exc)
        )

        return "RECOVERY_REJECTED"







def tick():

    roadmap_policy()

    if HOLD.exists():
        try:
            hold_kind = HOLD.read_text(encoding="utf-8-sig").split("|", 1)[0]
        except OSError:
            return "WAIT_HOLD_TRANSITION"

        # ?? ??? ?? ??? ?? ??????? ???? ???.
        if hold_kind == "ROADMAP_RESEARCH_V1":
            state(
                "WAIT_RESEARCH",
                emergency_hold=False,
                research_hold=True,
                release_applied=False,
            )
            log("SERVER_PUSH_HOLD=RESEARCH_SCOPED; RECOVERY_NOT_STARTED")
            return "RESEARCH_HOLD"

        auto_authorize_request_bound_hold_if_safe()
        auto_authorize_orphan_hold_if_safe()

        orphan_recovery = (
            recover_orphan_hold_if_authorized()
        )

        if orphan_recovery == "RECOVERED":

            # Never release in the same tick
            # that clears Emergency HOLD.
            return "RECOVERED"

        if orphan_recovery == "NOT_ORPHAN":

            recovery = (
                recover_hold_if_authorized()
            )

        else:

            recovery = orphan_recovery

        if recovery == "RECOVERED":

            # Never release in the same tick
            # that clears Emergency HOLD.
            return "RECOVERED"

        previous = {}

        if STATE.is_file():

            try:

                previous = json.loads(
                    STATE.read_text(
                        encoding="utf-8-sig"
                    )
                )

            except Exception:

                previous = {}

        wait_extra = {
            "emergency_hold":
                True,
        }

        for key in (
            "request_id",
            "reason",
            "recovery_rejected",
        ):

            if previous.get(
                key
            ) is not None:

                wait_extra[
                    key
                ] = previous.get(
                    key
                )

        state(
            "WAIT_EMERGENCY_HOLD",
            **wait_extra,
        )

        return "HOLD"

    # V3_MINIMAL_SHADOW_RUNTIME_GATE_V1
    #
    # No runtime request exists at PENDING_SHADOW.
    # Produce local Shadow evidence, then let deployment
    # materialize Canary only when minimal_shadow_pass()
    # accepts the evidence.
    if REQUEST.is_file() is False:

        try:

            import research_controller as controller
            try:
                with controller.lock():
                    if HOLD.exists():
                        return "RESEARCH_HOLD"
                    for tf in controller.DEPLOY_TF_ORDER:
                        if not controller.can_accept_canary_peer(tf):
                            continue
                        excluded = set()
                        while True:
                            candidate = controller.strategy_research._pipeline_local_shadow_choice(tf, excluded)
                            if not candidate:
                                break
                            if controller.candidate_already_tracked(tf, candidate):
                                excluded.add(str(candidate.get("strategy_id") or "").upper())
                                continue
                            prepared = v3_deployment.create_provisional_request(tf, candidate)
                            if prepared:
                                log("[주문][AUTO_RELEASE][LOCAL_SHADOW_READY] " + str(candidate.get("strategy_id")) + " " + str(candidate.get("exact_strategy_version")))
                            break
                    shadow_result = v3_shadow_engine.process_pending_shadows()
                    materialized_request = v3_deployment.materialize_local_ready_request()
            except RuntimeError as busy:
                if str(busy) == "RESEARCH_ALREADY_RUNNING":
                    return "RESEARCH_BUSY"
                raise

        except Exception as shadow_error:

            log(
                "SHADOW_RUNTIME_GATE=WAIT "
                + repr(
                    shadow_error
                )
            )

            state(
                "WAIT_SHADOW",
                emergency_hold=False,
                shadow_error=repr(
                    shadow_error
                ),
            )

            return "SHADOW_WAIT"

        if int(
            shadow_result.get(
                "processed"
            )
            or 0
        ) > 0:

            log(
                "SHADOW_RUNTIME_GATE "
                + json.dumps(
                    {
                        "processed":
                            shadow_result.get(
                                "processed"
                            ),

                        "passed":
                            shadow_result.get(
                                "passed"
                            ),

                        "pending":
                            shadow_result.get(
                                "pending"
                            ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        if (
            materialized_request
            is not None
        ):

            log(
                "SHADOW_TO_CANARY_MATERIALIZED "
                + str(
                    materialized_request.get(
                        "request_id"
                    )
                    or ""
                )
            )

    if not REQUEST.is_file():

        state(
            "IDLE",
            emergency_hold=False,
        )

        return "IDLE"

    request_id = ""

    try:

        req = read_request()

        # PREVALIDATION_REQUEST_ID_BINDING_V1
        request_id = str(
            req.get(
                "request_id"
            )
            or ""
        )

        validated_request_id = (
            validate_request(
                req
            )
        )

        if (
            not request_id
            or
            validated_request_id
            != request_id
        ):
            raise RuntimeError(
                "REQUEST_ID_VALIDATION_BINDING_FAIL"
            )

        prev = {}

        if STATE.is_file():

            try:

                prev = json.loads(
                    STATE.read_text(
                        encoding="utf-8-sig"
                    )
                )

            except Exception:

                prev = {}

        if (
            prev.get("status")
            == "APPLIED"
            and
            prev.get("request_id")
            == request_id
        ):

            # APPLIED_DUPLICATE_REQUEST_CONSUME_V1
            # A request already committed to last_applied is stale.
            # Consume it locally before any verify/pull/runtime-sync path.
            applied_path = (
                REQUEST.parent
                / "runtime_sync_last_applied.json"
            )

            if not applied_path.is_file():
                raise RuntimeError(
                    "DUPLICATE_APPLIED_REQUEST_LEDGER_MISSING"
                )

            try:

                applied_req = json.loads(
                    applied_path.read_text(
                        encoding="utf-8-sig"
                    )
                )

            except Exception as error:

                raise RuntimeError(
                    "DUPLICATE_APPLIED_REQUEST_LEDGER_INVALID="
                    + type(error).__name__
                )

            if not isinstance(
                applied_req,
                dict,
            ):
                raise RuntimeError(
                    "DUPLICATE_APPLIED_REQUEST_LEDGER_NOT_OBJECT"
                )

            for key in (
                "schema",
                "request_id",
                "strategy_version",
                "strategy",
                "timeframe",
                "tier",
                "version_kind",
            ):

                if str(
                    applied_req.get(key)
                    or ""
                ) != str(
                    req.get(key)
                    or ""
                ):
                    raise RuntimeError(
                        "DUPLICATE_APPLIED_REQUEST_LEDGER_MISMATCH="
                        + key
                    )

            REQUEST.unlink()

            log(
                "DUPLICATE_APPLIED_REQUEST_CONSUMED "
                + request_id
            )

            return "DUPLICATE"


        state(
            "VERIFYING",
            request_id=request_id,
            tier=req.get(
                "tier"
            ),
            version_kind=req.get(
                "version_kind"
            ),
        )

        # RUNTIME_REQUEST_TRANSACTION_GUARD_V1
        #
        # All verification is logically read-only.
        # If a verifier accidentally consumes or rewrites the active
        # request, restore the exact validated bytes before proceeding.
        #
        # The release still uses the exact original request and then
        # validates it again after PRE_RELEASE_PULL.

        request_bytes_before_verify = (
            REQUEST.read_bytes()
        )

        verify_local()

        request_mutated = (
            not REQUEST.is_file()
            or
            REQUEST.read_bytes()
            != request_bytes_before_verify
        )

        if request_mutated:

            restore = REQUEST.with_suffix(
                ".json.verify_restore.tmp"
            )

            restore.write_bytes(
                request_bytes_before_verify
            )

            restore.replace(
                REQUEST
            )

            log(
                "VERIFY_LOCAL_REQUEST_MUTATION_RESTORED "
                + request_id
            )

        # Exact original request must now be present.
        if (
            not REQUEST.is_file()
            or
            REQUEST.read_bytes()
            != request_bytes_before_verify
        ):
            raise RuntimeError(
                "VERIFY_LOCAL_REQUEST_RESTORE_FAILED"
            )

        # Revalidate restored/original bytes.
        req_verified = read_request()

        if validate_request(
            req_verified
        ) != request_id:
            raise RuntimeError(
                "REQUEST_CHANGED_DURING_VERIFY"
            )

        pull(
            "PRE_RELEASE_PULL"
        )

        req2 = read_request()

        if str(
            req2.get(
                "request_id"
            )
            or ""
        ) != request_id:
            raise RuntimeError(
                "REQUEST_CHANGED_AFTER_PRE_PULL"
            )

        validate_request(
            req2
        )

        state(
            "APPLYING",
            request_id=request_id,
            tier=req2.get(
                "tier"
            ),
        )

        apply_runtime_sync()

        pull(
            "POST_RELEASE_PULL"
        )

        if sha256(
            ORDER
        ) != ORDER_HASH:
            raise RuntimeError(
                "ORDER_CHANGED_AFTER_RUNTIME_SYNC"
            )

        state(
            "APPLIED",
            request_id=request_id,
            tier=req2.get(
                "tier"
            ),
            version_kind=req2.get(
                "version_kind"
            ),
            strategy_version=req2.get(
                "strategy_version"
            ),
            emergency_hold=False,
        )

        log(
            "AUTO_RUNTIME_RELEASE=PASS "
            + request_id
        )

        return "APPLIED"

    except Exception as exc:

        hard_stop(
            exc,
            request_id,
        )

        return "HARD_STOP"



def self_test():

    roadmap_policy()

    if sha256(ORDER) != ORDER_HASH:
        raise RuntimeError(
            "ORDER_FROZEN_HASH_FAIL"
        )

    for p in (
        SYNC_FROM,
        RUNTIME_SYNC,
        VERIFY_V3,
        VERIFY_OVERFIT,
        VERIFY_LOGIC,
        VERIFY_R4_SYNC,
        VERIFY_POS,
        VERIFY_AUTO,
    ):

        if not p.is_file():
            raise RuntimeError(
                "REQUIRED_FILE_MISSING="
                + str(p)
            )

    print(
        "AUTO_RELEASE_WORKER_SELFTEST=PASS"
    )

    print(
        "SERVER_WRITE=0"
    )

    return 0


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--loop",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )


    args = parser.parse_args()

    try:
        acquire_lock()

    except RuntimeError as exc:

        if str(exc) == "AUTO_RELEASE_ALREADY_RUNNING":
            print(
                "AUTO_RELEASE_ALREADY_RUNNING=YES"
            )
            return 0

        raise


    if args.self_test:
        return self_test()

    if args.loop <= 0:

        print(
            "AUTO_RELEASE_TICK="
            + tick()
        )

        return 0

    log(
        "AUTO_RELEASE_WORKER_STARTED PIPELINE_BRIDGE_V1"
    )

    while True:

        result = tick()

        # hard stop 뒤에는 계속 서버 release를 시도하지 않는다.
        if result == "HARD_STOP":

            time.sleep(
                max(
                    args.loop,
                    30,
                )
            )

        else:

            time.sleep(
                max(
                    args.loop,
                    5,
                )
            )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
