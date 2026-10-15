# -*- coding: utf-8 -*-

from pathlib import Path

import argparse
import ast
import hashlib
import json
import os
import re
import sys


ROOT = Path(
    os.getenv(
        "BBOT_LOCAL_ROOT",
        r"C:\my\bottest",
    )
).resolve()


LAB = (
    ROOT
    / "strategy_lab"
)


FAMILY_ROOT = (
    LAB
    / "v3_single_families"
).resolve()


RESULTS = (
    LAB
    / "results"
    / "v3"
)


GENERATED_COMBINATIONS = (
    RESULTS
    / "generated_combinations"
).resolve()


GENERATED_SAFE_GRAMMAR = (
    RESULTS
    / "generated_safe_grammar"
).resolve()


PORTABLE_MARKER = (
    "PORTABLE_RUNTIME_ARTIFACT_V1"
)


def sha256(path):

    return hashlib.sha256(
        Path(
            path
        ).read_bytes()
    ).hexdigest()


def within(path, root):

    path = Path(
        path
    ).resolve()

    root = Path(
        root
    ).resolve()

    try:

        path.relative_to(
            root
        )

        return True

    except ValueError:

        return False


def source_allowed(path):

    path = Path(
        path
    ).resolve()

    deploy_ready = (
        ROOT
        / "strategy_lab"
        / "deploy_ready"
    ).resolve()

    validated_single = (
        path.parent == deploy_ready
        and path.suffix.lower() == ".py"
    )

    return (
        within(
            path,
            GENERATED_COMBINATIONS,
        )
        or
        within(
            path,
            GENERATED_SAFE_GRAMMAR,
        )
        or
        validated_single
    )


def portable_guard(text):

    failures = []

    if re.search(
        r"[A-Za-z]:[\\/]",
        text,
    ):
        failures.append(
            "WINDOWS_ABSOLUTE_PATH"
        )

    if re.search(
        r"strategy_lab[\\/]+v3_single_families",
        text,
        flags=re.I,
    ):
        failures.append(
            "RESEARCH_FAMILY_PATH"
        )

    if (
        "spec_from_file_location"
        in text
    ):
        failures.append(
            "FILE_LOCATION_RUNTIME_LOADER"
        )

    if failures:

        raise RuntimeError(
            "NON_PORTABLE_RUNTIME_ARTIFACT="
            + ",".join(
                failures
            )
        )


def combo_meta(tree):

    for node in tree.body:

        if not isinstance(
            node,
            ast.Assign,
        ):
            continue

        if not any(
            isinstance(
                target,
                ast.Name,
            )
            and target.id
            == "STRATEGY_META"
            for target in node.targets
        ):
            continue

        try:

            value = ast.literal_eval(
                node.value
            )

        except Exception as exc:

            raise RuntimeError(
                "STRATEGY_META_NOT_LITERAL"
            ) from exc

        if not isinstance(
            value,
            dict,
        ):
            raise RuntimeError(
                "STRATEGY_META_NOT_OBJECT"
            )

        return value

    raise RuntimeError(
        "STRATEGY_META_MISSING"
    )


def component_literal_path(
    tree,
    variable,
):

    for node in tree.body:

        if not isinstance(
            node,
            ast.Assign,
        ):
            continue

        if not any(
            isinstance(
                target,
                ast.Name,
            )
            and target.id
            == variable
            for target in node.targets
        ):
            continue

        call = node.value

        if not (
            isinstance(
                call,
                ast.Call,
            )
            and
            isinstance(
                call.func,
                ast.Name,
            )
            and
            call.func.id
            == "_load"
            and
            call.args
        ):
            continue

        arg = call.args[0]

        if not (
            isinstance(
                arg,
                ast.Constant,
            )
            and
            isinstance(
                arg.value,
                str,
            )
        ):
            raise RuntimeError(
                "COMPONENT_PATH_NOT_LITERAL="
                + variable
            )

        return arg.value

    raise RuntimeError(
        "COMPONENT_PATH_MISSING="
        + variable
    )


