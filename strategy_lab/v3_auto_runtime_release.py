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
_github_pull_cache = {}


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


def pull(label, research_only=False):
    if research_only:
        # GitHub는 연구만 소유하고, 서버 검증·섀도·적용은 기존 작업자가 소유한다.
        import base64,io,re,zipfile
        config_path=ROOT/'strategy_lab/lab_config.json'
        config=json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
        source=config.get('github_research') or {}
        if source.get('enabled') is not True:
            return {'status':'DISABLED','imported':0}
        if source.get('repository')!='hans1177/bbot-lab':raise RuntimeError('GITHUB_RESEARCH_REPOSITORY_INVALID')
        if time.monotonic() < _github_pull_cache.get('next_check',0):
            if _github_pull_cache.get('error'):raise RuntimeError(_github_pull_cache['error'])
            return _github_pull_cache.get('result',{'status':'WAIT_POLL','imported':0})
        _github_pull_cache['next_check']=time.monotonic()+60
        try:
            def api(path):
                rc,output=run(['gh','api','repos/hans1177/bbot-lab'+path],timeout=90)
                if rc:raise RuntimeError('GITHUB_RESEARCH_READ_FAILED')
                return json.loads(output)
            if api('').get('private') is not True:raise RuntimeError('GITHUB_RESEARCH_PRIVATE_REQUIRED')
            head=api('/git/ref/heads/research-state')['object']['sha']
            if not re.fullmatch('[0-9a-f]{40}',head):raise RuntimeError('GITHUB_RESEARCH_HEAD_INVALID')
            receipt_path=ROOT/'strategy_lab/results/v3/github_research_receipt.json'
            previous=json.loads(receipt_path.read_text(encoding='utf-8-sig')) if receipt_path.exists() else {}
            if previous.get('checkpoint_commit')==head:
                result={'status':'UNCHANGED','imported':0,'checkpoint_commit':head}
            else:
                commit=api('/git/commits/'+head)
                tree=api('/git/trees/'+commit['tree']['sha'])['tree']
                entries={row['path']:row for row in tree}
                if set(entries)!={'CHECKPOINT.json','checkpoint.zip'}:raise RuntimeError('GITHUB_RESEARCH_TREE_INVALID')
                blobs={}
                for name,item in entries.items():
                    if item['type']!='blob' or item.get('size',0)>90*1024**2:raise RuntimeError('GITHUB_RESEARCH_BLOB_INVALID')
                    value=api('/git/blobs/'+item['sha']);raw=base64.b64decode(value['content'])
                    if len(raw)!=value['size']:raise RuntimeError('GITHUB_RESEARCH_BLOB_SIZE')
                    blobs[name]=raw
                receipt=json.loads(blobs['CHECKPOINT.json'])
                if receipt.get('schema')!='B_BOT_RESEARCH_CHECKPOINT_V1' or hashlib.sha256(blobs['checkpoint.zip']).hexdigest()!=receipt.get('archive_sha256'):raise RuntimeError('GITHUB_RESEARCH_CHECKPOINT_HASH')
                if receipt.get('constitution_sha256')!=sha256(ROOT/'ROADMAP_CONSTITUTION_V3.json'):raise RuntimeError('GITHUB_RESEARCH_CONSTITUTION_MISMATCH')
                if receipt.get('engine_sha256')!=source.get('engine_sha256'):raise RuntimeError('GITHUB_RESEARCH_ENGINE_MISMATCH')
                if not re.fullmatch('[0-9a-f]{40}',str(receipt.get('source_commit') or '')):raise RuntimeError('GITHUB_RESEARCH_SOURCE_COMMIT')
                verified={}
                with zipfile.ZipFile(io.BytesIO(blobs['checkpoint.zip'])) as archive:
                    if len(archive.namelist())!=len(set(archive.namelist())) or set(archive.namelist())!=set(receipt.get('files') or {}):raise RuntimeError('GITHUB_RESEARCH_FILE_SET')
                    if sum(item.file_size for item in archive.infolist())>512*1024**2:raise RuntimeError('GITHUB_RESEARCH_EXPANDED_LIMIT')
                    for name,row in receipt['files'].items():
                        target=(ROOT/name).resolve()
                        if not name.startswith('strategy_lab/results/') or not target.is_relative_to(ROOT.resolve()) or '\\' in name or '..' in Path(name).parts:raise RuntimeError('GITHUB_RESEARCH_PATH_INVALID')
                        raw=archive.read(name)
                        if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise RuntimeError('GITHUB_RESEARCH_FILE_HASH')
                        verified[name]=raw
                pipeline_name='strategy_lab/results/v3/historical_314_pipeline_stage2.json'
                pipeline=json.loads(verified.get(pipeline_name,b'{"entries":[]}'))
                candidates=[];seen=set()
                for row in pipeline.get('entries',[]):
                    if not isinstance(row,dict) or row.get('pipeline_stage')!='LOCAL_SHADOW_PENDING' or row.get('eligible') is not True:continue
                    if (row.get('full_validation_gate') or {}).get('pass') is not True or (row.get('sealed_final_blind_gate') or {}).get('pass') is not True or (row.get('sealed_final_blind_gate') or {}).get('next_stage')!='LOCAL_SHADOW_PENDING':raise RuntimeError('GITHUB_RESEARCH_FINAL_GATES_REQUIRED')
                    version=str(row.get('exact_strategy_version') or '')
                    if not version or version in seen:raise RuntimeError('GITHUB_RESEARCH_DUPLICATE_VERSION')
                    seen.add(version)
                    rel=str(row.get('canonical_source_file') or '')
                    prefixes=('strategy_lab/results/v3/generated_trials/','strategy_lab/results/v3/generated_combinations/','strategy_lab/results/v3/generated_safe_grammar/','strategy_lab/results/v3/redeveloped_strategies/')
                    prefix=next((p for p in prefixes if rel.startswith(p)),None)
                    if not prefix or '/' in rel[len(prefix):] or not rel.endswith('.py') or rel not in verified:raise RuntimeError('GITHUB_RESEARCH_SOURCE_MISSING')
                    raw=verified[rel]
                    if hashlib.sha256(raw).hexdigest()!=row.get('source_sha256'):raise RuntimeError('GITHUB_RESEARCH_SOURCE_HASH')
                    if '/generated_trials/' in rel and b'PORTABLE_GENERATED_SINGLE_V2' not in raw[:4096]:raise RuntimeError('GITHUB_RESEARCH_SOURCE_NOT_PORTABLE')
                    compile(raw.decode('utf-8-sig'),rel,'exec')
                    candidates.append(row)
                import research_controller as controller
                import v3_research_knowledge as knowledge
                cloud_knowledge=json.loads(verified.get('strategy_lab/results/v3/research_knowledge.json',b'{"records":{}}'))
                with controller.lock():
                    if REQUEST.exists() or HOLD.exists():raise RuntimeError('GITHUB_RESEARCH_LOCAL_RELEASE_BUSY')
                    local_path=ROOT/pipeline_name
                    local=json.loads(local_path.read_text(encoding='utf-8-sig')) if local_path.exists() else {'schema':'B_BOT_UNIFIED_STRATEGY_PIPELINE_STAGE10_V1','entries':[]}
                    existing={str(r.get('exact_strategy_version')):r for r in local.get('entries',[]) if isinstance(r,dict)}
                    prepared=[];copies={}
                    for row in candidates:
                        version=row['exact_strategy_version']
                        if version in existing:
                            bound=existing[version]
                            if bound.get('github_research_origin',{}).get('source_sha256')!=row['source_sha256']:raise RuntimeError('GITHUB_RESEARCH_EXISTING_VERSION_CONFLICT')
                            continue
                        record=next((r for r in cloud_knowledge.get('records',{}).values() if isinstance(r,dict) and r.get('exact_strategy_version')==version and r.get('source_sha256')==row['source_sha256'] and str(r.get('family_file') or r.get('canonical_source_file') or r.get('source_file') or '').replace(chr(92),'/')==row['canonical_source_file'] and str(r.get('strategy') or r.get('strategy_id') or '').upper()==str(row.get('strategy_id') or '').upper() and str(r.get('timeframe') or '').upper()==str(row.get('timeframe') or '').upper() and str(r.get('status')).upper()=='PASS' and (r.get('evaluation') or {}).get('pass') is True),None)
                        if record is None:raise RuntimeError('GITHUB_RESEARCH_BOUND_BACKTEST_REQUIRED')
                        selected={row['canonical_source_file']}
                        def references(value):
                            if isinstance(value,dict):
                                for item in value.values():references(item)
                            elif isinstance(value,list):
                                for item in value:references(item)
                            elif isinstance(value,str) and value.startswith('strategy_lab/results/') and value.endswith(('.json','.py','.parquet','.csv')):
                                if value not in verified:raise RuntimeError('GITHUB_RESEARCH_EVIDENCE_MISSING')
                                selected.add(value)
                        references(row);references(record)
                        for name in selected:
                            if name.endswith(('runtime_sync_request.json','deployment_state.json','version_registry.json')) or '/strategy_config_pending_' in name:raise RuntimeError('GITHUB_RESEARCH_MUTABLE_PAYLOAD_FORBIDDEN')
                            target=ROOT/name;raw=verified[name]
                            if target.exists() and target.read_bytes()!=raw:raise RuntimeError('GITHUB_RESEARCH_FILE_COLLISION')
                            copies[name]=raw
                        imported=dict(row)
                        imported['github_research_origin']={'repository':'hans1177/bbot-lab','checkpoint_commit':head,'source_commit':receipt['source_commit'],'source_sha256':row['source_sha256']}
                        prepared.append((imported,record))
                    for name,raw in copies.items():
                        target=ROOT/name;target.parent.mkdir(parents=True,exist_ok=True)
                        if not target.exists():
                            with target.open('xb') as output:output.write(raw)
                    for row,record in prepared:
                        knowledge.record_result('github_release:'+row['exact_strategy_version'],record,failed=False)
                        local.setdefault('entries',[]).append(row)
                    if prepared:
                        local['updated_at']=now();atomic_json(local_path,local)
                    result={'status':'IMPORTED' if prepared else 'WAIT_FINAL_RESEARCH','imported':len(prepared),'checkpoint_commit':head,'updated_at':now(),'orders_touched':False}
                    atomic_json(receipt_path,result)
                log('[주문][AUTO_RELEASE][GITHUB_RESEARCH] '+json.dumps(result,ensure_ascii=True))
            _github_pull_cache.update(result=result,error=None)
            return result
        except Exception as error:
            _github_pull_cache['error']=str(error)
            raise


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
    try:
        if not REQUEST.exists() and not HOLD.exists():
            pull("GITHUB_RESEARCH",research_only=True)
    except Exception as error:
        log("[주문][AUTO_RELEASE][GITHUB_RESEARCH_WAIT] "+str(error))
        state("WAIT_GITHUB_RESEARCH",reason=str(error),release_applied=False)
        return "GITHUB_RESEARCH_WAIT"

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


