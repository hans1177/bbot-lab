# TEST1 BOOTSTRAP IMPORTS
from pathlib import Path
import sys
# ?뚯씪紐? test1_multitf_v2.py
# TEST 1 ??SAME-DIRECTION MULTI-TF V2
#
# STANDARD V1 ?먮낯 ?뚯씪? ?섏젙?섏? ?딅뒗??
# leverage_scan.py??simulate_live_multiframe() ?뚯뒪瑜??고??꾩뿉 蹂듭젣????
# FLAT multi-TF ?꾨낫 ?좏깮 釉붾줉 ??1怨노쭔 ?ㅽ뿕?⑹쑝濡?援먯껜?쒕떎.
#
# V2-A:
#   12H/1D ?꾨낫媛 ????媛숈? 諛⑺뼢 -> 1D owner
#
# V2-B:
#   12H/1D ?꾨낫媛 ????媛숈? 諛⑺뼢 -> 12H owner
#
# 諛섎? 諛⑺뼢:
#   湲곗〈 V1泥섎읆 MULTI_TF SKIP
#
# ???꾨젅?꾨쭔 ?꾨낫:
#   湲곗〈 V1怨??숈씪
#
# Entry Gate / Control SL / Mark 1H / Trailing / STATE reentry ??
# ?섎㉧吏 simulator 肄붾뱶??STANDARD ?붿쭊 洹몃?濡??ъ슜?쒕떎.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import strategy_lab.leverage_scan as m

import argparse
import ast
import importlib.util
import inspect
import json
import sys
import textwrap
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(r"C:\my\bottest")
LAB = ROOT / "strategy_lab"
RESULTS = LAB / "results"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ============================================================
# EXACT STANDARD SIGNAL DEFINITION
# ============================================================

def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    mod = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        mod
    )

    return mod


tmpl = load_mod(
    "exact_template_test1_v2",
    LAB / "strategies" / "_TEMPLATE.py",
)

ALL_FRAMES = (
    "6H",
    "12H",
    "1D",
    "1W",
)

FRAMES = (
    "12H",
    "1D",
)

AUX_BY_TF = {
    "6H": "OBV",
    "12H": "SAR",
    "1D": "SAR",
    "1W": "OBV",
}

LEVERAGE = {
    "12H": 10,
    "1D": 15,
}

BASELINE = {
    "name": "STANDARD_V1",
    "bankrupt": 0,
    "final_equity": 188700719.64527103,
    "compound_pct": 1886907.1964527103,
    "realized_mdd_pct": -83.99704085937681,
    "liquidations": 0,
    "entries": 16379,
    "entry_blocks": 111989,
    "control_sl_closes": 2724,
    "trailing_closes": 11812,
    "state_reentries": 13881,
}


def agree(a, b):
    return tmpl.agree(
        a,
        b,
    )


def raw_ema_state(df, tf):
    prefix = str(tf).lower()

    long_col = f"{prefix}_ema_long"
    short_col = f"{prefix}_ema_short"

    if (
        long_col not in df.columns
        or
        short_col not in df.columns
    ):
        raise RuntimeError(
            f"EMA columns missing: {tf}"
        )

    long_v = pd.to_numeric(
        df[long_col],
        errors="coerce",
    ).fillna(
        0.0
    ).to_numpy(
        dtype=float
    )

    short_v = pd.to_numeric(
        df[short_col],
        errors="coerce",
    ).fillna(
        0.0
    ).to_numpy(
        dtype=float
    )

    state = np.zeros(
        len(df),
        dtype=np.int8,
    )

    state[
        (long_v > short_v)
        &
        (long_v > 0.0)
    ] = 1

    state[
        (short_v > long_v)
        &
        (short_v > 0.0)
    ] = -1

    valid_col = f"{prefix}_ema_valid"

    if valid_col in df.columns:
        valid = pd.to_numeric(
            df[valid_col],
            errors="coerce",
        ).fillna(
            0
        ).to_numpy(
            dtype=float
        )

        state[
            valid <= 0
        ] = 0

    return state


def final_b_event(df, tf):
    tf = str(tf).upper()

    ema = tmpl.material_signal(
        df,
        tf,
        "EMA_CROSS",
    )

    aux = tmpl.material_signal(
        df,
        tf,
        AUX_BY_TF[tf],
    )

    return agree(
        ema,
        aux,
    )