def resolve_family_path(raw):

    p = Path(
        raw
    )

    if not p.is_absolute():

        p = (
            ROOT
            / p
        )

    p = p.resolve()

    if not within(
        p,
        FAMILY_ROOT,
    ):
        raise RuntimeError(
            "COMPONENT_OUTSIDE_FAMILY_ROOT="
            + str(
                p
            )
        )

    if not p.is_file():

        raise RuntimeError(
            "COMPONENT_FILE_MISSING="
            + str(
                p
            )
        )

    return p


def portable_combo_code(
    meta,
    source_a,
    source_b,
):

    name = str(
        meta.get(
            "name"
        )
        or ""
    )

    if not name:

        raise RuntimeError(
            "COMBO_NAME_MISSING"
        )

    module_base = re.sub(
        r"[^A-Za-z0-9_]+",
        "_",
        name.lower(),
    )

    module_a = (
        "_bbot_"
        + module_base
        + "_a"
    )

    module_b = (
        "_bbot_"
        + module_base
        + "_b"
    )

    return f'''# -*- coding: utf-8 -*-
# PORTABLE_RUNTIME_ARTIFACT_V1
# Compiled from a validated generated-combination source.
# Component implementation is embedded; no research path is required at runtime.

import sys
import types
import numpy as np


def _embedded_module(source, name):

    mod = types.ModuleType(name)

    mod.__file__ = (
        "<embedded:"
        + name
        + ">"
    )

    mod.__package__ = ""

    sys.modules[
        name
    ] = mod

    exec(
        compile(
            source,
            mod.__file__,
            "exec",
        ),
        mod.__dict__,
    )

    return mod


_SRC_A = {source_a!r}

_SRC_B = {source_b!r}


A = _embedded_module(
    _SRC_A,
    {module_a!r},
)

B = _embedded_module(
    _SRC_B,
    {module_b!r},
)


STRATEGY_META = {meta!r}


def generate_signals(df, tf):

    x = A.generate_signals(
        df,
        tf,
    )

    y = B.generate_signals(
        df,
        tf,
    )

    out = np.zeros(
        len(df),
        dtype=np.int8,
    )

    out[
        (x > 0)
        & (y > 0)
    ] = 1

    out[
        (x < 0)
        & (y < 0)
    ] = -1

    return out


def signal(df, params=None):

    z = generate_signals(
        df,
        "LIVE",
    )

    v = (
        int(
            z[-1]
        )
        if len(z)
        else 0
    )

    return {{
        "signal":
            "long"
            if v > 0
            else
            "short"
            if v < 0
            else
            "wait",

        "state":
            "long"
            if v > 0
            else
            "short"
            if v < 0
            else
            "wait",

        "strength_long":
            1.0
            if v > 0
            else 0.0,

        "strength_short":
            1.0
            if v < 0
            else 0.0,

        "reason":
            STRATEGY_META[
                "name"
            ],
    }}
'''


def convert_legacy_combo(
    source_path,
    text,
):

    tree = ast.parse(
        text
    )

    meta = combo_meta(
        tree
    )

    raw_a = component_literal_path(
        tree,
        "A",
    )

    raw_b = component_literal_path(
        tree,
        "B",
    )

    path_a = resolve_family_path(
        raw_a
    )

    path_b = resolve_family_path(
        raw_b
    )

    source_a = path_a.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )

    source_b = path_b.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )

    code = portable_combo_code(
        meta,
        source_a,
        source_b,
    )

    return (
        code,
        {
            "mode":
                "LEGACY_COMBINATION_COMPILED",

            "component_a":
                path_a.name,

            "component_b":
                path_b.name,
        },
    )


