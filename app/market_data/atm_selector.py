"""
ATM strike selection helpers with spot-distance guardrails.

These helpers prevent pathological far-OTM selections when an option-chain
snapshot contains stale or anomalous CE/PE prices.
"""

from __future__ import annotations

from typing import Any


def _round_to_step(price: float, step: float) -> float:
    return round(price / step) * step


def select_atm_from_straddle_legs(
    legs_by_strike: dict[float, dict[str, float]],
    spot_price: float | None,
    strike_step: float | None,
    near_window_steps: int = 25,
    max_drift_steps: int = 6,
) -> tuple[float | None, dict[str, Any]]:
    """
    Choose ATM from CE+PE minimum with spot guardrails.

    Strategy:
    1) Compute global minimum straddle (CE+PE).
    2) If spot+step are available, prefer minimum within +/- near_window_steps.
    3) If chosen strike drifts beyond max_drift_steps from rounded spot,
       force rounded-spot fallback.
    """
    valid: list[tuple[float, float, float, float]] = []
    for strike, legs in legs_by_strike.items():
        ce = legs.get("CE")
        pe = legs.get("PE")
        if ce is None or pe is None:
            continue
        if ce <= 0 or pe <= 0:
            continue
        valid.append((float(strike), float(ce), float(pe), float(ce + pe)))

    if not valid:
        return None, {"reason": "no_valid_ce_pe_pairs"}

    global_best = min(valid, key=lambda t: (t[3], t[0]))

    rounded_spot: float | None = None
    window_best: tuple[float, float, float, float] | None = None
    if (
        spot_price is not None
        and strike_step is not None
        and strike_step > 0
        and near_window_steps > 0
    ):
        rounded_spot = _round_to_step(float(spot_price), float(strike_step))
        max_dist = near_window_steps * float(strike_step)
        near = [row for row in valid if abs(row[0] - rounded_spot) <= max_dist]
        if near:
            window_best = min(near, key=lambda t: (t[3], t[0]))

    chosen = window_best or global_best
    method = "straddle_min_near_spot" if window_best else "straddle_min_global"

    if (
        rounded_spot is not None
        and strike_step is not None
        and strike_step > 0
        and max_drift_steps > 0
        and abs(chosen[0] - rounded_spot) > (max_drift_steps * float(strike_step))
    ):
        return rounded_spot, {
            "method": "spot_guard_fallback",
            "rounded_spot": rounded_spot,
            "global_best_strike": global_best[0],
            "global_best_sum": global_best[3],
        }

    return chosen[0], {
        "method": method,
        "rounded_spot": rounded_spot,
        "selected_strike": chosen[0],
        "selected_ce": chosen[1],
        "selected_pe": chosen[2],
        "selected_sum": chosen[3],
        "global_best_strike": global_best[0],
        "global_best_sum": global_best[3],
    }


def legs_from_rest_optionchain(oc: dict[str, Any]) -> dict[float, dict[str, float]]:
    """Convert Dhan /optionchain oc payload to strike->(CE,PE) legs map."""
    out: dict[float, dict[str, float]] = {}
    for strike_str, strike_data in (oc or {}).items():
        try:
            strike = float(strike_str)
        except Exception:
            continue
        ce = (strike_data or {}).get("ce") or {}
        pe = (strike_data or {}).get("pe") or {}
        ce_ltp = ce.get("last_price")
        pe_ltp = pe.get("last_price")
        try:
            ce_f = float(ce_ltp) if ce_ltp is not None else None
            pe_f = float(pe_ltp) if pe_ltp is not None else None
        except Exception:
            continue
        legs = out.setdefault(strike, {})
        if ce_f is not None:
            legs["CE"] = ce_f
        if pe_f is not None:
            legs["PE"] = pe_f
    return out
