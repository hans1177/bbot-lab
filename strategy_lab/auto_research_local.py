# -*- coding: utf-8 -*-
# 파일: C:\my\bottest\strategy_lab\auto_research_local.py
#
# 역할
# - LOCAL = SOURCE OF TRUTH
# - 기존 연구/백테스트 결과와 연결
# - 기존 체크포인트/결과 재사용
# - runner/gate/daemon/config/strategies 구조 검증
# - 모든 검증 PASS 후 LOCAL FINAL manifest 생성
# - 선택적으로 서버 /tmp READY까지만 동기화
#
# 절대 하지 않는 것
# - 서버 live runner.py 교체
# - systemctl restart
# - 실거래 주문
# - /home/ec2-user/bot live 파일 수정

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# PATH
# ============================================================

ROOT = Path(r"C:\my\bottest")
LAB = ROOT / "strategy_lab"
EXPERIMENTS = LAB / "experiments"
RESULTS = LAB / "results"
PIPE_RESULTS = RESULTS / "research_pipeline_v2"

STRATEGY_DIR = ROOT / "assist" / "strategies"

FINAL_DIR = RESULTS / "local_final"
FINAL_MANIFEST = FINAL_DIR / "local_final_manifest.json"

LOCK_FILE = RESULTS / "auto_research_local.lock"

PYTHON = (
    ROOT / ".venv" / "Scripts" / "python.exe"
)

if not PYTHON.is_file():
    PYTHON = Path(sys.executable)


# ============================================================
# SOURCE OF TRUTH
# ============================================================

CORE_FILES = [
    "runner.py",
    "gate.py",
    "strategy_config.json",
    "assist/daemon.py",
]

REQUIRED_STRATEGIES = [
    "div.py",
    "ema_cross.py",
    "macd.py",
    "obv.py",
    "sar.py",
    "shape.py",
    "stoch.py",
]


# ============================================================
# SERVER READY ONLY
# ============================================================

SERVER_HOST = "ec2-user@13.159.128.120"
SERVER_KEY = Path(r"C:\my\han.pem")

REMOTE_READY = "/tmp/config_driven_refactor_ready"
REMOTE_ARCHIVE = "/tmp/local_final_ready.tar.gz"


# ============================================================
# UTIL
# ============================================================

def fail(msg):
    raise RuntimeError(msg)


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def sha256(path: Path):
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def read_json(path: Path):
    return json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )


def run(cmd, cwd=None):
    print()
    print("$", " ".join(map(str, cmd)))

    p = subprocess.run(
        [str(x) for x in cmd],
        cwd=str(cwd) if cwd else None,
    )

    if p.returncode != 0:
        fail(
            "COMMAND FAIL "
            f"rc={p.returncode}: "
            + " ".join(map(str, cmd))
        )


def atomic_json(path: Path, payload):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    os.replace(tmp, path)


# ============================================================
# LOCK
# ============================================================

def acquire_lock():
    RESULTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    if LOCK_FILE.exists():
        try:
            age = (
                time.time()
                - LOCK_FILE.stat().st_mtime
            )

            # 24시간 넘은 stale lock은 제거.
            if age > 86400:
                LOCK_FILE.unlink()
            else:
                fail(
                    "AUTO RESEARCH already locked: "
                    + str(LOCK_FILE)
                )

        except FileNotFoundError:
            pass

    LOCK_FILE.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": utc_now(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def release_lock():
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


# ============================================================
# SOURCE VALIDATION
# ============================================================

def validate_local_source():
    print()
    print("=" * 88)
    print("LOCAL SOURCE VALIDATION")
    print("=" * 88)

    if not ROOT.is_dir():
        fail(f"ROOT missing: {ROOT}")

    if not LAB.is_dir():
        fail(f"LAB missing: {LAB}")

    for rel in CORE_FILES:
        p = ROOT / rel

        if not p.is_file():
            fail(
                f"CORE FILE missing: {p}"
            )

    if not STRATEGY_DIR.is_dir():
        fail(
            "strategy dir missing: "
            + str(STRATEGY_DIR)
        )

    for name in REQUIRED_STRATEGIES:
        p = STRATEGY_DIR / name

        if not p.is_file():
            fail(
                f"strategy missing: {p}"
            )

    print("LOCAL SOURCE FILES = PASS")


# ============================================================
# PYTHON SYNTAX
# ============================================================

