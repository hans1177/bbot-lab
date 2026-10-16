# 파일명: r4_performance_tiering.py
"""Fail-closed net-performance tier decisions for the R4 strategy ledger."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

from tier_statistics import confidence_interval_95


TIERS = ("CANARY", "SMALL", "NORMAL")
MIN_CLOSED_TRADES = 2
PROMOTION_RULES = {"CANARY": (10, 3), "SMALL": (20, 5)}
LOSS_STREAK_LIMIT = 3
FRAME_SECONDS = {"30M": 1800, "1H": 3600, "6H": 21600, "12H": 43200, "1D": 86400, "1W": 604800}
QUOTE_ASSET = "USDT"


def _decimal(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"INVALID_{field.upper()}") from exc
    if not parsed.is_finite():
        raise ValueError(f"NONFINITE_{field.upper()}")
    return parsed


def _tier_step(current: str, direction: int) -> str | None:
    index = TIERS.index(current)
    target_index = index + direction
    if target_index < 0 or target_index >= len(TIERS):
        return None
    return TIERS[target_index]


def tier_priority_rank(tier: str, priority_order: list[str] | tuple[str, ...]) -> int:
    normalized = [str(value or "").upper() for value in priority_order]
    normalized_tier = str(tier or "").upper()
    if len(normalized) != len(TIERS) or set(normalized) != set(TIERS):
        raise ValueError("INVALID_TIER_PRIORITY_ORDER")
    try:
        return normalized.index(normalized_tier)
    except ValueError:
        return len(TIERS)


def transition_tier_config_row(
    row: dict,
    target_tier: str,
    tier_weights: dict,
    adaptive_policy_name: str = "",
    adaptive_hard_max: dict | None = None,
) -> dict:
    """Return the next-entry tier weight while preserving every safety flag."""
    current = str(row.get("tier") or "").upper()
    target = str(target_tier or "").upper()
    if target not in {"CANARY": {"SMALL"}, "SMALL": {"CANARY", "NORMAL"}, "NORMAL": {"SMALL"}}.get(current, set()):
        raise ValueError(f"INVALID_TIER_STEP:{current}->{target}")
    weights = tier_weights if isinstance(tier_weights, dict) else {}
    try:
        old_base = float(weights[current])
        new_base = float(weights[target])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("TIER_WEIGHT_MISSING") from exc
    if old_base <= 0.0 or new_base <= 0.0:
        raise ValueError("TIER_WEIGHT_INVALID")

    updated = dict(row)
    if adaptive_policy_name and str(row.get("adaptive_capital_boost_policy") or "") == adaptive_policy_name:
        try:
            old_weight = float(row.get("capital_weight") or 0.0)
            effective_ratio = old_weight / old_base
            target_hard_max = float((adaptive_hard_max or {}).get(target) or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError("ADAPTIVE_CAPITAL_METADATA_INVALID") from exc
        if effective_ratio <= 0.0 or target_hard_max <= 0.0:
            raise ValueError("ADAPTIVE_CAPITAL_METADATA_INVALID")
        updated["capital_base_weight"] = new_base
        updated["capital_weight"] = new_base * min(effective_ratio, target_hard_max)
    else:
        updated["capital_weight"] = new_base
    updated["tier"] = target
    return updated


def _usdt_value(
    row: dict,
    amount_key: str,
    asset_key: str,
    value_key: str,
    valuation_source_key: str,
) -> Decimal:
    asset = str(row.get(asset_key) or "").upper()
    explicit_value = str(row.get(value_key) or "").strip()
    if explicit_value:
        if asset != QUOTE_ASSET and not row.get(valuation_source_key):
            raise ValueError(f"{valuation_source_key.upper()}_MISSING")
        return _decimal(explicit_value, value_key)
    if asset != QUOTE_ASSET:
        raise ValueError(f"{asset_key.upper()}_VALUATION_MISSING")
    return _decimal(row.get(amount_key), amount_key)


def _complete_leg(item: dict) -> dict:
    leg = item["leg"]
    entries = item["entries"]
    exits = item["exits"]
    evidence = item["evidence"]
    funding_coverage = item.get("funding_coverage")

    if str(leg.get("status") or "").upper() != "CLOSED":
        raise ValueError("LEG_NOT_CLOSED")
    if int(leg.get("owned_qty_units") or 0) != 0:
        raise ValueError("LEG_STILL_OWNS_POSITION")
    entry_units = int(leg.get("entry_qty_units") or 0)
    exit_units = int(leg.get("exit_qty_units") or 0)
    if entry_units <= 0 or entry_units != exit_units:
        raise ValueError("LEDGER_ENTRY_EXIT_QTY_MISMATCH")
    if not leg.get("instance_id") or not leg.get("strategy_version"):
        raise ValueError("EXACT_STRATEGY_IDENTITY_MISSING")
    if not entries:
        raise ValueError("ENTRY_FILL_EVIDENCE_MISSING")
    if not exits:
        raise ValueError("EXIT_FILL_EVIDENCE_MISSING")
    # 파일명: r4_performance_tiering.py / 누적 수량과 실제 체결의 결합을 수량 구간으로 검증한다.
    bound_events=set()
    superseded_events=set()
    for kind,accounting,key in (("ENTRY_FILL",entries,"fill_id"),("EXIT_FILL",exits,"event_key")):
        stubs={}
        actual=[row for row in evidence if row.get("kind")==kind and row.get("trade_id")]
        for fill in accounting:
            identifier=str(fill[key])
            if identifier.startswith("ORDER:") and ":CUM:" in identifier:
                prefix,end_text=identifier.rsplit(":CUM:",1)
                order_id=prefix[len("ORDER:"):]
                end=int(end_text);start=end-int(fill.get("qty_units") or 0)
                if not order_id or start<0 or end<=start:
                    raise ValueError("INVALID_ACCOUNTED_FILL_INTERVAL")
                stubs.setdefault(order_id,[]).append((start,end,identifier))
            else:
                matches=[row for row in actual if str(row.get("source_event_id"))==identifier]
                if len(matches)!=1 or int(matches[0].get("qty_units") or 0)!=int(fill.get("qty_units") or 0):
                    raise ValueError(kind+"_EVIDENCE_KEY_OR_QTY_MISMATCH")
                bound_events.add((kind,str(matches[0]["source_event_id"])))
        for order_id,intervals in stubs.items():
            intervals.sort()
            for previous,current in zip(intervals,intervals[1:]):
                if previous[1]>current[0]:
                    raise ValueError("ACCOUNTED_FILL_INTERVAL_OVERLAP")
            recovered=[]
            for row in actual:
                if str(row.get("order_id"))!=order_id or row.get("attribution_basis")!="ACCOUNTED_CUMULATIVE_INTERVAL":
                    continue
                bound=json.loads(row.get("attribution_evidence_key") or "null")
                if (not isinstance(bound,list) or len(bound)!=3 or str(bound[0])!=order_id
                    or not isinstance(bound[1],int) or not isinstance(bound[2],int)
                    or bound[2]-bound[1]!=int(row.get("qty_units") or 0) or bound[1]<0):
                    raise ValueError("RECOVERED_FILL_INTERVAL_INVALID")
                recovered.append((bound[1],bound[2],str(row.get("source_event_id"))))
            recovered.sort()
            for previous,current in zip(recovered,recovered[1:]):
                if previous[1]>current[0]:
                    raise ValueError("RECOVERED_FILL_INTERVAL_OVERLAP")
            for start,end,identifier in intervals:
                cursor=start
                for lo,hi,_ in recovered:
                    if hi<=cursor or lo>=end:
                        continue
                    if lo>cursor:
                        break
                    cursor=min(end,hi)
                if cursor!=end:
                    raise ValueError("ACCOUNTED_FILL_TRADE_PROOF_INCOMPLETE")
                placeholders=[row for row in evidence if row.get("kind")==kind and str(row.get("source_event_id"))==identifier]
                if len(placeholders)!=1 or placeholders[0].get("trade_id") or placeholders[0].get("hard_fail"):
                    raise ValueError("ACCOUNTED_FILL_REFERENCE_INVALID")
                superseded_events.add((kind,identifier))
            if sum(end-start for start,end,_ in intervals)!=sum(hi-lo for lo,hi,_ in recovered):
                raise ValueError("RECOVERED_FILL_QUANTITY_MISMATCH")
            for lo,hi,identifier in recovered:
                cursor=lo
                for start,end,_ in intervals:
                    if start<=cursor<end:
                        cursor=end
                if cursor<hi:
                    raise ValueError("RECOVERED_FILL_OUTSIDE_ACCOUNTED_INTERVAL")
                bound_events.add((kind,identifier))
        if len(actual)!=len({str(row.get("trade_id")) for row in actual}):
            raise ValueError("DUPLICATE_EXCHANGE_TRADE")
        if any((kind,str(row.get("source_event_id"))) not in bound_events for row in actual):
            raise ValueError("UNBOUND_EXCHANGE_TRADE_EVIDENCE")
    evidence=[row for row in evidence if (str(row.get("kind")),str(row.get("source_event_id"))) not in superseded_events]
    if not funding_coverage or not funding_coverage.get("complete"):
        raise ValueError("FUNDING_COVERAGE_UNVERIFIED")
    if (
        int(funding_coverage.get("coverage_start_ts") or 0) > int(leg.get("created_ts") or 0)
        or int(funding_coverage.get("coverage_end_ts") or 0)
        < max(
            (
                int(row.get("exchange_ts") or 0)
                for row in evidence
                if row.get("kind")=="EXIT_FILL"
            ),
            default=0,
        )
        or not funding_coverage.get("source")
        or not funding_coverage.get("evidence_key")
    ):
        raise ValueError("FUNDING_COVERAGE_DOES_NOT_SPAN_LEG")

    by_source_event = {
        (str(row.get("kind")), str(row.get("source_event_id"))): row
        for row in evidence
    }
    if len(by_source_event) != len(evidence):
        raise ValueError("DUPLICATE_EVIDENCE_SOURCE_EVENT")
    entry_records = []
    exit_records = []
    funding_records = []
    for row in evidence:
        if (
            not row.get("evidence_complete")
            or str(row.get("instance_id")) != str(leg["instance_id"])
            or str(row.get("exact_version")) != str(leg["strategy_version"])
        ):
            raise ValueError("INCOMPLETE_OR_MISATTRIBUTED_EVIDENCE")
        kind = str(row.get("kind") or "")
        if kind in {"ENTRY_FILL", "EXIT_FILL"}:
            if not row.get("trade_id") or not row.get("order_id"):
                raise ValueError("EXCHANGE_TRADE_OR_ORDER_ID_MISSING")
            if not row.get("source") or not row.get("source_event_id"):
                raise ValueError("FILL_SOURCE_MISSING")
            if not row.get("qty_text") or not row.get("price_text"):
                raise ValueError("RAW_FILL_QUANTITY_OR_PRICE_MISSING")
            qty = _decimal(row["qty_text"], "qty_text")
            price = _decimal(row["price_text"], "price_text")
            if qty <= 0 or price <= 0:
                raise ValueError("NONPOSITIVE_FILL_QUANTITY_OR_PRICE")
            fee = _usdt_value(
                row,
                "fee_text",
                "fee_asset",
                "fee_value_usdt_text",
                "fee_valuation_source",
            )
            if fee < 0:
                raise ValueError("NEGATIVE_COMMISSION")
            if kind == "ENTRY_FILL":
                if not row.get("leverage_source") or not row.get("capital_basis_source"):
                    raise ValueError("VERIFIED_CAPITAL_BASIS_MISSING")
                leverage = _decimal(row.get("leverage_x"), "leverage_x")
                if leverage <= 0:
                    raise ValueError("VERIFIED_LEVERAGE_MISSING")
                expected_capital = qty * price / leverage
                capital = _decimal(row.get("capital_basis_usdt"), "capital_basis_usdt")
                if capital <= 0 or abs(capital - expected_capital) > Decimal("1e-12"):
                    raise ValueError("CAPITAL_BASIS_DOES_NOT_MATCH_FILL")
                entry_records.append((row, fee, capital))
            else:
                if not str(row.get("realized_pnl_text") or "").strip():
                    raise ValueError("REALIZED_PNL_MISSING")
                if str(row.get("realized_pnl_asset") or "").upper() != QUOTE_ASSET:
                    raise ValueError("REALIZED_PNL_ASSET_UNVERIFIED")
                exit_records.append((row, fee, _decimal(row["realized_pnl_text"], "realized_pnl_text")))
        elif kind == "FUNDING":
            if not row.get("attribution_basis") or not row.get("source_event_id"):
                raise ValueError("FUNDING_ATTRIBUTION_UNVERIFIED")
            event_ts=int(row.get("exchange_ts") or 0)
            if (
                event_ts<=0
                or event_ts<int(funding_coverage.get("coverage_start_ts") or 0)
                or event_ts>int(funding_coverage.get("coverage_end_ts") or 0)
            ):
                raise ValueError("FUNDING_EVENT_OUTSIDE_VERIFIED_COVERAGE")
            funding = _usdt_value(
                row,
                "funding_text",
                "funding_asset",
                "funding_value_usdt_text",
                "funding_valuation_source",
            )
            funding_records.append(funding)

    if sum(int(row.get("qty_units") or 0) for row,_,_ in entry_records)!=entry_units:
        raise ValueError("ENTRY_TRADE_EVIDENCE_QTY_MISMATCH")
    if sum(int(row.get("qty_units") or 0) for row,_,_ in exit_records)!=exit_units:
        raise ValueError("EXIT_TRADE_EVIDENCE_QTY_MISMATCH")

    if sum(int(row.get("qty_units") or 0) for row in entries) != entry_units:
        raise ValueError("ENTRY_EVIDENCE_QTY_MISMATCH")
    if sum(int(row.get("qty_units") or 0) for row in exits) != exit_units:
        raise ValueError("EXIT_EVIDENCE_QTY_MISMATCH")

    capital_basis = sum((row[2] for row in entry_records), Decimal("0"))
    if capital_basis <= 0:
        raise ValueError("VERIFIED_CAPITAL_BASIS_MISSING")
    realized_pnl = sum((row[2] for row in exit_records), Decimal("0"))
    fees = sum((row[1] for row in entry_records + exit_records), Decimal("0"))
    funding = sum(funding_records, Decimal("0"))
    net_pnl = realized_pnl - fees + funding
    roi_percent = net_pnl / capital_basis * Decimal("100")
    return {
        "leg_id": str(leg["leg_id"]),
        "instance_id": str(leg["instance_id"]),
        "strategy_id": str(leg["strategy_id"]),
        "exact_version": str(leg["strategy_version"]),
        "tier": str(leg["tier"]).upper(),
        "frame": str(leg.get("frame") or "").upper(),
        "entry_ts": int(leg.get("created_ts") or 0),
        "position_side": str(leg.get("position_side") or "").upper(),
        "closed_ts": max(
            int(row.get("exchange_ts") or 0)
            for row in evidence
            if row.get("kind")=="EXIT_FILL"
        ),
        "capital_basis_usdt": str(capital_basis),
        "realized_pnl_usdt": str(realized_pnl),
        "fees_usdt": str(fees),
        "funding_usdt": str(funding),
        "net_pnl_usdt": str(net_pnl),
        "roi_percent": float(roi_percent),
        "hard_fail_count": sum(int(bool(row.get("hard_fail"))) for row in evidence),
        "evidence_keys": sorted(str(row.get("event_key") or "") for row in evidence),
    }


def evaluate_instance(ledger, instance_id: str) -> dict:
    """Evaluate only complete, closed legs opened in the instance's current tier epoch."""
    cohort = ledger.performance_tier_cohort(instance_id)
    instance = cohort["instance"]
    current = str(instance.get("tier") or "").upper()
    if current not in TIERS:
        return {"action": "HOLD", "reason": "INVALID_CURRENT_TIER", "current_tier": current}

    raw_legs = cohort["legs"]
    fingerprint_payload = {
        "instance_id": instance_id,
        "exact_version": instance.get("exact_version"),
        "tier": current,
        "streak_policy": "TIER_NET_STREAK_V2",
        "legs": [
            {
                "leg_id": item["leg"].get("leg_id"),
                "updated_ts": item["leg"].get("updated_ts"),
                "entries": [
                    (row.get("fill_id"), row.get("qty_units"), row.get("ts"))
                    for row in item["entries"]
                ],
                "exits": [
                    (row.get("event_key"), row.get("qty_units"), row.get("ts"))
                    for row in item["exits"]
                ],
                "evidence": [
                    (row.get("event_key"), row.get("payload_hash"), row.get("evidence_complete"))
                    for row in item["evidence"]
                ],
                "funding_coverage": item.get("funding_coverage"),
            }
            for item in raw_legs
        ],
    }
    evidence_key = "PERF_" + hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    if not raw_legs:
        return {
            "action": "HOLD",
            "reason": "FRESH_CLOSED_TRADE_EVIDENCE_REQUIRED",
            "current_tier": current,
            "sample_count": 0,
            "evidence_key": evidence_key,
        }

    try:
        complete_legs = [_complete_leg(item) for item in raw_legs]
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "action": "HOLD",
            "reason": str(exc),
            "current_tier": current,
            "sample_count": len(raw_legs),
            "evidence_key": evidence_key,
        }

    if len(complete_legs) < MIN_CLOSED_TRADES:
        return {
            "action": "HOLD",
            "reason": "INSUFFICIENT_COMPLETED_TRADES",
            "current_tier": current,
            "sample_count": len(complete_legs),
            "minimum_sample_count": MIN_CLOSED_TRADES,
            "evidence_key": evidence_key,
        }

    confidence = confidence_interval_95(
        [row["roi_percent"] for row in complete_legs]
    )
    total_net_pnl = sum(
        (_decimal(row["net_pnl_usdt"], "net_pnl_usdt") for row in complete_legs),
        Decimal("0"),
    )
    hard_fail_count = sum(row["hard_fail_count"] for row in complete_legs)
    lower = confidence["lower"]
    upper = confidence["upper"]
    action = "HOLD"
    target = current
    reason = "CONFIDENCE_INTERVAL_NOT_DECISIVE"

    # 연패는 완료 LEG별로 확인한다. 동일 시각의 손익 혼재는 순서를 추정하지 않는다.
    close_events = {}
    for leg in complete_legs:
        net = _decimal(leg["net_pnl_usdt"], "net_pnl_usdt")
        close_events.setdefault(leg["closed_ts"], []).append(net)
    loss_streak = 0
    loss_order_ambiguous = False
    for ts in sorted(close_events, reverse=True):
        values = close_events[ts]
        if all(value < 0 for value in values):
            loss_streak += len(values)
        else:
            loss_order_ambiguous = any(value < 0 for value in values)
            break

    # 연승은 같은 프레임·방향·진입봉을 한 묶음으로 계산한다.
    groups = {}
    grouping_complete = True
    for leg in complete_legs:
        seconds = FRAME_SECONDS.get(leg["frame"])
        if not seconds or leg["entry_ts"] <= 0 or leg["position_side"] not in {"LONG", "SHORT"}:
            grouping_complete = False
            break
        key = (leg["frame"], leg["position_side"], leg["entry_ts"] // seconds)
        group = groups.setdefault(key, {"closed_ts": 0, "winning": True})
        group["closed_ts"] = max(group["closed_ts"], leg["closed_ts"])
        group["winning"] = group["winning"] and _decimal(leg["net_pnl_usdt"], "net_pnl_usdt") > 0
    win_events = {}
    for group in groups.values():
        ts = group["closed_ts"]
        win_events[ts] = win_events.get(ts, True) and group["winning"]
    win_streak = 0
    for ts in sorted(win_events, reverse=True):
        if not win_events[ts]:
            break
        win_streak += 1

    entry_blocked = False
    if hard_fail_count > 0:
        reason = "HARD_FAILURE_REQUIRES_INDEPENDENT_SAFETY_PATH"
    elif loss_streak >= LOSS_STREAK_LIMIT:
        if current == "CANARY":
            action = "BLOCK"
            entry_blocked = True
            reason = "CANARY_THREE_CONSECUTIVE_NET_LOSSES"
        else:
            action, target = "DEMOTE", _tier_step(current, -1)
            reason = "THREE_CONSECUTIVE_NET_LOSSES"
    elif upper is not None and upper < 0:
        next_tier = _tier_step(current, -1)
        if next_tier:
            action, target = "DEMOTE", next_tier
            reason = "ROI_95CI_UPPER_BELOW_ZERO"
        else:
            reason = "CANARY_FLOOR_REACHED"
    elif lower is not None and lower > 0 and total_net_pnl > 0:
        next_tier = _tier_step(current, 1)
        if next_tier:
            minimum, required_streak = PROMOTION_RULES[current]
            if len(complete_legs) < minimum:
                reason = "PROMOTION_MINIMUM_COMPLETED_TRADES_REQUIRED"
            elif not grouping_complete:
                reason = "PROMOTION_SIGNAL_GROUP_EVIDENCE_REQUIRED"
            elif win_streak < required_streak:
                reason = "PROMOTION_CONSECUTIVE_NET_WINS_REQUIRED"
            else:
                action, target = "PROMOTE", next_tier
                reason = "ROI_95CI_NET_PNL_SAMPLE_AND_STREAK_PASS"
        else:
            reason = "NORMAL_CEILING_REACHED"

    return {
        "action": action,
        "reason": reason,
        "current_tier": current,
        "target_tier": target,
        "sample_count": len(complete_legs),
        "minimum_sample_count": MIN_CLOSED_TRADES,
        "promotion_minimum_trades": PROMOTION_RULES.get(current, (0, 0))[0],
        "promotion_required_streak": PROMOTION_RULES.get(current, (0, 0))[1],
        "win_streak": win_streak,
        "loss_streak": loss_streak,
        "loss_order_ambiguous": loss_order_ambiguous,
        "promotion_grouping_complete": grouping_complete,
        **({"entry_blocked": True} if entry_blocked else {}),
        "total_net_pnl_usdt": str(total_net_pnl),
        "mean_roi_percent": confidence["mean"],
        "roi_95ci_lower": lower,
        "roi_95ci_upper": upper,
        "hard_fail_count": hard_fail_count,
        "legs": complete_legs,
        "evidence_key": evidence_key,
    }
