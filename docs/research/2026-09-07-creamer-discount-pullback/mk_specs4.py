"""Creamer round 2: zone touch (wick), absorption bar and second failure as three sequence steps."""
import copy
import json

from mk_specs3 import AND, BULLISH, HIGHER_LOW, OUT, PDVAL, SELLERS, TREND_1H, VOL_FLOOR, base, op, specs as _prev  # noqa: F401


def ZONE(tf="15min", n=3, lo=0.705, hi=0.886):
    return op("between", {"ind": "leg_retracement", "params": {"n": n, "price": "extreme"}, "tf": tf}, lo, hi)


BELOW_VAL_LOW = op("lt", {"field": "low"}, PDVAL)      # the probing wick went below yesterday's value

new = {}
seq = lambda zone, absorb=AND(SELLERS, BULLISH): [{"when": zone, "withinBars": 6}, {"when": absorb, "withinBars": 3}]

new["C2_creamer"] = base(
    "Creamer C2 — zone touch, then absorption, then second failure higher",
    "Value-up on the hourly (close > EMA20). Step 1: a 5-min bar's low probes the 70.5–88.6 % retracement of the last "
    "15-min swing leg (discount). Step 2, within 6 bars: a bar with net aggressive selling that closes bullish (sellers "
    "absorbed). Trigger, within 3 bars: a bullish bar with a higher low (sellers fail higher) → long at its close, stop "
    "1 pt under that bar, target 2R. 09:30–11:00, ≥ 10k contracts/5-min, max 2 trades/day, stop after 2 losses. Mirrored for shorts.",
    AND(HIGHER_LOW, BULLISH), sequence=seq(ZONE()), filters=[VOL_FLOOR, TREND_1H])

new["C2_no_flow"] = copy.deepcopy(new["C2_creamer"])
new["C2_no_flow"]["name"] = "Creamer C2 — no delta condition (control)"
new["C2_no_flow"]["entry"]["sequence"] = seq(ZONE(), BULLISH)

new["C2_below_val"] = copy.deepcopy(new["C2_creamer"])
new["C2_below_val"]["name"] = "Creamer C2 — zone must sit below yesterday's value-area low"
new["C2_below_val"]["entry"]["sequence"] = seq(AND(ZONE(), BELOW_VAL_LOW))

new["C2_wide"] = copy.deepcopy(new["C2_creamer"])
new["C2_wide"]["name"] = "Creamer C2 — golden-pocket band 0.618–0.886"
new["C2_wide"]["entry"]["sequence"] = seq(ZONE(lo=0.618))

new["C2_leg5m"] = copy.deepcopy(new["C2_creamer"])
new["C2_leg5m"]["name"] = "Creamer C2 — swing leg on the 5-min chart"
new["C2_leg5m"]["entry"]["sequence"] = seq(ZONE("5min", 3))

new["C2_no_trend"] = copy.deepcopy(new["C2_creamer"])
new["C2_no_trend"]["name"] = "Creamer C2 — no hourly trend filter"
new["C2_no_trend"]["filters"] = [VOL_FLOOR]

new["C2_rr15"] = copy.deepcopy(new["C2_creamer"])
new["C2_rr15"]["name"] = "Creamer C2 — target 1.5R"
new["C2_rr15"]["exit"]["target"] = {"type": "rr", "value": 1.5, "level": None}

new["C2_swing_target"] = copy.deepcopy(new["C2_creamer"])
new["C2_swing_target"]["name"] = "Creamer C2 — target the swing high"
new["C2_swing_target"]["exit"]["target"] = {"type": "level", "value": None, "level": "swing_high"}

new["C2_breakeven"] = copy.deepcopy(new["C2_creamer"])
new["C2_breakeven"]["name"] = "Creamer C2 — breakeven at 1R"
new["C2_breakeven"]["exit"]["breakeven"] = {"atR": 1.0, "offsetTicks": 1}

new["C2_stop_swing"] = copy.deepcopy(new["C2_creamer"])
new["C2_stop_swing"]["name"] = "Creamer C2 — stop under the 5-min swing low"
new["C2_stop_swing"]["exit"]["stop"] = {"type": "structure", "structure": "swing_low", "bufferTicks": 4, "value": None, "period": 14}

new["C2_all_day"] = copy.deepcopy(new["C2_creamer"])
new["C2_all_day"]["name"] = "Creamer C2 — entries until 15:00 (his 90-minute cutoff removed)"
new["C2_all_day"]["session"]["entryWindow"] = {"start": "09:30", "end": "15:00"}

for key, spec in new.items():
    spec["meta"]["variant"] = key
    (OUT / f"{key}.json").write_text(json.dumps(spec, indent=1))
print("wrote", list(new))