def connect_github(code_ref):
    """기존 로컬 배포 작업자를 GitHub 연구 소유권에 연결한다. 서버 파일은 쓰지 않는다."""
    import ast,base64,re
    if os.name!='nt':raise RuntimeError('GITHUB_CONNECTION_REQUIRES_EXISTING_WINDOWS_RELEASE_HOST')
    if not re.fullmatch('[0-9a-f]{40}',str(code_ref or '')):raise RuntimeError('PINNED_CODE_COMMIT_REQUIRED')
    if REQUEST.exists():raise RuntimeError('EXISTING_RELEASE_REQUEST_MUST_FINISH_FIRST')
    hold_text=HOLD.read_text(encoding='utf-8-sig') if HOLD.exists() else None
    if hold_text is not None and not hold_text.startswith('ROADMAP_RESEARCH_V1|'):raise RuntimeError('EXISTING_NONRESEARCH_HOLD_PRESERVED')
    self_test()
    sources={}
    for name in ('v3_auto_runtime_release.py','v3_deployment.py'):
        rc,out=run(['gh','api','repos/hans1177/bbot-lab/contents/strategy_lab/'+name+'?ref='+code_ref,'--jq','.content'],timeout=90)
        if rc:raise RuntimeError('PINNED_CONNECTION_CODE_READ_FAILED')
        raw=base64.b64decode(out);compile(raw.decode('utf-8-sig'),name,'exec');sources[name]=raw
    target_auto=ROOT/'strategy_lab/v3_auto_runtime_release.py'
    allowed={'da4e5ae9c5b9491bdfa1b13c65aad96cc6391c35f255d6a0547e50b0e7663fcf',hashlib.sha256(sources['v3_auto_runtime_release.py']).hexdigest()}
    if sha256(target_auto) not in allowed:raise RuntimeError('LOCAL_RELEASE_WORKER_CHANGED_REVIEW_REQUIRED')
    # 소스 선택 책임 함수만 교체하여 다른 로컬 배포 기능은 보존한다.
    dep=ROOT/'strategy_lab/v3_deployment.py';old_dep=dep.read_text(encoding='utf-8-sig')
    old_fn=next(n for n in ast.parse(old_dep).body if isinstance(n,ast.FunctionDef) and n.name=='source_for_pipeline')
    new_dep=sources['v3_deployment.py'].decode('utf-8-sig')
    new_fn=next(n for n in ast.parse(new_dep).body if isinstance(n,ast.FunctionDef) and n.name=='source_for_pipeline')
    old_lines=old_dep.splitlines(keepends=True);new_lines=new_dep.splitlines(keepends=True)
    old_lines[old_fn.lineno-1:old_fn.end_lineno]=new_lines[new_fn.lineno-1:new_fn.end_lineno]
    dep_bytes=''.join(old_lines).encode('utf-8')
    # 기존 Controller 진입 함수에서 로컬 연구만 멈춘다. 연구 원장·큐는 보존한다.
    controller_path=ROOT/'strategy_lab/research_controller.py'
    controller_text=controller_path.read_text(encoding='utf-8-sig');controller_lines=controller_text.splitlines(keepends=True)
    guard='''    # GITHUB_RESEARCH_OWNER: 계산은 GitHub, 검증·배포는 기존 작업자.
    if (load(LAB / "lab_config.json", {}).get("github_research") or {}).get("enabled") is True:
        return {"action":"SKIP","reason":"GITHUB_RESEARCH_OWNER","orders_touched":False}
'''
    functions=[n for n in ast.parse(controller_text).body if isinstance(n,ast.FunctionDef) and n.name in {'managed_research_once','run_once'}]
    if len(functions)!=2:raise RuntimeError('LOCAL_RESEARCH_ENTRYPOINTS_CHANGED')
    for fn in sorted(functions,key=lambda n:n.lineno,reverse=True):
        if 'GITHUB_RESEARCH_OWNER' not in ast.get_source_segment(controller_text,fn):controller_lines[fn.body[0].lineno-1:fn.body[0].lineno-1]=[guard]
    controller_bytes=''.join(controller_lines).encode('utf-8')
    updates={target_auto:sources['v3_auto_runtime_release.py'],dep:dep_bytes,controller_path:controller_bytes}
    for path,raw in updates.items():compile(raw.decode('utf-8-sig'),str(path),'exec')
    config_path=ROOT/'strategy_lab/lab_config.json';config_raw=config_path.read_bytes()
    config=json.loads(config_raw.decode('utf-8-sig'))
    config['github_research']={'enabled':True,'repository':'hans1177/bbot-lab','code_ref':code_ref,'engine_sha256':'3c665f886a02757e3b35444b437312385d88790c47c6346e90c64dfc0a5a86ed','qualification_required':'FULL_VALIDATION_AND_SEALED_FINAL_BLIND_THEN_EXISTING_LOCAL_SHADOW'}
    # 완료 중인 연구 자식 프로세스는 종료하지 않는다. 재기동 스케줄러만 정리한다.
    stop_script="Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -like '*C:\\my\\bottest*' -and $_.CommandLine -match 'v3_auto_runtime_release\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction Stop }"
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    originals={path:path.read_bytes() for path in updates}
    originals[config_path]=config_raw
    for path,raw in originals.items():path.with_name(path.name+'.bak_github_'+stamp).write_bytes(raw)
    worker=[str(ROOT/'.venv/Scripts/pythonw.exe'),str(target_auto),'--loop','15']
    owned_hold='GITHUB_RESEARCH_CONNECTION|'+code_ref
    if hold_text is None:
        with HOLD.open('x',encoding='utf-8') as output:output.write(owned_hold)
    if REQUEST.exists():
        if hold_text is None and HOLD.read_text(encoding='utf-8-sig')==owned_hold:HOLD.unlink()
        raise RuntimeError('EXISTING_RELEASE_REQUEST_MUST_FINISH_FIRST')
    proc=subprocess.run(['powershell.exe','-NoProfile','-Command',stop_script],capture_output=True,text=True,timeout=30)
    if proc.returncode:
        if hold_text is None and HOLD.read_text(encoding='utf-8-sig')==owned_hold:HOLD.unlink()
        subprocess.Popen(worker,cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raise RuntimeError('LOCAL_WORKER_STOP_FAILED')
    try:
        for path,raw in updates.items():
            temp=path.with_name(path.name+'.github.tmp');temp.write_bytes(raw);os.replace(temp,path)
        atomic_json(config_path,config)
        expected_hold=hold_text if hold_text is not None else owned_hold
        if not HOLD.exists() or HOLD.read_text(encoding='utf-8-sig')!=expected_hold:raise RuntimeError('CONNECTION_HOLD_CHANGED')
        HOLD.unlink()
        _github_pull_cache.clear()
        deadline=time.monotonic()+90
        while True:
            try:
                result=pull('GITHUB_CONNECT',research_only=True);break
            except RuntimeError as error:
                if str(error)!='RESEARCH_ALREADY_RUNNING' or time.monotonic()>=deadline:raise
                _github_pull_cache.clear();time.sleep(2)
    except Exception:
        for path,raw in originals.items():
            temp=path.with_name(path.name+'.github.rollback.tmp');temp.write_bytes(raw);os.replace(temp,path)
        current_hold=HOLD.read_text(encoding='utf-8-sig') if HOLD.exists() else None
        if current_hold is None or current_hold in (owned_hold,hold_text):
            if hold_text is not None:HOLD.write_text(hold_text,encoding='utf-8')
            elif HOLD.exists():HOLD.unlink()
        subprocess.Popen(worker,cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raise
    stop_research=stop_script.replace(r'v3_auto_runtime_release\.py',r'local_continuous_research\.py|roadmap_manager\.py')
    subprocess.run(['powershell.exe','-NoProfile','-Command',stop_research],capture_output=True,text=True,timeout=30)
    subprocess.Popen(worker,cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    print(json.dumps({'connection':'CONFIGURED','github_research':result,'local_research_enabled':False,'existing_release_worker_started':True,'server_write':False,'real_order':False},ensure_ascii=True))
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


    parser.add_argument("--connect-github",action="store_true")
    parser.add_argument("--code-ref")
    parser.add_argument("--github-check",action="store_true")
    args = parser.parse_args()
    if args.connect_github:
        return connect_github(args.code_ref)
    if args.github_check:
        print(json.dumps(pull("GITHUB_RESEARCH",research_only=True),ensure_ascii=True))
        return 0

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
