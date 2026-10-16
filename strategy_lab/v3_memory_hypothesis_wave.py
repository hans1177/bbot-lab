# 파일명: strategy_lab/v3_memory_hypothesis_wave.py
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any
from pathlib import Path

import v3_entry_research_memory as memory
import v3_hypothesis_factory as hf


SCHEMA = "B_BOT_MEMORY_HYPOTHESIS_WAVE_V1"

TF_ORDER = tuple(hf.TF_ORDER)

# Fixed before seeing this wave's results.
# This is a finite next-wave research domain.
MODES = (
    "CHANNEL_Z",
    "TREND_Z",
)

WINDOWS = (
    16,
    20,
    28,
    40,
    56,
)

THRESHOLDS = (
    0.55,
    0.85,
    1.15,
)

CONFIRM_MODES = (
    "BASE",
    "PERSIST_2",
    "PRICE_DIRECTION",
)

WAVE_SIZE_PER_TF = 12

# Research-wave diversity contract.
# Prevent one memory-favored structure from consuming the whole wave.
STRUCTURAL_GROUP_LIMIT = 4
MAX_PER_STRUCTURAL_GROUP = 3


def _name(recipe: dict[str, Any]) -> str:
    th = (
        str(recipe["threshold"])
        .replace(".", "P")
    )

    confirm = str(
        recipe["confirm_mode"]
    ).upper()

    suffix = (
        ""
        if confirm == "BASE"
        else "_M_" + confirm
    )

    return (
        "V3_MEMORY_"
        + str(recipe["mode"]).upper()
        + "_W"
        + str(recipe["window"])
        + "_T"
        + th
        + suffix
    )


def _existing_fingerprints() -> set[str]:
    out = set(
        memory.build_entry_research_memory()
        ["known_semantic_fingerprints"]
    )

    for recipe in hf.research_recipes():
        out.add(
            hf.semantic_recipe_fingerprint(
                recipe
            )
        )

    return out


def _strategy_names(
    tf: str,
    kind: str,
) -> list[str]:
    ctx = memory.hypothesis_context(
        tf,
        strong_limit=25,
        failure_limit=25,
    )

    key = (
        "strong_references"
        if kind == "STRONG"
        else "failure_references"
    )

    out = []

    for row in ctx[key]:
        name = str(
            row.get("strategy") or ""
        ).upper()

        if name:
            out.append(name)

    return out


def _extract_windows(
    names: list[str],
) -> list[int]:
    out = []

    for name in names:
        for raw in re.findall(
            r"(?:^|_)W(\d+)(?:_|$)",
            name,
        ):
            try:
                out.append(int(raw))
            except ValueError:
                pass

    return out


def _extract_thresholds(
    names: list[str],
) -> list[float]:
    out = []

    for name in names:
        for a, b in re.findall(
            r"(?:^|_)T(\d+)P(\d+)(?:_|$)",
            name,
        ):
            try:
                out.append(
                    float(a + "." + b)
                )
            except ValueError:
                pass

    return out


def _mode_score(
    mode: str,
    strong: list[str],
    failed: list[str],
) -> float:

    if mode == "TREND_Z":
        positive = (
            "TREND",
            "EFFICIENCY",
            "MOMENTUM",
            "BREAKOUT",
        )

    else:
        positive = (
            "CHANNEL",
            "MEDIAN",
            "MEAN_REVERSION",
            "RANGE",
        )

    s = sum(
        1
        for name in strong
        for token in positive
        if token in name
    )

    f = sum(
        1
        for name in failed
        for token in positive
        if token in name
    )

    return float(s) - (0.5 * float(f))


def _confirm_score(
    confirm: str,
    strong: list[str],
    failed: list[str],
) -> float:

    if confirm == "BASE":
        return 0.0

    s = sum(
        1
        for name in strong
        if confirm in name
    )

    f = sum(
        1
        for name in failed
        if confirm in name
    )

    return (
        float(s)
        - 0.5 * float(f)
    )