def final_b_state(df, tf):
    tf = str(tf).upper()

    ema = raw_ema_state(
        df,
        tf,
    )

    aux = tmpl.material_signal(
        df,
        tf,
        AUX_BY_TF[tf],
    )

    return agree(
        ema,
        aux,
    )


# ============================================================
# PROGRESS HEARTBEAT
# ============================================================

class Heartbeat:
    def __init__(
        self,
        label,
        sec=30,
    ):
        self.label = str(label)
        self.sec = int(sec)
        self.stop_event = threading.Event()
        self.started = None
        self.thread = None

    def __enter__(self):
        self.started = time.time()

        def loop():
            while not self.stop_event.wait(
                self.sec
            ):
                elapsed = int(
                    time.time()
                    -
                    self.started
                )

                h, rem = divmod(
                    elapsed,
                    3600,
                )

                mm, ss = divmod(
                    rem,
                    60,
                )

                print(
                    "[EXPERIMENT][RUNNING] "
                    f"candidate={self.label} "
                    f"elapsed="
                    f"{h:02d}:{mm:02d}:{ss:02d}",
                    flush=True,
                )

        self.thread = threading.Thread(
            target=loop,
            daemon=True,
        )

        self.thread.start()

        print(
            "[EXPERIMENT][START] "
            f"candidate={self.label}",
            flush=True,
        )

        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ):
        self.stop_event.set()

        elapsed = int(
            time.time()
            -
            self.started
        )

        h, rem = divmod(
            elapsed,
            3600,
        )

        mm, ss = divmod(
            rem,
            60,
        )

        print(
            "[EXPERIMENT][END] "
            f"candidate={self.label} "
            f"elapsed="
            f"{h:02d}:{mm:02d}:{ss:02d}",
            flush=True,
        )


# ============================================================
# EXACT ENGINE CLONE + ONE-BLOCK V2 PATCH
# ============================================================

def _is_v1_candidate_guard(node):
    """
    李얜뒗 ?먮낯 AST:
        if len(candidates) != 1:
            multi_tf_skips += 1
            continue
    """

    if not isinstance(
        node,
        ast.If,
    ):
        return False

    test = node.test

    if not isinstance(
        test,
        ast.Compare,
    ):
        return False

    if (
        len(test.ops) != 1
        or
        not isinstance(
            test.ops[0],
            ast.NotEq,
        )
        or
        len(test.comparators) != 1
    ):
        return False

    left = test.left
    right = test.comparators[0]

    if not (
        isinstance(
            left,
            ast.Call,
        )
        and
        isinstance(
            left.func,
            ast.Name,
        )
        and
        left.func.id == "len"
        and
        len(left.args) == 1
        and
        isinstance(
            left.args[0],
            ast.Name,
        )
        and
        left.args[0].id == "candidates"
    ):
        return False

    return (
        isinstance(
            right,
            ast.Constant,
        )
        and
        right.value == 1
    )


def _is_candidate_unpack(node):
    """
    李얜뒗 ?ㅼ쓬 ?먮낯 AST:
        tf, side = candidates[0]
    """

    if not isinstance(
        node,
        ast.Assign,
    ):
        return False

    if len(node.targets) != 1:
        return False

    target = node.targets[0]

    if not (
        isinstance(
            target,
            ast.Tuple,
        )
        and
        len(target.elts) == 2
        and
        all(
            isinstance(x, ast.Name)
            for x in target.elts
        )
        and
        [
            x.id
            for x in target.elts
        ]
        ==
        [
            "tf",
            "side",
        ]
    ):
        return False

    value = node.value

    if not isinstance(
        value,
        ast.Subscript,
    ):
        return False

    return (
        isinstance(
            value.value,
            ast.Name,
        )
        and
        value.value.id == "candidates"
    )


def _find_parent_body_with_guard(tree):
    """
    AST???대뼡 body 由ъ뒪?몄뿉
    V1 guard? 諛붾줈 ?ㅼ쓬 candidate unpack???덈뒗吏 李얜뒗??
    """

    matches = []

    def walk(node):
        for _field, value in ast.iter_fields(
            node
        ):
            if isinstance(
                value,
                list,
            ):
                for i, item in enumerate(
                    value
                ):
                    if (
                        isinstance(
                            item,
                            ast.AST,
                        )
                        and
                        _is_v1_candidate_guard(
                            item
                        )
                    ):
                        if (
                            i + 1
                            < len(value)
                            and
                            _is_candidate_unpack(
                                value[i + 1]
                            )
                        ):
                            matches.append(
                                (
                                    value,
                                    i,
                                    item,
                                    value[i + 1],
                                )
                            )

                    if isinstance(
                        item,
                        ast.AST,
                    ):
                        walk(
                            item
                        )

            elif isinstance(
                value,
                ast.AST,
            ):
                walk(
                    value
                )

    walk(
        tree
    )

    return matches