def portable_mutation_code(
    recipe,
):
    """Build one deterministic standalone bounded-mutation artifact."""

    recipe = dict(
        recipe
    )

    mode = str(
        recipe.get(
            "mode"
        )
        or ""
    ).upper()

    window = int(
        recipe.get(
            "window"
        )
    )

    threshold = float(
        recipe.get(
            "threshold"
        )
    )

    confirm_mode = str(
        recipe.get(
            "confirm_mode"
        )
        or ""
    ).upper()

    name = str(
        recipe.get(
            "name"
        )
        or ""
    )

    parent_name = str(
        recipe.get(
            "parent_name"
        )
        or ""
    )

    fingerprint = str(
        recipe.get(
            "semantic_fingerprint"
        )
        or ""
    )

    mutation_type = str(
        recipe.get(
            "mutation_type"
        )
        or ""
    )

    mutation_id = str(
        recipe.get(
            "mutation_id"
        )
        or ""
    )

    allowed_modes = {
        "TREND_Z",
        "CHANNEL_Z",
    }

    allowed_windows = {
        12,
        18,
        24,
        32,
        48,
        64,
    }

    allowed_thresholds = {
        0.4,
        0.7,
        1.0,
        1.3,
    }

    allowed_confirm_modes = {
        "PRICE_DIRECTION",
        "PERSIST_2",
    }

    if mode not in allowed_modes:
        raise RuntimeError(
            "PORTABLE_MUTATION_MODE_FORBIDDEN"
        )

    if window not in allowed_windows:
        raise RuntimeError(
            "PORTABLE_MUTATION_WINDOW_FORBIDDEN"
        )

    if threshold not in allowed_thresholds:
        raise RuntimeError(
            "PORTABLE_MUTATION_THRESHOLD_FORBIDDEN"
        )

    if confirm_mode not in allowed_confirm_modes:
        raise RuntimeError(
            "PORTABLE_MUTATION_CONFIRM_MODE_FORBIDDEN"
        )

    threshold_token = (
        str(
            threshold
        )
        .replace(
            ".",
            "P",
        )
    )

    expected_parent = (
        "V3_SAFE_"
        + mode
        + "_W"
        + str(window)
        + "_T"
        + threshold_token
    )

    expected_name = (
        expected_parent
        + "_M_"
        + confirm_mode
    )

    if parent_name != expected_parent:
        raise RuntimeError(
            "PORTABLE_MUTATION_PARENT_IDENTITY_INVALID"
        )

    if name != expected_name:
        raise RuntimeError(
            "PORTABLE_MUTATION_NAME_IDENTITY_INVALID"
        )

    if not fingerprint:
        raise RuntimeError(
            "PORTABLE_MUTATION_FINGERPRINT_MISSING"
        )

    if (
        mutation_type
        and mutation_type
        != "STRUCTURAL_CONFIRMATION_FILTER"
    ):
        raise RuntimeError(
            "PORTABLE_MUTATION_TYPE_FORBIDDEN"
        )

    meta = {
        "schema":
            "B_BOT_V3_PORTABLE_MUTATION_SPEC_V1",

        "name":
            name,

        "strategy_kind":
            "BOUNDED_STRUCTURAL_MUTATION",

        "mode":
            mode,

        "window":
            window,

        "threshold":
            threshold,

        "confirm_mode":
            confirm_mode,

        "parent_name":
            parent_name,

        "mutation_type":
            mutation_type,

        "mutation_id":
            mutation_id,

        "semantic_fingerprint":
            fingerprint,

        "bounded":
            True,

        "uses_future_data":
            False,

        "live_mutation":
            False,

        "promotion_eligible":
            False,

        "promotion_status":
            "RESEARCH_ONLY__PORTABILITY_DOES_NOT_IMPLY_PROMOTION",
    }

    return f"""# -*- coding: utf-8 -*-
# PORTABLE_RUNTIME_ARTIFACT_V1
# PORTABLE_GENERATED_MUTATION_V1
# Deterministic bounded structural-mutation artifact.
# Runtime dependency is limited to NumPy and pandas.

import numpy as np
import pandas as pd


STRATEGY_META = {meta!r}
PORTABLE_SPEC = STRATEGY_META


def _num(series):
    return pd.to_numeric(
        series,
        errors="coerce",
    )


def generate_signals(
    df,
    tf,
):
    d = df.copy()

    c = _num(
        d["close"]
    )

    mode = {mode!r}
    window = {window!r}
    threshold = {threshold!r}
    confirm_mode = {confirm_mode!r}

    out = np.zeros(
        len(d),
        dtype=np.int8,
    )

    if mode == "TREND_Z":

        ema = c.ewm(
            span=window,
            adjust=False,
        ).mean()

        vol = (
            c.diff()
            .rolling(
                window,
                min_periods=window,
            )
            .std()
            .replace(
                0,
                np.nan,
            )
        )

        z = (
            c
            - ema
        ) / vol

        slope = (
            ema
            - ema.shift(
                max(
                    2,
                    window // 6,
                )
            )
        )

        long_mask = (
            (z > threshold)
            & (slope > 0)
        )

        short_mask = (
            (z < -threshold)
            & (slope < 0)
        )

    elif mode == "CHANNEL_Z":

        mean = c.rolling(
            window,
            min_periods=window,
        ).mean()

        std = (
            c.rolling(
                window,
                min_periods=window,
            )
            .std()
            .replace(
                0,
                np.nan,
            )
        )

        upper = (
            mean
            + threshold * std
        )

        lower = (
            mean
            - threshold * std
        )

        momentum = (
            c
            - c.shift(
                max(
                    2,
                    window // 8,
                )
            )
        )

        long_mask = (
            (c > upper)
            & (momentum > 0)
        )

        short_mask = (
            (c < lower)
            & (momentum < 0)
        )

    else:
        raise RuntimeError(
            "PORTABLE_MUTATION_MODE_INVALID"
        )

    if confirm_mode == "PRICE_DIRECTION":

        long_mask = (
            long_mask
            & (
                c
                > c.shift(1)
            )
        )

        short_mask = (
            short_mask
            & (
                c
                < c.shift(1)
            )
        )

    elif confirm_mode == "PERSIST_2":

        prior_long = (
            long_mask
            .shift(1)
            .fillna(False)
        )

        prior_short = (
            short_mask
            .shift(1)
            .fillna(False)
        )

        long_mask = (
            long_mask
            & prior_long
        )

        short_mask = (
            short_mask
            & prior_short
        )

    else:
        raise RuntimeError(
            "PORTABLE_MUTATION_CONFIRM_MODE_INVALID"
        )

    out[
        long_mask
        .fillna(False)
        .to_numpy()
    ] = 1

    out[
        short_mask
        .fillna(False)
        .to_numpy()
    ] = -1

    return out


def signal(
    df,
    params=None,
):
    values = generate_signals(
        df,
        "LIVE",
    )

    value = (
        int(
            values[-1]
        )
        if len(values)
        else 0
    )

    return {{
        "signal":
            "long"
            if value > 0
            else
            "short"
            if value < 0
            else
            "wait",

        "state":
            "long"
            if value > 0
            else
            "short"
            if value < 0
            else
            "wait",

        "strength_long":
            1.0
            if value > 0
            else 0.0,

        "strength_short":
            1.0
            if value < 0
            else 0.0,

        "reason":
            STRATEGY_META[
                "name"
            ],
    }}
"""


