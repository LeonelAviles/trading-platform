"""Strategy document schema shared by the API (validation) and the frontend
builder. The NautilusTrader worker (nautilus_backtest.py) interprets these
same condition types at runtime.
"""

CONDITION_DEFS = {
    "price_above": {"params": {"value": float}},
    "price_below": {"params": {"value": float}},
    "sma_cross_above": {"params": {"fast": int, "slow": int}},
    "sma_cross_below": {"params": {"fast": int, "slow": int}},
    "rsi_above": {"params": {"period": int, "value": float}},
    "rsi_below": {"params": {"period": int, "value": float}},
    "breaks_high": {"params": {"lookback": int}},
    "breaks_low": {"params": {"lookback": int}},
    "consecutive": {"params": {"count": int, "color": str}},
}

STOP_TYPES = {"percent", "fixed_points", "atr"}
TARGET_TYPES = {"rr", "percent", "fixed_points"}


def validate_strategy(s: dict) -> list[str]:
    """Returns a list of human-readable problems; empty list means valid."""
    errors = []
    if not s.get("name", "").strip():
        errors.append("name is required")
    if not s.get("symbol", "").strip():
        errors.append("symbol is required")
    if s.get("direction") not in ("long", "short"):
        errors.append("direction must be 'long' or 'short'")
    conditions = s.get("conditions", [])
    if not conditions:
        errors.append("at least one entry condition is required")
    for i, cond in enumerate(conditions):
        defn = CONDITION_DEFS.get(cond.get("type"))
        if defn is None:
            errors.append(f"condition {i + 1}: unknown type '{cond.get('type')}'")
            continue
        for pname, ptype in defn["params"].items():
            if pname not in cond:
                errors.append(f"condition {i + 1} ({cond['type']}): missing '{pname}'")
            elif ptype in (int, float):
                try:
                    ptype(cond[pname])
                except (TypeError, ValueError):
                    errors.append(f"condition {i + 1} ({cond['type']}): '{pname}' must be a number")
    if s.get("stop", {}).get("type") not in STOP_TYPES:
        errors.append(f"stop.type must be one of {sorted(STOP_TYPES)}")
    if s.get("target", {}).get("type") not in TARGET_TYPES:
        errors.append(f"target.type must be one of {sorted(TARGET_TYPES)}")
    for key in ("stop", "target"):
        try:
            float(s.get(key, {}).get("value"))
        except (TypeError, ValueError):
            errors.append(f"{key}.value must be a number")
    return errors