def build_v2_simulator(owner_tf):
    """
    leverage_scan.py ?뚯씪 ?먯껜???섏젙?섏? ?딅뒗??

    simulate_live_multiframe()瑜?inspect濡??쎄퀬,
    exact V1 FLAT ?꾨낫 釉붾줉 ??怨노쭔 ?고???蹂듭젣蹂몄뿉??諛붽씔??
    """

    owner_tf = str(
        owner_tf
    ).upper()

    if owner_tf not in (
        "12H",
        "1D",
    ):
        raise ValueError(
            "owner_tf must be 12H or 1D"
        )

    src = textwrap.dedent(
        inspect.getsource(
            m.simulate_live_multiframe
        )
    )

    tree = ast.parse(
        src
    )

    matches = (
        _find_parent_body_with_guard(
            tree
        )
    )

    if len(matches) != 1:
        raise RuntimeError(
            "V2 PATCH TARGET ERROR: "
            f"expected=1 actual={len(matches)}"
        )

    _body, _idx, guard, unpack = (
        matches[0]
    )

    lines = src.splitlines()

    start = int(
        guard.lineno
    ) - 1

    end = int(
        unpack.end_lineno
    )

    original_block = "\n".join(
        lines[
            start:end
        ]
    )

    indent = (
        lines[start][
            :len(lines[start])
            -
            len(
                lines[start].lstrip()
            )
        ]
    )

    unit = "    "

    patched_block = "\n".join(
        [
            indent
            + "if len(candidates) == 1:",

            indent
            + unit
            + "tf, side = candidates[0]",

            indent
            + "else:",

            indent
            + unit
            + "_v2_sides = {"
            + "int(x[1]) for x in candidates"
            + "}",

            indent
            + unit
            + "if (",

            indent
            + unit * 2
            + "len(candidates) == 2",

            indent
            + unit * 2
            + "and len(_v2_sides) == 1",

            indent
            + unit
            + "):",

            indent
            + unit * 2
            + "tf = str(__TEST1_OWNER_TF).upper()",

            indent
            + unit * 2
            + "_v2_frames = {"
            + "str(x[0]).upper() for x in candidates"
            + "}",

            indent
            + unit * 2
            + "if tf not in _v2_frames:",

            indent
            + unit * 3
            + "raise RuntimeError("
            + "f'V2 owner missing: {tf} "
            + "candidates={candidates}'"
            + ")",

            indent
            + unit * 2
            + "side = int(candidates[0][1])",

            indent
            + unit
            + "else:",

            indent
            + unit * 2
            + "multi_tf_skips += 1",

            indent
            + unit * 2
            + "continue",
        ]
    )

    patched_lines = (
        lines[:start]
        +
        patched_block.splitlines()
        +
        lines[end:]
    )

    patched_src = "\n".join(
        patched_lines
    ) + "\n"

    # 臾몃쾿 寃利?
    compile(
        patched_src,
        "<test1_v2_simulator>",
        "exec",
    )

    ns = dict(
        m.__dict__
    )

    ns[
        "__TEST1_OWNER_TF"
    ] = owner_tf

    exec(
        compile(
            patched_src,
            "<test1_v2_simulator>",
            "exec",
        ),
        ns,
        ns,
    )

    fn = ns.get(
        "simulate_live_multiframe"
    )

    if not callable(
        fn
    ):
        raise RuntimeError(
            "patched simulator missing"
        )

    print(
        "V2 PATCH = PASS "
        f"owner={owner_tf}",
        flush=True,
    )

    print(
        "V2 PATCH TARGETS = 1",
        flush=True,
    )

    print(
        "V1 BLOCK REMOVED CHARS =",
        len(
            original_block
        ),
        flush=True,
    )

    return fn


# ============================================================
# RESULT HELPERS
# ============================================================

