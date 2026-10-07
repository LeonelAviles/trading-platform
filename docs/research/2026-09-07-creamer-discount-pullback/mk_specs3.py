"""Chris Creamer (Robbins World Cup, YouTube PL7LKUsCgIQ) — value-structure pullback into discount,
seller absorption, second failure higher, entry. 5-minute primary, first 90 minutes of RTH.

Long-side trees; `direction: both` mirrors them (leg_retracement negates, prior_day_val -> vah, deltas negate).
"""
import copy
import json
import pathlib

OUT = pathlib.Path(__file__).parent / "specs"
OUT.mkdir(exist_ok=True)

CLOSE, OPEN, LOW, VOL = {"field": "close"}, {"field": "open"}, {"field": "low"}, {"field": "volume"}
DELTA = {"ind": "bar_delta"}
PREV_LOW = {"ind": "lowest", "params": {"n": 1}}                    # previous closed bar's low
PDVAL = {"ind": "prior_day_val"}


def RETR(tf="15min", n=3):
    return {"ind": "leg_retracement", "params": {"n": n}, "tf": tf}


def op(o, *a):
    return {"op": o, "args": list(a)}


def AND(*a):
    return op("and", *a)


DISCOUNT = lambda tf="15min", n=3: op("between", RETR(tf, n), 0.705, 0.886)
BULLISH = op("gt", CLOSE, OPEN)
HIGHER_LOW = op("gt", LOW, PREV_LOW)
SELLERS = op("lt", DELTA, 0)
BELOW_VAL = op("lt", CLOSE, PDVAL)
TREND_1H = op("gt", {"field": "close", "tf": "1h"}, {"ind": "ema", "params": {"period": 20}, "tf": "1h"})
VOL_FLOOR = op("gte", VOL, 10000)


def base(name, description, trigger, *, sequence=None, filters=None, stop=None, target=None, breakeven=None,
         trailing=None, direction="both", entry=("09:30", "11:00"), context=("15min", "1h")):
    return {
        "schemaVersion": 2,
        "name": name,
        "description": description,
        "origin": {"type": "manual", "sourceId": None},
        "lineage": {"parentId": None, "changedVariable": None, "rationale": None, "trialIndex": 0},
        "status": "testing",
        "instrument": {"root": "ES", "symbol": "ES1!"},
        "timeframes": {"primary": "5min", "context": list(context)},
        "direction": direction,
        "session": {"entryWindow": {"start": entry[0], "end": entry[1]}, "noTradeWindows": [], "flattenAt": "15:58"},
        "entry": {"trigger": trigger, "sequence": sequence or [], "orderType": "market",
                  "limitOffsetTicks": 0, "stopOffsetTicks": 1, "timeoutBars": 3},
        "filters": filters if filters is not None else [VOL_FLOOR],
        "exit": {
            "stop": stop or {"type": "structure", "structure": "bar_low", "bufferTicks": 4, "value": None, "period": 14},
            "target": target or {"type": "rr", "value": 2.0, "level": None},
            "trailing": trailing, "breakeven": breakeven, "timeStop": None, "scaleOut": [],
        },
        "sizing": {"type": "fixed_contracts", "value": 1.0, "maxContracts": 1, "period": 14},
        "constraints": {"maxTradesPerDay": 2, "cooldownBars": 0, "stopAfterConsecutiveLosses": 2, "maxConcurrentPositions": 1},
        "execution": {"mode": "bars", "slippageTicksOverride": None},
        "meta": {"study": "creamer-discount-pullback-2026-09-07"},
    }


specs = {}

# Step 1: absorption bar in discount — sellers aggressive (delta < 0), bullish close, below prior-day VAL.
# Trigger (within 3 bars): sellers fail higher — higher low than the previous bar, bullish close.
ABSORB = AND(DISCOUNT(), BELOW_VAL, SELLERS, BULLISH)
FLIP2 = AND(HIGHER_LOW, BULLISH)

