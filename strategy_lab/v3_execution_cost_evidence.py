# filename: v3_execution_cost_evidence.py

"""
GREEN7A_REAL_EXECUTION_COST_V1

Pure execution evidence producer.

Security / evidence rules:

- EXACT_ORDER_ID_BINDING
- PARTIAL_FILL_VWAP
- ACTUAL_COMMISSION_SUM
- No network calls.
- No order placement.
- No synthetic fills.
- No synthetic commission.
- No commission-asset conversion without explicit evidence.
- No slippage PASS without a durable pre-order reference price.
- No cost sanity PASS without validated numeric cost policy.

A producer existing is NOT equivalent to Capital Scale readiness.
"""

from __future__ import annotations

import math


MARKER = (
    "GREEN7A_REAL_EXECUTION_COST_V1"
)

EXACT_ORDER_ID_BINDING = True
PARTIAL_FILL_VWAP = True
ACTUAL_COMMISSION_SUM = True


def _finite(value):
    try:
        x = float(value)
    except Exception:
        return None

    if not math.isfinite(x):
        return None

    return x


def _positive(value):
    x = _finite(value)

    if (
        x is None
        or x <= 0.0
    ):
        return None

    return x


def _nonnegative(value):
    x = _finite(value)

    if (
        x is None
        or x < 0.0
    ):
        return None

    return x


def _text(value):
    return str(
        value
        or ""
    ).strip()


def _order_id(value):
    text = _text(value)

    if not text:
        return ""

    return text


def _trade_qty(row):
    if not isinstance(
        row,
        dict,
    ):
        return None

    for key in (
        "qty",
        "executedQty",
        "quantity",
    ):
        if key in row:
            value = _positive(
                row.get(key)
            )

            if value is not None:
                return value

    return None


def _trade_price(row):
    if not isinstance(
        row,
        dict,
    ):
        return None

    return _positive(
        row.get("price")
    )


def _trade_quote(row, qty, price):
    """
    Prefer exchange-provided quoteQty when present.
    Otherwise price*qty is used from the same actual trade row.
    """

    for key in (
        "quoteQty",
        "quoteQuantity",
    ):
        if key in row:

            quote = _positive(
                row.get(key)
            )

            if quote is not None:
                return quote

    return (
        float(qty)
        * float(price)
    )


def _normalize_side(value):
    side = _text(
        value
    ).upper()

    if side in (
        "BUY",
        "SELL",
    ):
        return side

    return ""


def _validated_cost_policy(
    policy,
):
    """
    Numeric thresholds must come from an explicitly validated
    execution policy. No default threshold is created here.
    """

    if not isinstance(
        policy,
        dict,
    ):
        return None

    if policy.get(
        "validated"
    ) is not True:
        return None

    version = _text(
        policy.get(
            "policy_version"
        )
    )

    if not version:
        return None

    max_fee = _nonnegative(
        policy.get(
            "max_fee_bps"
        )
    )

    max_slippage = _nonnegative(
        policy.get(
            "max_abs_slippage_bps"
        )
    )

    if (
        max_fee is None
        or max_slippage is None
    ):
        return None

    return {
        "policy_version":
            version,

        "max_fee_bps":
            max_fee,

        "max_abs_slippage_bps":
            max_slippage,
    }


