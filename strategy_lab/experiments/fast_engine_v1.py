# file: fast_engine_v1.py
# FAST EXPERIMENT ENGINE V2
# STANDARD source files are never modified.
# Decision semantics stay unchanged; only pure/repeated helper work is cached or inlined.

from __future__ import annotations

import ast
import inspect
import textwrap

import numpy as np

BUILD_ID = "FAST_V2_FIXED_20260826_A"


class _FastPatchTransformer(ast.NodeTransformer):
    """Remove diagnostic-only work inside simulate_live_multiframe()."""

    def __init__(self):
        super().__init__()
        self.pre_guard_removed = 0
        self.risk_event_appends_removed = 0
        self.liq_diagnostics_disabled = 0

    @staticmethod
    def _is_name(node, name):
        return isinstance(node, ast.Name) and node.id == name

    @staticmethod
    def _is_call_name(node, name):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        )

    def visit_Assign(self, node):
        node = self.generic_visit(node)
        if (
            len(node.targets) == 1
            and self._is_name(node.targets[0], "pre_guard_eval")
            and self._is_call_name(node.value, "_lab_cross_guard_eval")
        ):
            self.pre_guard_removed += 1
            return ast.copy_location(
                ast.Assign(
                    targets=[ast.Name(id="pre_guard_eval", ctx=ast.Store())],
                    value=ast.Constant(value=None),
                ),
                node,
            )
        return node

    def visit_Expr(self, node):
        node = self.generic_visit(node)
        call = node.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "append"
            and self._is_name(call.func.value, "risk_guard_events")
        ):
            self.risk_event_appends_removed += 1
            return ast.copy_location(ast.Pass(), node)
        return node

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if self._is_call_name(node, "liquidate_cross_until_safe"):
            for kw in node.keywords:
                if kw.arg == "diagnostics" and self._is_name(kw.value, "liq_diag_rows"):
                    kw.value = ast.Constant(value=None)
                    self.liq_diagnostics_disabled += 1
        return node


