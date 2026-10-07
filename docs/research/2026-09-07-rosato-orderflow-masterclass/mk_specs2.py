"""Round 2: context ('C' of CLC) filters, a longs-only diagnostic, prior-day-high pullback."""
import copy
import json

from mk_specs import AND, CLOSE, DELTA, OUT, RH, RL, gt, gte, specs, within_ticks

new = {}

ADX_1H_BALANCED = {"op": "lt", "args": [{"ind": "adx", "params": {"period": 14}, "tf": "1h"}, 20]}
TIGHT_3DAY = within_ticks(RH(3), RL(3), 320)   # 3-day range <= 80 pts (~p20)

for src in ("A0_touch_reclaim", "A3_buy_delta"):
    s = copy.deepcopy(specs[src])
    s["name"] = specs[src]["name"] + " + 1h ADX < 20"
    s["description"] += " Context filter: 1-hour ADX(14) below 20 (no trend = balanced tape)."
    s["timeframes"]["context"] = ["1h", "1D"]
    s["filters"] = [ADX_1H_BALANCED]
    new[src.split("_")[0] + "_adx"] = s

    s = copy.deepcopy(specs[src])
    s["name"] = specs[src]["name"] + " + 3-day range ≤ 80 pts"
    s["description"] += " Context filter: the previous 3 days span at most 80 points (tight balance)."
    s["filters"] = [TIGHT_3DAY]
    new[src.split("_")[0] + "_tight"] = s

s = copy.deepcopy(specs["A3_buy_delta"])
s["name"] = "Rosato A3 — longs only (trend-alignment diagnostic)"
s["direction"] = "long"
new["A3_long"] = s

s = copy.deepcopy(specs["B1_retest_buyers"])
s["name"] = "Rosato B3 — prior-day-high breakout pullback with buyers"
s["description"] = ("Break above the prior RTH high, pullback to within 1 pt, close back above within 30 bars, "
                    "pullback bar delta > 0. Stop 20 ticks, 2R.")
s["timeframes"]["context"] = []
s["entry"]["trigger"] = AND({"op": "retest", "args": [{"ind": "prior_day_high"}, 4, 30]}, gt(DELTA, 0))
new["B3_pdh_retest"] = s

s = copy.deepcopy(specs["B2_retest_strong_break"])
s["name"] = "Rosato B2 — target 3R"
s["exit"]["target"] = {"type": "rr", "value": 3.0, "level": None}
new["B2_rr3"] = s

for key, spec in new.items():
    spec["meta"]["variant"] = key
    (OUT / f"{key}.json").write_text(json.dumps(spec, indent=1))
print("wrote", list(new))