def package_mutation_recipe(
    recipe,
    output,
):
    """Package one bounded structural mutation without research-source dependency."""

    output = Path(
        output
    ).resolve()

    code = portable_mutation_code(
        recipe
    )

    portable_guard(
        code
    )

    compile(
        code,
        str(output),
        "exec",
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = output.with_suffix(
        output.suffix
        + ".tmp"
    )

    temp.write_text(
        code,
        encoding="utf-8",
        newline="\n",
    )

    os.replace(
        temp,
        output,
    )

    return {
        "schema":
            "B_BOT_V3_PORTABLE_MUTATION_ARTIFACT_V1",

        "strategy":
            str(
                recipe.get(
                    "name"
                )
                or ""
            ),

        "parent_name":
            str(
                recipe.get(
                    "parent_name"
                )
                or ""
            ),

        "semantic_fingerprint":
            str(
                recipe.get(
                    "semantic_fingerprint"
                )
                or ""
            ),

        "runtime_sha256":
            sha256(
                output
            ),

        "output":
            str(
                output
            ),

        "mode":
            "BOUNDED_MUTATION_COMPILE",

        "promotion_eligible":
            False,

        "live_effect":
            False,
    }

def package_strategy(
    source,
    output,
):

    source = Path(
        source
    ).resolve()

    output = Path(
        output
    ).resolve()

    if not source_allowed(
        source
    ):
        raise RuntimeError(
            "PORTABLE_PACKAGE_SOURCE_FORBIDDEN="
            + str(
                source
            )
        )

    if not source.is_file():

        raise RuntimeError(
            "PORTABLE_PACKAGE_SOURCE_MISSING="
            + str(
                source
            )
        )

    before = sha256(
        source
    )

    text = source.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )

    details = {}

    if (
        "PORTABLE_GENERATED_COMBINATION_V1"
        in text
        or
        "PORTABLE_RUNTIME_ARTIFACT_V1"
        in text
    ):

        code = text

        details[
            "mode"
        ] = "ALREADY_PORTABLE"

    elif within(
        source,
        GENERATED_COMBINATIONS,
    ):

        code, details = convert_legacy_combo(
            source,
            text,
        )

    elif within(
        source,
        GENERATED_SAFE_GRAMMAR,
    ):

        code = text

        details[
            "mode"
        ] = "PORTABLE_SAFE_GRAMMAR_COPY"

    elif (
        source.parent
        == (
            ROOT
            / "strategy_lab"
            / "deploy_ready"
        ).resolve()
        and source.suffix.lower() == ".py"
    ):

        # Validated standalone strategy source.
        # The source itself is never uploaded as research-tree payload.
        # strategy_runtime_sync stages only the portable copy under
        # assist/strategies after portable_guard + compile.
        code = text

        details[
            "mode"
        ] = "PORTABLE_VALIDATED_SINGLE_COPY"

    else:

        raise RuntimeError(
            "PORTABLE_PACKAGE_MODE_UNSUPPORTED"
        )

    if (
        "PORTABLE_RUNTIME_ARTIFACT_V1"
        not in code
    ):

        code = (
            "# PORTABLE_RUNTIME_ARTIFACT_V1\n"
            + code
        )

    portable_guard(
        code
    )

    compile(
        code,
        str(
            output
        ),
        "exec",
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = output.with_suffix(
        output.suffix
        + ".tmp"
    )

    temp.write_text(
        code,
        encoding="utf-8",
        newline="\n",
    )

    os.replace(
        temp,
        output,
    )

    after = sha256(
        source
    )

    if after != before:

        raise RuntimeError(
            "VALIDATED_SOURCE_MUTATED_DURING_PACKAGE"
        )

    result = {
        "schema":
            "B_BOT_V3_PORTABLE_RUNTIME_ARTIFACT_V1",

        "source":
            str(
                source
            ),

        "source_sha256":
            before,

        "runtime_sha256":
            sha256(
                output
            ),

        "output":
            str(
                output
            ),

        **details,
    }

    return result


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--source",
        required=True,
    )

    parser.add_argument(
        "--output",
        required=True,
    )

    args = parser.parse_args()

    result = package_strategy(
        args.source,
        args.output,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    print(
        "PORTABLE_RUNTIME_ARTIFACT=PASS"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