def extract_metrics(
    name,
    r,
):
    stats = r.get(
        "risk_guard_stats",
        {},
    )

    return {
        "name": str(
            name
        ),

        "bankrupt": int(
            r.get(
                "bankrupt",
                0,
            )
        ),

        "final_equity": float(
            r[
                "final_equity"
            ]
        ),

        "compound_pct": float(
            r[
                "compound_pct"
            ]
        ),

        "realized_mdd_pct": float(
            r[
                "realized_mdd_pct"
            ]
        ),

        "liquidations": int(
            r.get(
                "liquidations",
                0,
            )
        ),

        "entries": int(
            r.get(
                "entries",
                r.get(
                    "entry_count",
                    0,
                ),
            )
        ),

        "entry_checks": int(
            stats.get(
                "entry_checks",
                0,
            )
        ),

        "entry_blocks": int(
            stats.get(
                "entry_blocks",
                0,
            )
        ),

        "control_sl_checks": int(
            stats.get(
                "control_sl_checks",
                0,
            )
        ),

        "control_sl_breaches": int(
            stats.get(
                "control_sl_breaches",
                0,
            )
        ),

        "control_sl_closes": int(
            stats.get(
                "control_sl_closes",
                0,
            )
        ),

        "trailing_closes": int(
            r.get(
                "trailing_closes",
                r.get(
                    "trailing_count",
                    0,
                ),
            )
        ),

        "state_reentries": int(
            r.get(
                "state_reentries",
                r.get(
                    "reentries",
                    0,
                ),
            )
        ),

        "multi_tf_skips": int(
            r.get(
                "multi_tf_skips",
                0,
            )
        ),
    }