def _distance_score(
    value: float,
    strong_values: list[float],
    failed_values: list[float],
) -> float:

    score = 0.0

    if strong_values:
        nearest = min(
            abs(value - x)
            for x in strong_values
        )

        score += 1.0 / (
            1.0 + nearest
        )

    if failed_values:
        nearest_fail = min(
            abs(value - x)
            for x in failed_values
        )

        score -= (
            0.35
            / (1.0 + nearest_fail)
        )

    return score


def candidate_pool(
    tf: str,
    search_contract: dict[str, Any] | None = None,
    training_frames=None,
) -> list[dict[str, Any]]:

    tf = str(tf).upper()

    if tf not in TF_ORDER:
        raise ValueError(
            "UNSUPPORTED_TIMEFRAME"
        )

    existing = _existing_fingerprints()

    strong = _strategy_names(
        tf,
        "STRONG",
    )

    failed = _strategy_names(
        tf,
        "FAIL",
    )

    strong_windows = _extract_windows(
        strong
    )

    failed_windows = _extract_windows(
        failed
    )

    strong_thresholds = _extract_thresholds(
        strong
    )

    failed_thresholds = _extract_thresholds(
        failed
    )

    rows = []

    for mode in MODES:
        for window in WINDOWS:
            for threshold in THRESHOLDS:
                for confirm in CONFIRM_MODES:

                    recipe = {
                        "mode": mode,
                        "window": window,
                        "threshold":
                            threshold,
                        "confirm_mode":
                            confirm,
                    }

                    fp = (
                        hf.semantic_recipe_fingerprint(
                            recipe
                        )
                    )

                    if fp in existing:
                        continue

                    score = (
                        _mode_score(
                            mode,
                            strong,
                            failed,
                        )
                        + _confirm_score(
                            confirm,
                            strong,
                            failed,
                        )
                        + _distance_score(
                            float(window),
                            [
                                float(x)
                                for x
                                in strong_windows
                            ],
                            [
                                float(x)
                                for x
                                in failed_windows
                            ],
                        )
                        + _distance_score(
                            float(threshold),
                            strong_thresholds,
                            failed_thresholds,
                        )
                    )

                    row = {
                        **recipe,
                        "name": _name(recipe),
                        "timeframe": tf,
                        "semantic_fingerprint":
                            fp,
                        "memory_priority_score":
                            round(score, 12),
                        "research_origin":
                            "MEMORY_DERIVED_BOUNDED_GRAMMAR",
                        "automatic_promotion":
                            False,
                    }

                    rows.append(row)

    rows.sort(
        key=lambda r: (
            -float(
                r[
                    "memory_priority_score"
                ]
            ),
            str(r["mode"]),
            int(r["window"]),
            float(r["threshold"]),
            str(r["confirm_mode"]),
        )
    )

    # 학습 탐색은 공식 검증 이전의 후보 제안 단계로만 실행한다.
    if search_contract is not None:
        import random
        import time
        import numpy as np
        import pandas as pd
        import optuna
        import run_strategy

        contract = dict(search_contract)
        if contract.get("schema") != "B_BOT_CANDIDATE_SEARCH_V1" or contract.get("timeframe") != tf:
            raise ValueError("SEARCH_CONTRACT_INVALID")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(contract.get("run_id") or "")):
            raise ValueError("SEARCH_RUN_ID_REQUIRED")
        limits = {"seed": (0, 2**32 - 1), "population_size": (4, 32),
                  "generations": (1, 16), "optuna_trials": (1, 128),
                  "max_evaluations": (4, 256), "max_seconds": (1, 1800)}
        for key, (lower, upper) in limits.items():
            value = contract.get(key)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("SEARCH_BUDGET_INVALID:" + key)
        if not training_frames or not isinstance(training_frames, dict):
            raise ValueError("TRAINING_DATA_REQUIRED")
        bounds = [pd.Timestamp(contract[key]) for key in (
            "training_start_utc", "training_end_utc", "oos_start_utc", "sealed_start_utc")]
        if any(value.tzinfo is None for value in bounds) or not all(a < b for a, b in zip(bounds, bounds[1:])):
            raise ValueError("TRAINING_WINDOW_OVERLAP_OR_TIMEZONE_MISSING")
        hard_end = pd.Timestamp("2024-08-20T23:59:59Z")
        if bounds[2] > hard_end or bounds[3] <= hard_end:
            raise ValueError("OFFICIAL_BOUNDARY_INVALID")
        fee_rate = contract.get("fee_rate")
        if isinstance(fee_rate, bool) or not isinstance(fee_rate, (int, float)) or not math.isfinite(fee_rate) or fee_rate <= 0:
            raise ValueError("TRAINING_COST_REQUIRED")
        frames = {}
        data_hashes = {}
        for symbol, frame in sorted(training_frames.items()):
            if not isinstance(symbol, str) or not symbol or not isinstance(frame, pd.DataFrame) or frame.empty:
                raise ValueError("TRAINING_FRAME_INVALID")
            columns = ["ts", "open", "high", "low", "close", "volume"]
            if not set(columns).issubset(frame.columns):
                raise ValueError("TRAINING_OHLCV_REQUIRED")
            frame = frame[columns].copy()
            for key in columns:
                frame[key] = pd.to_numeric(frame[key], errors="raise")
            if not np.isfinite(frame.to_numpy(dtype=float)).all() or (frame["close"] <= 0).any():
                raise ValueError("TRAINING_VALUE_INVALID")
            timestamps = frame["ts"].to_numpy(dtype=float)
            if not np.equal(timestamps, np.floor(timestamps)).all() or not np.all(np.diff(timestamps) > 0):
                raise ValueError("TRAINING_TIMESTAMPS_INVALID")
            if timestamps.min() < bounds[0].timestamp() or timestamps.max() > bounds[1].timestamp():
                raise ValueError("NON_TRAINING_CANDLE_REJECTED")
            frames[symbol] = frame.reset_index(drop=True)
            data_hashes[symbol] = hashlib.sha256(pd.util.hash_pandas_object(frames[symbol], index=False).values.tobytes()).hexdigest()
        identity = {"contract": contract, "data_hashes": data_hashes,
                    "pool": [r["semantic_fingerprint"] for r in rows],
                    "optuna": optuna.__version__, "numpy": np.__version__, "pandas": pd.__version__}
        search_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        state_key = "training_search:" + tf
        knowledge, _ = hf.knowledge.ensure()
        prior = (knowledge.get("records") or {}).get(state_key) or {}
        cache = dict(prior.get("scores") or {}) if prior.get("search_hash") == search_hash else {}
        if len(cache) > contract["max_evaluations"]:
            raise RuntimeError("SEARCH_CHECKPOINT_BUDGET_MISMATCH")
        deadline = time.monotonic() + contract["max_seconds"]
        pool_by_genes = {(r["mode"], r["window"], r["threshold"], r["confirm_mode"]): r for r in rows}
        genes = list(pool_by_genes)
        if not genes:
            return []
        rng = random.Random(contract["seed"])
        used = set()

        def evaluate(gene):
            recipe = pool_by_genes[gene]
            fingerprint = recipe["semantic_fingerprint"]
            if time.monotonic() >= deadline:
                raise RuntimeError("TRAINING_SEARCH_TIME_BUDGET_REACHED")
            if fingerprint in cache:
                score = float(cache[fingerprint])
                if not math.isfinite(score):
                    raise RuntimeError("SEARCH_CHECKPOINT_SCORE_INVALID")
                used.add(fingerprint)
                return score
            if len(cache) >= contract["max_evaluations"]:
                return None
            trades = []
            for frame in frames.values():
                signal = hf.generate_signals_recipe(frame, recipe)
                trades.extend(run_strategy.simulate(frame, signal, fee_rate, trailing=None, reentry=None))
            score = float(np.mean([r["net_pct"] for r in trades])) if trades else -1e12
            if not math.isfinite(score):
                raise RuntimeError("TRAINING_SCORE_NONFINITE")
            cache[fingerprint] = score
            used.add(fingerprint)
            knowledge.setdefault("records", {})[state_key] = {
                "search_hash": search_hash, "scores": cache,
                "reference_only": True, "automatic_promotion": False,
                "retention": "LATEST_SEARCH_PER_TIMEFRAME_UNTIL_WAVE_SEAL",
            }
            hf.knowledge.atomic(hf.knowledge.KNOWLEDGE, knowledge)
            return score

        population = rng.sample(genes, min(contract["population_size"], len(genes)))
        for _ in range(contract["generations"]):
            ranked = [(score, gene) for gene in population if (score := evaluate(gene)) is not None]
            if not ranked:
                break
            ranked.sort(key=lambda item: (-item[0], item[1]))
            parents = [gene for _, gene in ranked[:max(2, len(ranked) // 2)]]
            population = list(parents)
            while len(population) < contract["population_size"]:
                a, b = rng.choice(parents), rng.choice(parents)
                child = [a[index] if rng.random() < 0.5 else b[index] for index in range(4)]
                index = rng.randrange(4)
                child[index] = rng.choice(genes)[index]
                child = tuple(child)
                population.append(child if child in pool_by_genes else rng.choice(genes))
        ranked = sorted((gene for gene in genes if pool_by_genes[gene]["semantic_fingerprint"] in used),
                        key=lambda gene: (-cache[pool_by_genes[gene]["semantic_fingerprint"]], gene))
        structures = list(dict.fromkeys((gene[0], gene[3]) for gene in ranked))[:STRUCTURAL_GROUP_LIMIT]
        studies = [optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=(contract["seed"] + index) % 2**32, n_startup_trials=2))
                   for index in range(len(structures))]
        for index in range(contract["optuna_trials"]):
            if not structures:
                break
            structure_index = index % len(structures)
            mode, confirm = structures[structure_index]
            study = studies[structure_index]
            trial = study.ask()
            window = trial.suggest_categorical("window", list(WINDOWS))
            threshold = trial.suggest_categorical("threshold", list(THRESHOLDS))
            gene = (mode, window, threshold, confirm)
            if gene not in pool_by_genes:
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
                continue
            score = evaluate(gene)
            if score is None:
                study.tell(trial, state=optuna.trial.TrialState.PRUNED)
            else:
                study.tell(trial, score)
        selected_rows = []
        for recipe in rows:
            fingerprint = recipe["semantic_fingerprint"]
            if fingerprint not in used:
                continue
            selected_rows.append({**recipe, "name": hf.recipe_name(recipe),
                                  "memory_priority_score": cache[fingerprint],
                                  "research_origin": "GENETIC_OPTUNA_TRAINING_ONLY",
                                  "search_contract_hash": search_hash,
                                  "search_evaluations": len(cache), "decision": "HYPOTHESIS_ONLY"})
        selected_rows.sort(key=lambda row: (-row["memory_priority_score"], row["semantic_fingerprint"]))
        return selected_rows

    return rows


def _structural_group(
    row: dict[str, Any],
) -> tuple[str, str]:

    return (
        str(row["mode"]).upper(),
        str(row["confirm_mode"]).upper(),
    )


def _diversified_selection(
    pool: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Deterministic bounded selection.

    Memory score chooses the best structural groups and the best
    candidates inside them, but no single structure may monopolize
    the entire research wave.
    """

    grouped: dict[
        tuple[str, str],
        list[dict[str, Any]]
    ] = {}

    for row in pool:
        key = _structural_group(row)
        grouped.setdefault(key, []).append(row)

    # candidate_pool() is already deterministically score-sorted.
    ranked_groups = sorted(
        grouped,
        key=lambda key: (
            -float(
                grouped[key][0][
                    "memory_priority_score"
                ]
            ),
            key[0],
            key[1],
        ),
    )

    active_groups = ranked_groups[
        :STRUCTURAL_GROUP_LIMIT
    ]

    selected = []

    # Round-robin avoids a single parameter neighborhood taking all
    # slots even inside the chosen structural groups.
    for depth in range(
        MAX_PER_STRUCTURAL_GROUP
    ):
        for key in active_groups:

            rows = grouped[key]

            if depth >= len(rows):
                continue

            selected.append(
                rows[depth]
            )

            if len(selected) >= WAVE_SIZE_PER_TF:
                return selected

    # Fail-closed deterministic fallback only if a group unexpectedly
    # has fewer candidates. No result-driven mutation occurs here.
    used = {
        (
            row["timeframe"],
            row["semantic_fingerprint"],
        )
        for row in selected
    }

    for row in pool:

        key = (
            row["timeframe"],
            row["semantic_fingerprint"],
        )

        if key in used:
            continue
        group = _structural_group(row)
        if group not in active_groups:
            continue
        if sum(_structural_group(item) == group for item in selected) >= MAX_PER_STRUCTURAL_GROUP:
            continue

        selected.append(row)
        used.add(key)

        if len(selected) >= WAVE_SIZE_PER_TF:
            break

    return selected


def wave(
    tf: str,
    search_contract: dict[str, Any] | None = None,
    training_frames=None,
) -> dict[str, Any]:

    tf = str(tf).upper()

    if search_contract is not None:
        import pandas as pd
        import v3_entry_hypothesis_wave_seal as seal

        contract = dict(search_contract)
        if contract.get("schema") != "B_BOT_CANDIDATE_SEARCH_V1" or contract.get("timeframe") != tf:
            raise ValueError("SEARCH_CONTRACT_INVALID")
        contract_hash = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        state_key = "candidate_search_wave:" + tf
        knowledge, _ = hf.knowledge.ensure()
        previous = (knowledge.get("records") or {}).get(state_key) or {}
        if previous.get("run_id") == contract.get("run_id"):
            if previous.get("contract_hash") != contract_hash:
                raise RuntimeError("SEALED_SEARCH_CONTRACT_MUTATION_FORBIDDEN")
            sealed_path = (hf.ROOT / str(previous["manifest_file"])).resolve()
            sealed_path.relative_to(hf.ROOT.resolve())
            result = json.loads(sealed_path.read_text(encoding="utf-8"))
            payload = {key: value for key, value in result.items() if key not in {"wave_id", "wave_hash", "created_at_utc"}}
            if seal.sha256_json(payload) != previous.get("wave_hash") or result.get("wave_hash") != previous.get("wave_hash"):
                raise RuntimeError("SEALED_SEARCH_MANIFEST_HASH_MISMATCH")
            for candidate in result["candidates"]:
                source = (hf.ROOT / candidate["canonical_source_file"]).resolve()
                source.relative_to(hf.ROOT.resolve())
                if hashlib.sha256(source.read_bytes()).hexdigest() != candidate["source_sha256"]:
                    raise RuntimeError("SEALED_CANDIDATE_SOURCE_CHANGED")
            return result
        if training_frames is None:
            training_frames = {}
            files = contract.get("training_files")
            if not isinstance(files, list) or not files:
                raise ValueError("TRAINING_MANIFEST_REQUIRED")
            for row in files:
                if row.get("role") != "TRAINING_ONLY" or row.get("timeframe") != tf:
                    raise ValueError("TRAINING_DATASET_ROLE_INVALID")
                relative = Path(str(row.get("path") or ""))
                path = (hf.ROOT / relative).resolve()
                path.relative_to((hf.ROOT / "data" / "candles").resolve())
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row.get("sha256"):
                    raise RuntimeError("TRAINING_DATASET_HASH_MISMATCH")
                symbol = str(row.get("symbol") or "").upper()
                if not symbol or symbol in training_frames:
                    raise ValueError("TRAINING_SYMBOL_DUPLICATE_OR_EMPTY")
                if path.suffix == ".csv":
                    training_frames[symbol] = pd.read_csv(path)
                elif path.suffix == ".parquet":
                    training_frames[symbol] = pd.read_parquet(path)
                else:
                    raise ValueError("TRAINING_FORMAT_NOT_SUPPORTED")
        pool = candidate_pool(tf, contract, training_frames)
        selected = _diversified_selection(pool)
        if not selected:
            raise RuntimeError("TRAINING_SEARCH_NO_CANDIDATES")
        for candidate in selected:
            source, _ = hf.module_for(candidate, tf)
            candidate["canonical_source_file"] = str(source.relative_to(hf.ROOT)).replace(chr(92), "/")
            candidate["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        result = seal.make_manifest(
            candidates=selected,
            memory_reference={"source": "TRAINING_ONLY", "contract_hash": contract_hash,
                              "run_id": contract["run_id"], "reference_only": True},
            provenance={"source": "GENETIC_OPTUNA_BOUNDED_PROPOSAL", "contract": contract,
                        "official_validation_executed": False, "automatic_promotion": False,
                        "training_evaluations": max(row["search_evaluations"] for row in selected)},
        )
        sealed_path = seal.seal_create_only(result, hf.V3 / "entry_hypothesis_waves")
        knowledge, _ = hf.knowledge.ensure()
        knowledge.setdefault("records", {})[state_key] = {
            "run_id": contract["run_id"], "contract_hash": contract_hash,
            "manifest_file": str(sealed_path.relative_to(hf.ROOT)).replace(chr(92), "/"),
            "wave_hash": result["wave_hash"], "reference_only": True,
            "automatic_promotion": False,
        }
        knowledge["records"].pop("training_search:" + tf, None)
        hf.knowledge.atomic(hf.knowledge.KNOWLEDGE, knowledge)
        return result

    pool = candidate_pool(tf)

    selected = _diversified_selection(
        pool
    )

    fingerprints = [
        r["semantic_fingerprint"]
        for r in selected
    ]

    if len(fingerprints) != len(
        set(fingerprints)
    ):
        raise RuntimeError(
            "WAVE_FINGERPRINT_DUPLICATE"
        )

    canonical = json.dumps(
        selected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    wave_id = hashlib.sha256(
        canonical
    ).hexdigest()[:16]

    return {
        "schema": SCHEMA,
        "timeframe": tf,
        "wave_id":
            "MW_" + wave_id,
        "candidate_domain_fixed":
            True,
        "result_driven_same_wave_mutation":
            False,
        "candidate_count":
            len(selected),
        "pool_count":
            len(pool),
        "diversity_contract": {
            "structural_group":
                "MODE_X_CONFIRM_MODE",
            "structural_group_limit":
                STRUCTURAL_GROUP_LIMIT,
            "max_per_structural_group":
                MAX_PER_STRUCTURAL_GROUP,
            "selection":
                "DETERMINISTIC_MEMORY_SCORE_ROUND_ROBIN",
        },
        "existing_fingerprint_count":
            len(
                _existing_fingerprints()
            ),
        "candidates":
            selected,
    }


def all_waves() -> dict[str, Any]:

    waves = {
        tf: wave(tf)
        for tf in TF_ORDER
    }

    all_fp = []

    for item in waves.values():
        all_fp.extend(
            row[
                "semantic_fingerprint"
            ]
            for row
            in item["candidates"]
        )

    return {
        "schema": SCHEMA,
        "wave_protocol":
            "FIXED_BOUNDED_BEFORE_RESULTS",
        "timeframes":
            list(TF_ORDER),
        "wave_size_per_tf":
            WAVE_SIZE_PER_TF,
        "total_candidate_count":
            sum(
                x["candidate_count"]
                for x in waves.values()
            ),
        "cross_tf_fingerprint_reuse":
            len(all_fp)
            != len(set(all_fp)),
        "waves":
            waves,
        "research_execution":
            False,
        "automatic_promotion":
            False,
    }


if __name__ == "__main__":

    result = all_waves()

    print(
        "SCHEMA=",
        result["schema"],
    )

    print(
        "TOTAL_CANDIDATES=",
        result[
            "total_candidate_count"
        ],
    )

    print(
        "WAVE_SIZE_PER_TF=",
        result[
            "wave_size_per_tf"
        ],
    )

    for tf in TF_ORDER:
        w = result["waves"][tf]

        print(
            "\nTF=",
            tf,
            "WAVE_ID=",
            w["wave_id"],
            "COUNT=",
            w["candidate_count"],
            "POOL=",
            w["pool_count"],
        )

        for row in w["candidates"]:
            print(
                " ",
                row["name"],
                "SCORE=",
                row[
                    "memory_priority_score"
                ],
                "FP=",
                row[
                    "semantic_fingerprint"
                ][:12],
            )

    print(
        "\nRESEARCH_EXECUTION=NO"
    )
    print(
        "KNOWLEDGE_WRITE=NO"
    )
    print(
        "ORDER_WRITE=NO"
    )
    print(
        "POSITION_CHANGE=NO"
    )
    print(
        "FROZEN_CORE_MODIFIED=NO"
    )
    print(
        "SERVER_RELEASE=NO"
    )
