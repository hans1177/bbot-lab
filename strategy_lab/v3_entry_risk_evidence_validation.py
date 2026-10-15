from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import os


ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "strategy_lab"
V3 = LAB / "results" / "v3"

CALIBRATION = (
    LAB
    / "v3_entry_risk_calibration.py"
)

HISTORICAL = (
    V3
    / "entry_risk_historical_pit_validation.json"
)

OUT = (
    V3
    / "entry_risk_evidence_validation.json"
)

SCHEMA = (
    "B_BOT_V3_ENTRY_RISK_EVIDENCE_VALIDATION_V1"
)


def load_module(path, name):

    spec = (
        importlib.util
        .spec_from_file_location(
            name,
            path,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "MODULE_LOAD_FAILED"
        )

    module = (
        importlib.util
        .module_from_spec(
            spec
        )
    )

    spec.loader.exec_module(
        module
    )

    return module


def validate():

    cal = load_module(
        CALIBRATION,
        "entry_risk_validation_cal",
    )

    calibration = (
        cal.measure()
    )

    historical = {}

    if HISTORICAL.is_file():
        historical = json.loads(
            HISTORICAL.read_text(
                encoding="utf-8-sig"
            )
        )

    usable_forward = int(
        calibration.get(
            "paired_samples"
        )
        or 0
    )

    minimum = int(
        calibration.get(
            "minimum_paired_samples"
        )
        or 20
    )

    reasons = []

    if usable_forward < minimum:
        reasons.append(
            "USABLE_FORWARD_PAIRS_BELOW_MINIMUM"
        )

    if historical.get(
        "retrospective_only"
    ) is not False:
        reasons.append(
            "RETROSPECTIVE_ONLY_EVIDENCE"
        )

    if historical.get(
        "independent_validation"
    ) is not True:
        reasons.append(
            "NO_INDEPENDENT_VALIDATION"
        )

    if (
        historical.get(
            "stop_evidence_status"
        )
        != "AVAILABLE"
    ):
        reasons.append(
            "STOP_HIT_EVIDENCE_UNAVAILABLE"
        )

    result = {
        "schema":
            SCHEMA,

        "status":
            "INSUFFICIENT_EVIDENCE",

        "validation_stage":
            "EVIDENCE_VALIDATION",

        "independent_validation":
            False,

        "retrospective_only":
            True,

        "policy_candidate_allowed":
            False,

        "live_effect":
            False,

        "policy_effect":
            "NONE",

        "usable_forward_paired_samples":
            usable_forward,

        "minimum_forward_paired_samples":
            minimum,

        "historical_pit_pairs":
            int(
                historical.get(
                    "paired_samples"
                )
                or 0
            ),

        "historical_adverse_corr":
            historical.get(
                "adverse_corr"
            ),

        "historical_adverse_mae_error":
            historical.get(
                "adverse_mae_error"
            ),

        "stop_evidence_status":
            historical.get(
                "stop_evidence_status"
            ),

        "stop_prediction_validated":
            False,

        "adverse_prediction_independently_validated":
            False,

        "forward_evidence_required":
            True,

        "reasons":
            reasons,

        "next_required_stage":
            "FORWARD_SHADOW_EVIDENCE_ACCUMULATION",

        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    return result


def run():

    result = validate()

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = OUT.with_suffix(
        ".json.tmp"
    )

    tmp.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    os.replace(
        tmp,
        OUT,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    run()