specs["C1_creamer"] = base(
    "Creamer C1 — discount pullback, seller absorption, second failure higher",
    "Value-up on the hourly (close > EMA20). Location: 15-min swing leg retraced 70.5–88.6 % and price below the prior "
    "session's value-area low. Confirmation on 5-min bars: a bar with net aggressive selling that closes bullish "
    "(absorption), then within 3 bars a bullish bar with a higher low (sellers fail higher) → long at its close. Stop 1 pt "
    "under that bar, target 2R. 09:30–11:00, ≥ 10k contracts per 5-min bar, max 2 trades, stop after 2 losses. Mirrored for shorts.",
    FLIP2, sequence=[{"when": ABSORB, "withinBars": 3}], filters=[VOL_FLOOR, TREND_1H])

specs["C0_no_flow"] = copy.deepcopy(specs["C1_creamer"])
specs["C0_no_flow"]["name"] = "Creamer C0 — same geometry, no delta condition (control)"
specs["C0_no_flow"]["entry"]["sequence"] = [{"when": AND(DISCOUNT(), BELOW_VAL, BULLISH), "withinBars": 3}]

specs["C0b_no_location"] = copy.deepcopy(specs["C1_creamer"])
specs["C0b_no_location"]["name"] = "Creamer C0b — absorption + second failure anywhere (no discount / VAL)"
specs["C0b_no_location"]["entry"]["sequence"] = [{"when": AND(SELLERS, BULLISH), "withinBars": 3}]

specs["C1_no_val"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_no_val"]["name"] = "Creamer C1 — discount only (drop the below-VAL condition)"
specs["C1_no_val"]["entry"]["sequence"] = [{"when": AND(DISCOUNT(), SELLERS, BULLISH), "withinBars": 3}]

specs["C1_no_trend"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_no_trend"]["name"] = "Creamer C1 — no hourly trend filter"
specs["C1_no_trend"]["filters"] = [VOL_FLOOR]

specs["C1_no_volfloor"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_no_volfloor"]["name"] = "Creamer C1 — no volume floor"
specs["C1_no_volfloor"]["filters"] = [TREND_1H]

specs["C1_leg5m"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_leg5m"]["name"] = "Creamer C1 — swing leg on the 5-min chart (n=5)"
specs["C1_leg5m"]["entry"]["sequence"] = [{"when": AND(DISCOUNT("5min", 5), BELOW_VAL, SELLERS, BULLISH), "withinBars": 3}]

specs["C1_buyers_on_entry"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_buyers_on_entry"]["name"] = "Creamer C1 — entry bar must show net buying (imbalances lighting up)"
specs["C1_buyers_on_entry"]["entry"]["trigger"] = AND(HIGHER_LOW, BULLISH, op("gt", DELTA, 0))

specs["C1_rr15"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_rr15"]["name"] = "Creamer C1 — target 1.5R"
specs["C1_rr15"]["exit"]["target"] = {"type": "rr", "value": 1.5, "level": None}

specs["C1_swing_target"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_swing_target"]["name"] = "Creamer C1 — target the swing high (his 'swing points')"
specs["C1_swing_target"]["exit"]["target"] = {"type": "level", "value": None, "level": "swing_high"}

specs["C1_breakeven"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_breakeven"]["name"] = "Creamer C1 — breakeven at 1R, target 2R"
specs["C1_breakeven"]["exit"]["breakeven"] = {"atR": 1.0, "offsetTicks": 1}

specs["C1_trail"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_trail"]["name"] = "Creamer C1 — trail 16 ticks after 1R, target 3R"
specs["C1_trail"]["exit"]["target"] = {"type": "rr", "value": 3.0, "level": None}
specs["C1_trail"]["exit"]["trailing"] = {"type": "ticks", "value": 16, "period": 14, "activateAtR": 1.0}

specs["C1_stop_swing"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_stop_swing"]["name"] = "Creamer C1 — stop under the 5-min swing low"
specs["C1_stop_swing"]["exit"]["stop"] = {"type": "structure", "structure": "swing_low", "bufferTicks": 4, "value": None, "period": 14}

specs["C1_single_flip"] = copy.deepcopy(specs["C1_creamer"])
specs["C1_single_flip"]["name"] = "Creamer C1 — enter on the first bullish flip (no second failure)"
specs["C1_single_flip"]["entry"]["sequence"] = []
specs["C1_single_flip"]["entry"]["trigger"] = ABSORB

for key, spec in specs.items():
    spec["meta"]["variant"] = key
    (OUT / f"{key}.json").write_text(json.dumps(spec, indent=1))
print(f"wrote {len(specs)} specs to {OUT}")
