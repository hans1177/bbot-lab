import importlib.util
import sys
from pathlib import Path


path = Path(
    r"C:\my\bottest\strategy_lab\roadmap_manager.py"
)

spec = importlib.util.spec_from_file_location(
    "bbot_roadmap_manager_selftest",
    path,
)

if spec is None or spec.loader is None:
    raise RuntimeError(
        "IMPORT_SPEC_FAIL"
    )

m = importlib.util.module_from_spec(
    spec
)

sys.modules[
    "bbot_roadmap_manager_selftest"
] = m

spec.loader.exec_module(
    m
)


tests = [
    ("R7", "R7"),
    ("R7_CROSS_ENTRY_GATE", "R7"),
    ("r7_cross_entry_gate_freeze_latest.json", "R7"),
    ("R6A_SYMBOL_ENTRY_PRIORITY", "R6A"),
    ("r6a_symbol_entry_priority_freeze_latest.json", "R6A"),
    ("R8_EMA_CROSS_SAR", "R8"),
    ("r8_ema_cross_sar_freeze_latest.json", "R8"),
]


for raw, expected in tests:

    actual = m.normalize_axis(
        raw
    )

    if actual != expected:

        raise RuntimeError(
            f"NORMALIZER_FAIL raw={raw!r} "
            f"expected={expected!r} "
            f"actual={actual!r}"
        )


print(
    "ROADMAP_AXIS_NORMALIZER_SELFTEST=PASS"
)