def print_result(
    row
):
    print()
    print(
        "=" * 100
    )

    print(
        row["name"]
    )

    print(
        "=" * 100
    )

    print(
        "STATUS             =",
        (
            "BANKRUPT"
            if row[
                "bankrupt"
            ]
            else "SURVIVED"
        ),
    )

    print(
        "FINAL EQUITY       =",
        f"${row['final_equity']:,.2f}",
    )

    print(
        "COMPOUND           =",
        f"{row['compound_pct']:+,.2f}%",
    )

    print(
        "MDD                =",
        f"{row['realized_mdd_pct']:+,.4f}%",
    )

    print(
        "LIQUIDATIONS       =",
        row[
            "liquidations"
        ],
    )

    print(
        "ENTRIES            =",
        row[
            "entries"
        ],
    )

    print(
        "ENTRY CHECKS       =",
        row[
            "entry_checks"
        ],
    )

    print(
        "ENTRY BLOCKS       =",
        row[
            "entry_blocks"
        ],
    )

    print(
        "CONTROL SL CLOSES  =",
        row[
            "control_sl_closes"
        ],
    )

    print(
        "TRAILING CLOSES    =",
        row[
            "trailing_closes"
        ],
    )

    print(
        "STATE REENTRIES    =",
        row[
            "state_reentries"
        ],
    )

    print(
        "MULTI TF SKIPS     =",
        row[
            "multi_tf_skips"
        ],
    )

    print(
        "? FINAL vs STD     =",
        (
            f"${row['final_equity'] - BASELINE['final_equity']:,.2f}"
        ),
    )

    print(
        "? MDD vs STD       =",
        (
            f"{row['realized_mdd_pct'] - BASELINE['realized_mdd_pct']:+.4f}pp"
        ),
    )


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=(
            "1D",
            "12H",
            "both",
        ),
        default="both",
        help=(
            "1D=V2-A / "
            "12H=V2-B / "
            "both=怨듯넻 ?곗씠??1??以鍮??????꾨낫 ?쒖감?ㅽ뻾"
        ),
    )

    args = parser.parse_args()

    RESULTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        "=" * 100
    )

    print(
        "TEST 1 ??SAME-DIRECTION MULTI-TF V2"
    )

    print(
        "STANDARD source files are NOT modified"
    )

    print(
        "=" * 100
    )

    # --------------------------------------------------------
    # DATA + EXACT EVENT/STATE BOOK
    # --------------------------------------------------------

    with Heartbeat(
        "PREPARE_DATA_AND_BOOKS"
    ):
        (
            snapshot,
            active_symbols,
            active_count,
        ) = m.load_snapshot(
            m.SNAPSHOT_FILE
        )

        cache = m.load_current_cache(
            active_symbols
        )

        symbols = sorted(
            str(
                x
            ).upper()
            for x in cache[
                "symbol"
            ].dropna().unique()
        )

        if len(
            symbols
        ) != 35:
            raise RuntimeError(
                "expected 35 symbols, "
                f"got {len(symbols)}"
            )

        if int(
            active_count
        ) != 35:
            raise RuntimeError(
                "expected active_count=35, "
                f"got {active_count}"
            )

        print(
            "cache symbols =",
            len(
                symbols
            ),
            "/ active =",
            active_count,
            flush=True,
        )

        print(
            "loading ordinary market base ...",
            flush=True,
        )

        market = (
            m.load_market_bars(
                cache
            )
        )

        print(
            "building EXACT FINAL B EVENT book ...",
            flush=True,
        )

        event_book, event_meta = (
            m.build_research_six_frame_signal_book(
                cache=cache,
                signal_fn=final_b_event,
                frames=ALL_FRAMES,
                data_mode="CACHE_MATERIALS",
            )
        )

        print(
            "building EXACT FINAL B OWNER STATE book ...",
            flush=True,
        )

        state_book, state_meta = (
            m.build_research_six_frame_signal_book(
                cache=cache,
                signal_fn=final_b_state,
                frames=ALL_FRAMES,
                data_mode="CACHE_MATERIALS",
            )
        )

        start_ts = max(
            int(
                pd.Timestamp(
                    "2020-01-01 00:00:00",
                    tz="UTC",
                ).timestamp()
            ),
            int(
                cache[
                    "ts"
                ].min()
            ),
        )

        end_ts = int(
            cache[
                "ts"
            ].max()
        )

    modes = (
        (
            "V2_A_OWNER_1D",
            "1D",
        ),
        (
            "V2_B_OWNER_12H",
            "12H",
        ),
    )

    if args.mode == "1D":
        modes = (
            (
                "V2_A_OWNER_1D",
                "1D",
            ),
        )

    elif args.mode == "12H":
        modes = (
            (
                "V2_B_OWNER_12H",
                "12H",
            ),
        )

    rows = []

    for (
        name,
        owner_tf,
    ) in modes:
        simulator = (
            build_v2_simulator(
                owner_tf
            )
        )

        with Heartbeat(
            name
        ):
            r = simulator(
                signal_book=event_book,
                symbols=symbols,
                snapshot=snapshot,
                market=market,
                start_ts=start_ts,
                end_ts=end_ts,
                leverage_map=LEVERAGE,
                frames=FRAMES,
                active_count=35,
                start_equity=10000.0,
                collect_trade_log=False,
                trailing_start_pct=4.0,
                trailing_callback_pct=2.0,
                state_reentry_book=state_book,
                state_reentry_pullback_pct=0.0,
            )

        row = extract_metrics(
            name,
            r,
        )

        rows.append(
            row
        )

        print_result(
            row
        )

        # 媛??꾨낫 ?앸궇 ??利됱떆 ???
        partial_path = (
            RESULTS
            /
            (
                "test1_multitf_v2_"
                + owner_tf.lower()
                + "_latest.json"
            )
        )

        partial_path.write_text(
            json.dumps(
                {
                    "schema":
                        "TEST1_SAME_DIRECTION_MULTI_TF_V2_V2",

                    "baseline":
                        BASELINE,

                    "result":
                        row,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            "PARTIAL RESULT =",
            partial_path,
            flush=True,
        )

    # --------------------------------------------------------
    # COMBINED SAVE
    # --------------------------------------------------------

    payload = {
        "schema":
            "TEST1_SAME_DIRECTION_MULTI_TF_V2_V2",

        "baseline":
            BASELINE,

        "results":
            rows,
    }

    json_path = (
        RESULTS
        /
        "test1_multitf_v2_latest.json"
    )

    json_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    csv_path = (
        RESULTS
        /
        "test1_multitf_v2_latest.csv"
    )

    pd.DataFrame(
        [
            BASELINE,
            *rows,
        ]
    ).to_csv(
        csv_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(
        "=" * 100
    )

    print(
        "TEST 1 SUMMARY"
    )

    print(
        "=" * 100
    )

    table = pd.DataFrame(
        rows
    )

    if not table.empty:
        cols = [
            "name",
            "bankrupt",
            "final_equity",
            "compound_pct",
            "realized_mdd_pct",
            "liquidations",
            "entries",
            "entry_blocks",
            "control_sl_closes",
            "trailing_closes",
            "state_reentries",
            "multi_tf_skips",
        ]

        print(
            table[
                cols
            ].to_string(
                index=False
            )
        )

    print()
    print(
        "RESULT JSON =",
        json_path,
    )

    print(
        "RESULT CSV  =",
        csv_path,
    )

    print(
        "TEST1_COMPLETE",
        flush=True,
    )


if __name__ == "__main__":
    main()