def validate_python_syntax():
    paths = [
        ROOT / "runner.py",
        ROOT / "gate.py",
        ROOT / "assist" / "daemon.py",
    ]

    paths += sorted(
        STRATEGY_DIR.glob("*.py")
    )

    for p in paths:
        source = p.read_text(
            encoding="utf-8-sig"
        )

        ast.parse(
            source,
            filename=str(p),
        )

    print(
        "LOCAL PYTHON SYNTAX = PASS"
    )


# ============================================================
# STRATEGY CONTRACT
# ============================================================

def validate_strategy_contract():
    rows = []

    for p in sorted(
        STRATEGY_DIR.glob("*.py")
    ):
        if p.name.startswith("_"):
            continue

        source = p.read_text(
            encoding="utf-8-sig"
        )

        tree = ast.parse(
            source,
            filename=str(p),
        )

        nodes = [
            n
            for n in ast.walk(tree)
            if isinstance(
                n,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
            and n.name == "signal"
        ]

        if not nodes:
            # 보조 모듈이면 config가 실제 사용하는지 뒤에서 판단.
            rows.append(
                {
                    "file": p.name,
                    "signal": False,
                }
            )
            continue

        if len(nodes[0].args.args) < 1:
            fail(
                f"signal(df) invalid: {p}"
            )

        rows.append(
            {
                "file": p.name,
                "signal": True,
            }
        )

    print(
        "STRATEGY CONTRACT SCAN = PASS"
    )

    return rows


# ============================================================
# CONFIG
# ============================================================

def validate_config_strategy_link():
    # V3_CANONICAL_VALIDATION_SOURCE_V1
    #
    # Runtime ASSIST_FILE artifacts under assist/strategies are release
    # artifacts, not the immutable source of truth.
    #
    # Validation rule:
    #   1. Existing runtime strategy file may be validated directly.
    #   2. If an ASSIST_FILE runtime mirror is absent, the registry file
    #      basename must resolve exactly to one immutable generated source
    #      under an allowed V3 generated source root.
    #   3. Unknown paths, traversal, ambiguous generated sources, invalid
    #      source, or missing signal() fail closed.
    #   4. Legacy non-ASSIST_FILE strategy validation remains unchanged.

    cfg_path = (
        ROOT / "strategy_config.json"
    )

    cfg = read_json(cfg_path)

    frames = (
        cfg.get("frames")
        if isinstance(
            cfg.get("frames"),
            dict,
        )
        else {}
    )

    registry = (
        cfg.get("strategy_registry")
        if isinstance(
            cfg.get("strategy_registry"),
            dict,
        )
        else {}
    )

    active = []

    for tf, row in frames.items():
        if not isinstance(row, dict):
            continue

        if not row.get("enabled"):
            continue

        raw = row.get(
            "strategies",
            [],
        )

        if isinstance(raw, str):
            raw = [
                x.strip()
                for x in raw.split("+")
                if x.strip()
            ]

        for name in raw:
            u = str(name).strip().upper()

            if u and u not in active:
                active.append(u)

    if not active:
        fail(
            "NO ACTIVE STRATEGIES"
        )

    generated_roots = [
        (
            ROOT
            / "strategy_lab"
            / "results"
            / "v3"
            / "generated_combinations"
        ),
        (
            ROOT
            / "strategy_lab"
            / "results"
            / "v3"
            / "generated_safe_grammar"
        ),
        (
            ROOT
            / "strategy_lab"
            / "deploy_ready"
        ),
    ]

    for name in active:
        registry_row = registry.get(name)

        engine = ""

        if isinstance(
            registry_row,
            dict,
        ):
            engine = str(
                registry_row.get("engine")
                or ""
            ).strip().upper()

        if engine == "ASSIST_FILE":
            registry_file = str(
                registry_row.get("file")
                or ""
            ).strip()

            if not registry_file:
                fail(
                    "CONFIG ASSIST_FILE registry "
                    "filename missing: "
                    + name
                )

            if (
                "/"
                in registry_file
                or "\\"
                in registry_file
                or ":"
                in registry_file
                or registry_file
                in {".", ".."}
            ):
                fail(
                    "CONFIG ASSIST_FILE registry "
                    "path forbidden: "
                    + registry_file
                )

            if not registry_file.lower().endswith(
                ".py"
            ):
                fail(
                    "CONFIG ASSIST_FILE registry "
                    "file extension invalid: "
                    + registry_file
                )

            filename = registry_file

        else:
            filename = (
                name.lower() + ".py"
            )

        p = STRATEGY_DIR / filename

        validation_path = p

        if not p.is_file():
            if engine != "ASSIST_FILE":
                fail(
                    "CONFIG strategy file missing: "
                    + str(p)
                )

            generated_candidates = [
                root / filename
                for root in generated_roots
            ]

            generated_matches = []

            for root, candidate in zip(
                generated_roots,
                generated_candidates,
            ):
                if not candidate.is_file():
                    continue

                try:
                    resolved_root = root.resolve()
                    resolved_candidate = (
                        candidate.resolve()
                    )

                except Exception as exc:
                    fail(
                        "CONFIG generated strategy "
                        "path resolution failed: "
                        + repr(exc)
                    )

                if (
                    resolved_candidate.parent
                    != resolved_root
                ):
                    fail(
                        "CONFIG generated strategy "
                        "escaped allowed root: "
                        + str(candidate)
                    )

                if (
                    resolved_candidate.name
                    != filename
                ):
                    fail(
                        "CONFIG generated strategy "
                        "basename mismatch: "
                        + str(candidate)
                    )

                generated_matches.append(
                    candidate
                )

            if len(
                generated_matches
            ) != 1:
                fail(
                    "CONFIG generated strategy "
                    "source resolution count="
                    + str(
                        len(
                            generated_matches
                        )
                    )
                    + " for "
                    + filename
                )

            validation_path = (
                generated_matches[0]
            )

        try:
            source = (
                validation_path.read_text(
                    encoding="utf-8-sig",
                    errors="strict",
                )
            )

            compile(
                source,
                str(validation_path),
                "exec",
            )

            tree = ast.parse(
                source,
                filename=str(
                    validation_path
                ),
            )

        except Exception as exc:
            fail(
                "CONFIG strategy validation "
                "source invalid: "
                + str(validation_path)
                + " "
                + repr(exc)
            )

        if not any(
            isinstance(
                n,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
            and n.name == "signal"
            for n in ast.walk(tree)
        ):
            fail(
                f"CONFIG active strategy "
                f"has no signal(): {name}"
            )

    print(
        "CONFIG <-> STRATEGY = PASS"
    )

    print(
        "ACTIVE STRATEGIES =",
        active,
    )

    return cfg, active



# ============================================================
# CONFIG-DRIVEN ARCHITECTURE
# ============================================================

def validate_architecture():
    daemon = (
        ROOT / "assist" / "daemon.py"
    ).read_text(
        encoding="utf-8-sig"
    )

    gate = (
        ROOT / "gate.py"
    ).read_text(
        encoding="utf-8-sig"
    )

    runner = (
        ROOT / "runner.py"
    ).read_text(
        encoding="utf-8-sig"
    )

    daemon_need = [
        "def configured_strategy_names(",
        "def load_strategies(",
    ]

    for marker in daemon_need:
        if marker not in daemon:
            fail(
                "DAEMON config-driven "
                f"marker missing: {marker}"
            )

    if (
        "from assist.strategies import ("
        in daemon
    ):
        fail(
            "DAEMON still static import"
        )

    if "VALID_STRATEGIES = {" in gate:
        fail(
            "GATE still has "
            "VALID_STRATEGIES hardcode"
        )

    gate_need = [
        "def strategy_material_key(",
        "def resolve_management_owner(",
    ]

    for marker in gate_need:
        if marker not in gate:
            fail(
                "GATE config-driven "
                f"marker missing: {marker}"
            )

    runner_need = [
        "def strategy_feature_enabled(",
        "def trail_start_pct(",
        "def trail_callback_pct(",
        "active_owner_tf",
        "active_owner_strategy",
    ]

    for marker in runner_need:
        if marker not in runner:
            fail(
                "RUNNER config-driven "
                f"marker missing: {marker}"
            )

    print(
        "CONFIG-DRIVEN ARCHITECTURE = PASS"
    )


# ============================================================
# EXISTING RESEARCH PIPELINE DISCOVERY
# ============================================================

STAGE_ORDER = [
    "reentry_structure",
    "reentry_material",
    "trailing",
    "exit_source",
    "opposite_reopen",
    "universe_size",
    "robustness",
    "walk_forward",
    "walk_forward_recheck_3",
    "robustness_confirm_u30",
    "original_exact_u30",
]


def discover_stage_scripts():
    """
    experiments/*.py의 실제 argparse
    --stage choices만 읽는다.

    파일 안에 stage 문자열이 단순히
    존재하는 것은 지원 stage로 판단하지 않는다.
    """

    found = {
        stage: []
        for stage in STAGE_ORDER
    }

    if not EXPERIMENTS.is_dir():
        return found

    for p in sorted(
        EXPERIMENTS.glob("*.py")
    ):
        try:
            script_text = p.read_text(
                encoding="utf-8-sig"
            )

            tree = ast.parse(
                script_text,
                filename=str(p),
            )

        except Exception:
            continue

        assigned = {}

        for node in tree.body:

            if isinstance(
                node,
                ast.Assign,
            ):
                for target in node.targets:

                    if isinstance(
                        target,
                        ast.Name,
                    ):
                        assigned[
                            target.id
                        ] = node.value

            elif (
                isinstance(
                    node,
                    ast.AnnAssign,
                )
                and isinstance(
                    node.target,
                    ast.Name,
                )
            ):
                assigned[
                    node.target.id
                ] = node.value

        def resolve_choices(
            node,
            seen=None,
        ):
            if node is None:
                return set()

            if seen is None:
                seen = set()

            if (
                isinstance(
                    node,
                    ast.Constant,
                )
                and isinstance(
                    node.value,
                    str,
                )
            ):
                return {
                    node.value
                }

            if isinstance(
                node,
                (
                    ast.List,
                    ast.Tuple,
                    ast.Set,
                ),
            ):
                out = set()

                for item in node.elts:
                    out.update(
                        resolve_choices(
                            item,
                            seen,
                        )
                    )

                return out

            if isinstance(
                node,
                ast.Dict,
            ):
                out = set()

                for key in node.keys:
                    out.update(
                        resolve_choices(
                            key,
                            seen,
                        )
                    )

                return out

            if isinstance(
                node,
                ast.Name,
            ):
                name = node.id

                if name in seen:
                    return set()

                value = assigned.get(
                    name
                )

                if value is None:
                    return set()

                return resolve_choices(
                    value,
                    seen | {name},
                )

            if isinstance(
                node,
                ast.BinOp,
            ) and isinstance(
                node.op,
                ast.Add,
            ):
                return (
                    resolve_choices(
                        node.left,
                        seen,
                    )
                    |
                    resolve_choices(
                        node.right,
                        seen,
                    )
                )

            if isinstance(
                node,
                ast.Call,
            ):
                func = node.func

                if (
                    isinstance(
                        func,
                        ast.Name,
                    )
                    and func.id
                    in {
                        "list",
                        "tuple",
                        "set",
                        "sorted",
                    }
                    and node.args
                ):
                    return resolve_choices(
                        node.args[0],
                        seen,
                    )

                if (
                    isinstance(
                        func,
                        ast.Attribute,
                    )
                    and func.attr
                    in {
                        "keys",
                        "values",
                    }
                ):
                    return resolve_choices(
                        func.value,
                        seen,
                    )

            return set()

        supported = set()

        for node in ast.walk(tree):

            if not isinstance(
                node,
                ast.Call,
            ):
                continue

            func = node.func

            if not (
                isinstance(
                    func,
                    ast.Attribute,
                )
                and func.attr
                == "add_argument"
            ):
                continue

            is_stage = False

            for arg in node.args:

                if (
                    isinstance(
                        arg,
                        ast.Constant,
                    )
                    and arg.value
                    == "--stage"
                ):
                    is_stage = True
                    break

            if not is_stage:
                continue

            choices_node = None

            for keyword in node.keywords:

                if (
                    keyword.arg
                    == "choices"
                ):
                    choices_node = (
                        keyword.value
                    )
                    break

            supported.update(
                resolve_choices(
                    choices_node
                )
            )

        for stage in STAGE_ORDER:

            if stage in supported:
                found[
                    stage
                ].append(p)

    return found




def choose_stage_script(
    stage,
    scripts,
):
    """
    동일 stage를 가진 과거 snapshot이 여러 개면
    수정시간이 가장 최신인 파일을 사용한다.
    """

    rows = scripts.get(
        stage,
        [],
    )

    if not rows:
        return None

    return max(
        rows,
        key=lambda p: p.stat().st_mtime,
    )


# ============================================================
# RUN EXISTING RESEARCH
# ============================================================

def run_existing_research():
    scripts = (
        discover_stage_scripts()
    )

    print()
    print("=" * 88)
    print("EXISTING RESEARCH PIPELINE")
    print("=" * 88)

    ran = []
    missing = []

    for stage in STAGE_ORDER:
        script = choose_stage_script(
            stage,
            scripts,
        )

        if script is None:
            missing.append(stage)
            continue

        print()
        print(
            f"[EXISTING STAGE] "
            f"{stage}"
        )

        print(
            f"SCRIPT = {script}"
        )

        run(
            [
                PYTHON,
                script,
                "--stage",
                stage,
            ],
            cwd=script.parent,
        )

        ran.append(
            {
                "stage": stage,
                "script": str(script),
            }
        )

    print()
    print(
        "EXISTING STAGES RUN =",
        [
            x["stage"]
            for x in ran
        ],
    )

    print(
        "UNAVAILABLE STAGES =",
        missing,
    )

    return ran, missing


# ============================================================
# HISTORICAL FINAL EVIDENCE
# ============================================================

def find_original_exact_result():
    candidates = [
        PIPE_RESULTS
        / "original_exact_u30_latest.json",
    ]

    candidates += sorted(
        RESULTS.rglob(
            "original_exact_u30_latest.json"
        )
    )

    for p in candidates:
        if p.is_file():
            return p

    return None


def validate_original_exact():
    path = find_original_exact_result()

    if path is None:
        fail(
            "ORIGINAL EXACT latest result "
            "not found"
        )

    data = read_json(path)

    verdict = str(
        data.get(
            "verdict",
            "",
        )
    ).upper()

    parity_ok = bool(
        data.get(
            "parity_ok",
            False,
        )
    )

    hard_ok = bool(
        data.get(
            "hard_safety_ok",
            False,
        )
    )

    if verdict != "PASS":
        fail(
            "ORIGINAL EXACT verdict != PASS"
        )

    if not parity_ok:
        fail(
            "ORIGINAL EXACT parity != PASS"
        )

    if not hard_ok:
        fail(
            "ORIGINAL EXACT safety != PASS"
        )

    print(
        "ORIGINAL EXACT = PASS"
    )

    print(
        "ORIGINAL RESULT =",
        path,
    )

    return path, data


# ============================================================
# FREEZE EVIDENCE
# ============================================================

def validate_candidate_freeze():
    candidates = [
        PIPE_RESULTS
        / (
            "standard_v2_"
            "candidate_freeze_latest.json"
        )
    ]

    candidates += sorted(
        RESULTS.rglob(
            "*candidate_freeze_latest.json"
        )
    )

    found = None

    for p in candidates:
        if p.is_file():
            found = p

    if found is None:
        fail(
            "candidate freeze latest "
            "not found"
        )

    data = read_json(found)

    status = str(
        data.get(
            "status",
            "",
        )
    ).upper()

    if status != (
        "FROZEN_FOR_ORIGINAL_EXACT"
    ):
        fail(
            "candidate freeze status invalid: "
            + status
        )

    print(
        "CANDIDATE FREEZE = PASS"
    )

    print(
        "FREEZE FILE =",
        found,
    )

    return found, data


# ============================================================
# STANDARD VERSION REGISTRY
# ============================================================

def validate_version_registry():
    version_dir = (
        RESULTS / "standard_versions"
    )

    if not version_dir.is_dir():
        fail(
            "standard_versions missing"
        )

    latest = (
        version_dir
        / "standard_versions_latest.json"
    )

    if not latest.is_file():
        fail(
            "standard_versions_latest.json "
            "missing"
        )

    data = read_json(latest)

    champion = data.get(
        "current_historical_champion"
    )

    if not champion:
        fail(
            "current_historical_champion "
            "missing"
        )

    print(
        "STANDARD VERSION REGISTRY = PASS"
    )

    print(
        "CURRENT HISTORICAL CHAMPION =",
        champion,
    )

    return latest, data


# ============================================================
# FINAL LOCAL HASH SET
# ============================================================

def final_file_list():
    files = [
        ROOT / rel
        for rel in CORE_FILES
    ]

    files += sorted(
        p
        for p in STRATEGY_DIR.glob("*.py")
        if not p.name.startswith("_")
    )

    # 중복 제거.
    out = []
    seen = set()

    for p in files:
        key = str(
            p.resolve()
        ).lower()

        if key in seen:
            continue

        seen.add(key)
        out.append(p)

    return out


def freeze_local_final(
    cfg,
    active,
    original_path,
    original_data,
    freeze_path,
    freeze_data,
    registry_path,
    registry_data,
):
    FINAL_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    rows = {}

    for p in final_file_list():
        rel = str(
            p.relative_to(ROOT)
        ).replace("\\", "/")

        rows[rel] = {
            "sha256": sha256(p),
            "bytes": p.stat().st_size,
        }

    candidate = (
        freeze_data.get(
            "source_candidate"
        )
        or
        original_data.get(
            "candidate"
        )
        or
        "UNKNOWN"
    )

    manifest = {
        "schema":
            "LOCAL_FINAL_MANIFEST_V1",

        "created_at_utc":
            utc_now(),

        "source_of_truth":
            str(ROOT),

        "status":
            "PROMOTION_READY",

        "candidate":
            candidate,

        "strategy_config_schema":
            cfg.get("schema"),

        "strategy_config_version":
            cfg.get("version"),

        "active_strategies":
            active,

        "research_evidence": {
            "candidate_freeze":
                str(freeze_path),

            "candidate_freeze_hash":
                freeze_data.get(
                    "freeze_hash"
                ),

            "original_exact":
                str(original_path),

            "original_exact_verdict":
                original_data.get(
                    "verdict"
                ),

            "original_exact_parity_ok":
                original_data.get(
                    "parity_ok"
                ),

            "original_exact_hard_safety_ok":
                original_data.get(
                    "hard_safety_ok"
                ),

            "standard_versions_latest":
                str(registry_path),

            "historical_champion":
                registry_data.get(
                    "current_historical_champion"
                ),
        },

        "files":
            rows,

        "server_live_applied":
            False,
    }

    atomic_json(
        FINAL_MANIFEST,
        manifest,
    )

    print()
    print("=" * 88)
    print("LOCAL FINAL FREEZE")
    print("=" * 88)

    print(
        "CANDIDATE =",
        candidate,
    )

    print(
        "MANIFEST =",
        FINAL_MANIFEST,
    )

    print(
        "FILE COUNT =",
        len(rows),
    )

    print(
        "LOCAL FINAL = PASS"
    )

    return manifest


# ============================================================
# VERIFY FINAL MANIFEST
# ============================================================

def verify_final_manifest():
    if not FINAL_MANIFEST.is_file():
        fail(
            "FINAL manifest missing"
        )

    m = read_json(
        FINAL_MANIFEST
    )

    if (
        m.get("status")
        != "PROMOTION_READY"
    ):
        fail(
            "FINAL status "
            "!= PROMOTION_READY"
        )

    for rel, row in (
        m.get("files") or {}
    ).items():
        p = ROOT / rel

        if not p.is_file():
            fail(
                "FINAL file missing: "
                + rel
            )

        current = sha256(p)

        if current != row.get(
            "sha256"
        ):
            fail(
                "FINAL HASH CHANGED: "
                + rel
            )

    print(
        "LOCAL FINAL HASH VERIFY = PASS"
    )

    return m


# ============================================================
# SERVER READY ARCHIVE
# ============================================================

def build_server_ready_archive():
    m = verify_final_manifest()

    tmp_dir = Path(
        tempfile.mkdtemp(
            prefix="local_final_ready_"
        )
    )

    try:
        for rel in (
            m.get("files") or {}
        ):
            src = ROOT / rel
            dst = tmp_dir / rel

            dst.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            shutil.copy2(
                src,
                dst,
            )

        manifest_dst = (
            tmp_dir
            / "local_final_manifest.json"
        )

        shutil.copy2(
            FINAL_MANIFEST,
            manifest_dst,
        )

        archive = (
            FINAL_DIR
            / "local_final_ready.tar.gz"
        )

        with tarfile.open(
            archive,
            "w:gz",
        ) as tf:
            for p in sorted(
                tmp_dir.rglob("*")
            ):
                if p.is_file():
                    tf.add(
                        p,
                        arcname=str(
                            p.relative_to(
                                tmp_dir
                            )
                        ),
                    )

        print(
            "SERVER READY ARCHIVE =",
            archive,
        )

        return archive

    finally:
        shutil.rmtree(
            tmp_dir,
            ignore_errors=True,
        )


# ============================================================
# SERVER READY SYNC
# ============================================================

def server_ready_sync():
    """
    /tmp READY에만 전송한다.
    live /home/ec2-user/bot 파일은 수정하지 않는다.
    """

    if not SERVER_KEY.is_file():
        fail(
            "SSH key missing: "
            + str(SERVER_KEY)
        )

    archive = (
        build_server_ready_archive()
    )

    run(
        [
            "scp",
            "-i",
            SERVER_KEY,
            archive,
            (
                SERVER_HOST
                + ":"
                + REMOTE_ARCHIVE
            ),
        ],
        cwd=ROOT,
    )

    remote = (
        "set -e; "
        f"rm -rf {REMOTE_READY}; "
        f"mkdir -p {REMOTE_READY}; "
        f"tar -xzf {REMOTE_ARCHIVE} "
        f"-C {REMOTE_READY}; "
        f"cd {REMOTE_READY}; "
        "python3 -m py_compile "
        "runner.py gate.py "
        "assist/daemon.py "
        "assist/strategies/*.py; "
        "echo SERVER_READY_COMPILE=PASS; "
        "sha256sum "
        "runner.py "
        "gate.py "
        "strategy_config.json "
        "assist/daemon.py "
        "assist/strategies/*.py"
    )

    run(
        [
            "ssh",
            "-i",
            SERVER_KEY,
            SERVER_HOST,
            remote,
        ],
        cwd=ROOT,
    )

    print()
    print(
        "SERVER READY SYNC = PASS"
    )

    print(
        "REMOTE READY =",
        REMOTE_READY,
    )

    print(
        "SERVER LIVE = UNCHANGED"
    )


# ============================================================
# CHECK ONLY
# ============================================================

def check_only():
    validate_local_source()
    validate_python_syntax()

    validate_strategy_contract()

    cfg, active = (
        validate_config_strategy_link()
    )

    validate_architecture()

    freeze_path, freeze_data = (
        validate_candidate_freeze()
    )

    original_path, original_data = (
        validate_original_exact()
    )

    registry_path, registry_data = (
        validate_version_registry()
    )

    return (
        cfg,
        active,
        freeze_path,
        freeze_data,
        original_path,
        original_data,
        registry_path,
        registry_data,
    )


# ============================================================
# FINALIZE
# ============================================================

def finalize():
    (
        cfg,
        active,
        freeze_path,
        freeze_data,
        original_path,
        original_data,
        registry_path,
        registry_data,
    ) = check_only()

    return freeze_local_final(
        cfg=cfg,
        active=active,
        original_path=original_path,
        original_data=original_data,
        freeze_path=freeze_path,
        freeze_data=freeze_data,
        registry_path=registry_path,
        registry_data=registry_data,
    )


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=(
            "check",
            "research",
            "finalize",
            "server-ready",
            "all",
        ),
        default="check",
    )

    args = parser.parse_args()

    print("=" * 88)
    print("LOCAL AUTO RESEARCH ORCHESTRATOR")
    print("=" * 88)

    print(
        "ROOT =",
        ROOT,
    )

    print(
        "PYTHON =",
        PYTHON,
    )

    print(
        "MODE =",
        args.mode,
    )

    print(
        "LOCAL = SOURCE OF TRUTH"
    )

    print(
        "SERVER LIVE AUTO DEPLOY = OFF"
    )

    acquire_lock()

    try:
        if args.mode == "check":
            check_only()

        elif args.mode == "research":
            validate_local_source()
            validate_python_syntax()
            validate_strategy_contract()
            validate_config_strategy_link()
            run_existing_research()

        elif args.mode == "finalize":
            finalize()

        elif args.mode == "server-ready":
            server_ready_sync()

        elif args.mode == "all":
            validate_local_source()
            validate_python_syntax()
            validate_strategy_contract()
            validate_config_strategy_link()
            validate_architecture()

            run_existing_research()

            finalize()

            server_ready_sync()

        print()
        print("=" * 88)
        print("AUTO RESEARCH RESULT = PASS")
        print("MODE =", args.mode)
        print("SERVER LIVE = UNCHANGED")
        print("=" * 88)

    finally:
        release_lock()


if __name__ == "__main__":
    main()