def summarize_order_trades(
    trade_rows,
    *,
    order_id,
    symbol=None,
):
    """
    Bind exchange account-trade rows to one exact orderId.

    Multiple rows for the same order are treated as partial fills
    and aggregated into one actual-fill VWAP.
    """

    wanted_order = _order_id(
        order_id
    )

    wanted_symbol = _text(
        symbol
    ).upper()

    if not wanted_order:
        return {
            "schema":
                MARKER,

            "ok":
                False,

            "reason":
                "ORDER_ID_MISSING",

            "exact_order_id_binding":
                False,
        }

    rows = (
        trade_rows
        if isinstance(
            trade_rows,
            list,
        )
        else []
    )

    matched = []

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        if _order_id(
            row.get(
                "orderId"
            )
        ) != wanted_order:
            continue

        row_symbol = _text(
            row.get(
                "symbol"
            )
        ).upper()

        if (
            wanted_symbol
            and row_symbol
            and row_symbol
            != wanted_symbol
        ):
            continue

        matched.append(
            row
        )

    if not matched:
        return {
            "schema":
                MARKER,

            "ok":
                False,

            "reason":
                "EXACT_ORDER_TRADE_ROWS_NOT_FOUND",

            "order_id":
                wanted_order,

            "symbol":
                wanted_symbol,

            "exact_order_id_binding":
                True,

            "matched_trade_count":
                0,
        }

    total_qty = 0.0
    total_quote = 0.0

    commission_by_asset = {}

    side_values = set()
    trade_ids = []
    trade_times = []

    realized_pnl = 0.0
    realized_pnl_complete = True

    for row in matched:

        qty = _trade_qty(
            row
        )

        price = _trade_price(
            row
        )

        if (
            qty is None
            or price is None
        ):
            return {
                "schema":
                    MARKER,

                "ok":
                    False,

                "reason":
                    "ACTUAL_FILL_PRICE_OR_QTY_INVALID",

                "order_id":
                    wanted_order,

                "exact_order_id_binding":
                    True,
            }

        quote = _trade_quote(
            row,
            qty,
            price,
        )

        if (
            quote is None
            or not math.isfinite(
                quote
            )
            or quote <= 0.0
        ):
            return {
                "schema":
                    MARKER,

                "ok":
                    False,

                "reason":
                    "ACTUAL_FILL_QUOTE_INVALID",

                "order_id":
                    wanted_order,

                "exact_order_id_binding":
                    True,
            }

        total_qty += qty
        total_quote += quote

        side = _normalize_side(
            row.get(
                "side"
            )
        )

        if side:
            side_values.add(
                side
            )

        commission = _nonnegative(
            row.get(
                "commission"
            )
        )

        asset = _text(
            row.get(
                "commissionAsset"
            )
        ).upper()

        if commission is None:
            return {
                "schema":
                    MARKER,

                "ok":
                    False,

                "reason":
                    "ACTUAL_COMMISSION_INVALID",

                "order_id":
                    wanted_order,

                "exact_order_id_binding":
                    True,
            }

        if commission > 0.0:

            if not asset:
                return {
                    "schema":
                        MARKER,

                    "ok":
                        False,

                    "reason":
                        "COMMISSION_ASSET_MISSING",

                    "order_id":
                        wanted_order,

                    "exact_order_id_binding":
                        True,
                }

            commission_by_asset[
                asset
            ] = (
                commission_by_asset.get(
                    asset,
                    0.0,
                )
                + commission
            )

        pnl_raw = row.get(
            "realizedPnl"
        )

        if pnl_raw is None:
            realized_pnl_complete = False

        else:
            pnl = _finite(
                pnl_raw
            )

            if pnl is None:
                realized_pnl_complete = False

            else:
                realized_pnl += pnl

        trade_id = row.get(
            "id"
        )

        if trade_id is not None:
            trade_ids.append(
                str(trade_id)
            )

        trade_time = row.get(
            "time"
        )

        if trade_time is not None:
            trade_times.append(
                trade_time
            )

    if (
        total_qty <= 0.0
        or total_quote <= 0.0
    ):
        return {
            "schema":
                MARKER,

            "ok":
                False,

            "reason":
                "ACTUAL_FILL_TOTAL_INVALID",

            "order_id":
                wanted_order,

            "exact_order_id_binding":
                True,
        }

    vwap = (
        total_quote
        / total_qty
    )

    detected_side = (
        next(
            iter(
                side_values
            )
        )
        if len(
            side_values
        ) == 1
        else ""
    )

    return {
        "schema":
            MARKER,

        "ok":
            True,

        "reason":
            "ACTUAL_ORDER_TRADES_BOUND",

        "order_id":
            wanted_order,

        "symbol":
            wanted_symbol,

        "side":
            detected_side,

        "exact_order_id_binding":
            True,

        "partial_fill_vwap":
            True,

        "actual_commission_sum":
            True,

        "matched_trade_count":
            len(
                matched
            ),

        "partial_fill":
            len(
                matched
            ) > 1,

        "actual_fill_qty":
            total_qty,

        "actual_quote_qty":
            total_quote,

        "actual_fill_vwap":
            vwap,

        "commission_by_asset":
            dict(
                sorted(
                    commission_by_asset.items()
                )
            ),

        "realized_pnl":
            (
                realized_pnl
                if realized_pnl_complete
                else None
            ),

        "trade_ids":
            trade_ids,

        "trade_times":
            trade_times,
    }