class FastEngineV2:
    """
    Exact-equivalent research accelerator.

    V1 optimizations:
      - remove diagnostic-only duplicate Cross Guard evaluation
      - remove risk_guard_events payload construction
      - disable liquidation diagnostic payloads
      - cache flattened leverage brackets
      - cache historical Cross stress scenario selection

    V2 optimizations:
      - precompile maintenance brackets (floor/cap/mmr/cum) once per symbol
      - faster pure get_mark path using cached per-symbol market dict
      - inline _cross_trigger_price binary-search gap calculation so close-side
        uPnL/maintenance are computed once instead of 37 times

    The actual strategy, Entry Gate, Control SL, liquidation rules, trailing,
    STATE reentry, arithmetic formulas, and 36-step trigger search are unchanged.
    """

    EXPECTED_PATCH_COUNTS = {
        "pre_guard_removed": 1,
        "risk_event_appends_removed": 3,
        "liq_diagnostics_disabled": 1,
    }

    def __init__(self, module):
        self.m = module

        self._original_flatten = module.flatten_brackets
        self._original_scenarios = module._lab_cross_guard_scenarios
        self._original_get_mark = module.get_mark
        self._original_maintenance = module.maintenance_margin
        self._original_cross_trigger = module._cross_trigger_price

        self._bracket_cache = {}
        self._maintenance_cache = {}
        self._scenario_cache = {}
        self._market_symbol_cache = {}

        self.patch_stats = {}
        self.simulator = self._build_simulator()

    def clear_caches(self):
        # Read-only data caches may safely persist through one simulation only.
        # Clear between runs to avoid stale objects if caller supplies new data.
        self._bracket_cache.clear()
        self._maintenance_cache.clear()
        self._scenario_cache.clear()
        self._market_symbol_cache.clear()

    # ------------------------------------------------------------------
    # Exact-equivalent helper caches
    # ------------------------------------------------------------------

    def _cached_flatten_brackets(self, snapshot, symbol):
        symbol = str(symbol).upper()
        key = (id(snapshot), symbol)
        out = self._bracket_cache.get(key)
        if out is None:
            out = self._original_flatten(snapshot, symbol)
            self._bracket_cache[key] = out
        return out

    def _compiled_maintenance_rows(self, snapshot, symbol):
        symbol = str(symbol).upper()
        key = (id(snapshot), symbol)
        rows = self._maintenance_cache.get(key)
        if rows is not None:
            return rows

        brackets = self._cached_flatten_brackets(snapshot, symbol)
        compiled = []
        for b in brackets:
            floor = self.m.safe_float(b.get("notionalFloor"), 0.0)
            cap = self.m.safe_float(b.get("notionalCap"), np.inf)
            mmr = self.m.safe_float(b.get("maintMarginRatio"), 0.0)
            cum = self.m.safe_float(b.get("cum"), 0.0)
            compiled.append((float(floor), float(cap), float(mmr), float(cum)))

        if not compiled:
            raise RuntimeError(f"empty leverage brackets: {symbol}")

        rows = tuple(compiled)
        self._maintenance_cache[key] = rows
        return rows

    def _fast_maintenance_margin(self, snapshot, symbol, notional):
        n = abs(float(notional))
        rows = self._compiled_maintenance_rows(snapshot, symbol)

        chosen = rows[-1]
        for row in rows:
            floor, cap, _mmr, _cum = row
            if n >= floor and n < cap:
                chosen = row
                break

        _floor, _cap, mmr, cum = chosen
        return max(0.0, n * mmr - cum)

    def _market_bucket(self, market, symbol):
        symbol = str(symbol)
        key = (id(market), symbol)
        bucket = self._market_symbol_cache.get(key)
        if bucket is None:
            bucket = market.get(symbol, {})
            self._market_symbol_cache[key] = bucket
        return bucket

    def _fast_get_mark(self, market, symbol, ts, side=0, stress=False):
        row = self._market_bucket(market, symbol).get(int(ts))
        if row is None:
            return np.nan

        close, low, high = row
        if not stress or side == 0:
            return float(close)
        return float(low if int(side) == 1 else high)

    def _cached_guard_scenarios(self, positions, decision_ts):
        # Exact source only depends on decision time and current symbol/side membership.
        state_key = tuple(
            sorted(
                {
                    (str(p.get("symbol", "")).upper(), int(p.get("side", 0)))
                    for p in positions.values()
                    if int(p.get("side", 0)) in (-1, 1)
                }
            )
        )
        key = (int(decision_ts), state_key)

        frozen = self._scenario_cache.get(key)
        if frozen is not None:
            return list(frozen)

        result = self._original_scenarios(positions, int(decision_ts))
        frozen = tuple(int(x) for x in result)
        self._scenario_cache[key] = frozen
        return list(frozen)

    # ------------------------------------------------------------------
    # Exact-equivalent hot liquidation helper
    # ------------------------------------------------------------------

    def _fast_cross_trigger_price(
        self,
        wallet,
        positions,
        market,
        snapshot,
        ts,
        trade_id,
        base_state,
    ):
        p = positions[trade_id]
        close_mark = float(base_state["marks"].get(trade_id, p["entry_price"]))

        adverse = self._fast_get_mark(
            market,
            p["symbol"],
            ts,
            p["side"],
            stress=True,
        )

        if not np.isfinite(adverse) or adverse <= 0:
            return None

        if int(p["side"]) == 1:
            if adverse >= close_mark:
                return None
        else:
            if adverse <= close_mark:
                return None

        base_gap = float(base_state["margin_balance"] - base_state["maintenance"])
        if base_gap <= 0:
            return close_mark

        # These two values are invariant throughout the 36-step binary search.
        # Original _single_position_gap recomputes both every call.
        close_upnl = self.m.position_upnl(p, close_mark)
        close_mm = self._fast_maintenance_margin(
            snapshot,
            p["symbol"],
            abs(float(p["qty"]) * close_mark),
        )
        base_margin_balance = float(base_state["margin_balance"])
        base_maintenance = float(base_state["maintenance"])

        def gap_at(price):
            price = float(price)
            test_upnl = self.m.position_upnl(p, price)
            test_mm = self._fast_maintenance_margin(
                snapshot,
                p["symbol"],
                abs(float(p["qty"]) * price),
            )
            margin_balance = base_margin_balance + (test_upnl - close_upnl)
            maintenance = base_maintenance + (test_mm - close_mm)
            return float(margin_balance - maintenance)

        adverse_gap = gap_at(adverse)
        if adverse_gap > 0:
            return None

        safe_p = close_mark
        unsafe_p = float(adverse)

        # Keep STANDARD's exact iteration count and branch semantics.
        for _ in range(36):
            mid = (safe_p + unsafe_p) / 2.0
            gap = gap_at(mid)
            if gap > 0:
                safe_p = mid
            else:
                unsafe_p = mid

        return float(unsafe_p)

    # ------------------------------------------------------------------
    # Simulator clone
    # ------------------------------------------------------------------

    def _build_simulator(self):
        src = textwrap.dedent(inspect.getsource(self.m.simulate_live_multiframe))
        tree = ast.parse(src)

        transformer = _FastPatchTransformer()
        tree = transformer.visit(tree)
        ast.fix_missing_locations(tree)

        actual = {
            "pre_guard_removed": transformer.pre_guard_removed,
            "risk_event_appends_removed": transformer.risk_event_appends_removed,
            "liq_diagnostics_disabled": transformer.liq_diagnostics_disabled,
        }
        self.patch_stats = actual

        if actual != self.EXPECTED_PATCH_COUNTS:
            raise RuntimeError(
                "FAST ENGINE V2 PATCH TARGET ERROR: "
                f"expected={self.EXPECTED_PATCH_COUNTS} actual={actual}"
            )

        code = compile(tree, "<fast_engine_v2_simulator>", "exec")
        ns = dict(self.m.__dict__)
        ns["flatten_brackets"] = self._cached_flatten_brackets
        ns["maintenance_margin"] = self._fast_maintenance_margin
        ns["get_mark"] = self._fast_get_mark
        ns["_cross_trigger_price"] = self._fast_cross_trigger_price
        ns["_lab_cross_guard_scenarios"] = self._cached_guard_scenarios

        exec(code, ns, ns)
        fn = ns.get("simulate_live_multiframe")
        if not callable(fn):
            raise RuntimeError("FAST ENGINE V2 simulator clone missing")
        return fn

    def run(self, *args, **kwargs):
        self.clear_caches()

        old_flatten = self.m.flatten_brackets
        old_maintenance = self.m.maintenance_margin
        old_get_mark = self.m.get_mark
        old_scenarios = self.m._lab_cross_guard_scenarios
        old_cross_trigger = self.m._cross_trigger_price

        self.m.flatten_brackets = self._cached_flatten_brackets
        self.m.maintenance_margin = self._fast_maintenance_margin
        self.m.get_mark = self._fast_get_mark
        self.m._lab_cross_guard_scenarios = self._cached_guard_scenarios
        self.m._cross_trigger_price = self._fast_cross_trigger_price

        try:
            return self.simulator(*args, **kwargs)
        finally:
            self.m.flatten_brackets = old_flatten
            self.m.maintenance_margin = old_maintenance
            self.m.get_mark = old_get_mark
            self.m._lab_cross_guard_scenarios = old_scenarios
            self.m._cross_trigger_price = old_cross_trigger


def build_fast_engine(module):
    return FastEngineV2(module)

# Compatibility alias for V2 naming.
def build_fast_engine_v2(module):
    return FastEngineV2(module)