def execution_cost_evidence(
    trade_rows,
    *,
    order_id,
    symbol=None,
    side=None,
    reference_price=None,
    quote_asset="USDT",
    validated_cost_policy=None,
):
    """
    Build actual execution-cost evidence for one order.

    actual_fee_slippage_sane can only become True when:

    1. exact orderId account-trade rows exist,
    2. actual fill VWAP is valid,
    3. actual commission can be represented in quote asset,
    4. durable pre-order reference price exists,
    5. validated numeric execution-cost policy exists,
    6. observed fee/slippage satisfy that validated policy.

    Otherwise fail closed.
    """

    summary = summarize_order_trades(
        trade_rows,
        order_id=order_id,
        symbol=symbol,
    )

    out = {
        "schema":
            MARKER,

        "ok":
            False,

        "decision":
            "WAIT",

        "reason":
            summary.get(
                "reason",
                "UNKNOWN",
            ),

        "actual_fee_slippage_sane":
            False,

        "exact_order_id_binding":
            bool(
                summary.get(
                    "exact_order_id_binding"
                )
            ),

        "partial_fill_vwap":
            False,

        "actual_commission_sum":
            False,

        "slippage_reference_available":
            False,

        "validated_cost_policy_available":
            False,

        "commission_quote_complete":
            False,

        "summary":
            summary,
    }

    if not summary.get(
        "ok"
    ):
        return out

    out[
        "partial_fill_vwap"
    ] = True

    out[
        "actual_commission_sum"
    ] = True

    quote_asset = _text(
        quote_asset
    ).upper()

    commission_by_asset = (
        summary.get(
            "commission_by_asset"
        )
        if isinstance(
            summary.get(
                "commission_by_asset"
            ),
            dict,
        )
        else {}
    )

    unsupported_assets = [
        asset
        for asset, amount
        in commission_by_asset.items()
        if (
            amount > 0.0
            and asset
            != quote_asset
        )
    ]

    if unsupported_assets:

        out[
            "reason"
        ] = (
            "COMMISSION_QUOTE_CONVERSION_MISSING"
        )

        out[
            "unsupported_commission_assets"
        ] = sorted(
            unsupported_assets
        )

        return out

    quote_commission = float(
        commission_by_asset.get(
            quote_asset,
            0.0,
        )
    )

    out[
        "commission_quote_complete"
    ] = True

    out[
        "actual_commission_quote"
    ] = quote_commission

    quote_qty = _positive(
        summary.get(
            "actual_quote_qty"
        )
    )

    if quote_qty is None:

        out[
            "reason"
        ] = (
            "ACTUAL_QUOTE_QTY_INVALID"
        )

        return out

    fee_bps = (
        quote_commission
        / quote_qty
        * 10000.0
    )

    out[
        "actual_fee_bps"
    ] = fee_bps

    ref = _positive(
        reference_price
    )

    if ref is None:

        # Required verifier marker:
        # SLIPPAGE_REFERENCE_MISSING

        out[
            "reason"
        ] = (
            "SLIPPAGE_REFERENCE_MISSING"
        )

        return out

    out[
        "slippage_reference_available"
    ] = True

    out[
        "reference_price"
    ] = ref

    actual_vwap = _positive(
        summary.get(
            "actual_fill_vwap"
        )
    )

    if actual_vwap is None:

        out[
            "reason"
        ] = (
            "ACTUAL_FILL_VWAP_INVALID"
        )

        return out

    effective_side = (
        _normalize_side(
            side
        )
        or _normalize_side(
            summary.get(
                "side"
            )
        )
    )

    if effective_side not in (
        "BUY",
        "SELL",
    ):

        out[
            "reason"
        ] = (
            "EXECUTION_SIDE_MISSING"
        )

        return out

    if effective_side == "BUY":

        slippage_cost_ratio = (
            actual_vwap
            - ref
        ) / ref

    else:

        slippage_cost_ratio = (
            ref
            - actual_vwap
        ) / ref

    slippage_cost_bps = (
        slippage_cost_ratio
        * 10000.0
    )

    out[
        "side"
    ] = effective_side

    out[
        "actual_slippage_cost_bps"
    ] = slippage_cost_bps

    out[
        "actual_abs_slippage_bps"
    ] = abs(
        slippage_cost_bps
    )

    policy = _validated_cost_policy(
        validated_cost_policy
    )

    if policy is None:

        out[
            "reason"
        ] = (
            "VALIDATED_COST_POLICY_MISSING"
        )

        return out

    out[
        "validated_cost_policy_available"
    ] = True

    out[
        "cost_policy_version"
    ] = policy[
        "policy_version"
    ]

    fee_ok = (
        fee_bps
        <= policy[
            "max_fee_bps"
        ]
    )

    slippage_ok = (
        abs(
            slippage_cost_bps
        )
        <= policy[
            "max_abs_slippage_bps"
        ]
    )

    sane = (
        fee_ok
        and slippage_ok
    )

    out[
        "fee_within_validated_limit"
    ] = bool(
        fee_ok
    )

    out[
        "slippage_within_validated_limit"
    ] = bool(
        slippage_ok
    )

    out[
        "actual_fee_slippage_sane"
    ] = bool(
        sane
    )

    out[
        "ok"
    ] = bool(
        sane
    )

    out[
        "decision"
    ] = (
        "PASS"
        if sane
        else "BLOCK"
    )

    out[
        "reason"
    ] = (
        "ACTUAL_EXECUTION_COST_WITHIN_VALIDATED_POLICY"
        if sane
        else "ACTUAL_EXECUTION_COST_LIMIT_EXCEEDED"
    )

    return out
