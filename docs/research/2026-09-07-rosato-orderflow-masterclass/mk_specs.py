"""Generate Strategy Spec v2 variants for the Rosato balance-range study.

Long-side trees; `direction: both` mirrors them for shorts (lowest<->highest,
signed deltas negated, bid<->ask).
"""
import copy
import json
import pathlib

OUT = pathlib.Path(__file__).parent / "specs"
OUT.mkdir(exist_ok=True)

# Balance range = highest/lowest of the previous N closed daily (UTC-day) bars, i.e. a
# multi-day range that is stable through the session. exclude_current=False so the
# most recent completed day counts.
def RL(n=2):
    return {"ind": "lowest", "params": {"n": n, "exclude_current": False}, "tf": "1D"}


def RH(n=2):
    return {"ind": "highest", "params": {"n": n, "exclude_current": False}, "tf": "1D"}


CLOSE = {"field": "close"}
DELTA = {"ind": "bar_delta"}


def touched(level, tol, within):
    return {"op": "touched", "args": [level, tol, within]}


def gt(a, b):
    return {"op": "gt", "args": [a, b]}


def gte(a, b):
    return {"op": "gte", "args": [a, b]}


def lt(a, b):
    return {"op": "lt", "args": [a, b]}


def lte(a, b):
    return {"op": "lte", "args": [a, b]}


def AND(*a):
    return {"op": "and", "args": list(a)}


def within_ticks(a, b, n):
    return {"op": "within_ticks", "args": [a, b, n]}


def base(name, description, trigger, *, sequence=None, filters=None, stop=None, target=None,
         direction="both", entry=("09:31", "15:00"), max_trades=2, cooldown=10, time_stop=None, context=("1D",)):
    return {
        "schemaVersion": 2,
        "name": name,
        "description": description,
        "origin": {"type": "manual", "sourceId": None},
        "lineage": {"parentId": None, "changedVariable": None, "rationale": None, "trialIndex": 0},
        "status": "testing",
        "instrument": {"root": "ES", "symbol": "ES1!"},
        "timeframes": {"primary": "1min", "context": list(context)},
        "direction": direction,
        "session": {"entryWindow": {"start": entry[0], "end": entry[1]}, "noTradeWindows": [], "flattenAt": "15:58"},
        "entry": {"trigger": trigger, "sequence": sequence or [], "orderType": "market",
                  "limitOffsetTicks": 0, "stopOffsetTicks": 1, "timeoutBars": 3},
        "filters": filters or [],
        "exit": {
            "stop": stop or {"type": "structure", "structure": "session_low", "bufferTicks": 8, "value": None, "period": 14},
            "target": target or {"type": "rr", "value": 3.0, "level": None},
            "trailing": None, "breakeven": None,
            "timeStop": ({"bars": time_stop} if time_stop else None), "scaleOut": [],
        },
        "sizing": {"type": "fixed_contracts", "value": 1.0, "maxContracts": 1, "period": 14},
        "constraints": {"maxTradesPerDay": max_trades, "cooldownBars": cooldown,
                        "stopAfterConsecutiveLosses": 0, "maxConcurrentPositions": 1},
        "execution": {"mode": "bars", "slippageTicksOverride": None},
        "meta": {"study": "rosato-balance-fade-2026-09-07"},
    }


specs = {}

# ---------------------------------------------------------------- Setup A: fade the extremes
# Location: this bar traded within 1 pt of the 2-day range low (or through it) and closed back above it.
loc = lambda n=2, tol=4: AND(touched(RL(n), tol, 0), gt(CLOSE, RL(n)))

specs["A0_touch_reclaim"] = base(
    "Rosato A0 — balance-low touch & reclaim (no flow, control)",
    "Control for the order-flow confirmations: bar touches the 2-day range low (±1 pt) and closes back above it. "
    "Stop 2 pts beyond the session low, target 3R. Mirrored for shorts at the 2-day range high.",
    loc())

specs["A1_absorption"] = base(
    "Rosato A1 — balance-low absorption (sell delta, no follow-through)",
    "Carmine Rosato's core confirmation: heavy aggressive selling into the level (bar delta ≤ −300, ~p75) that fails to "
    "push price through — the bar closes back above the 2-day range low. Stop 2 pts beyond session low, 3R target.",
    AND(loc(), lte(DELTA, -300)))

specs["A2_delta_flip"] = base(
    "Rosato A2 — balance-low delta flip",
    "Sequence: heavy selling at the 2-day range low (delta ≤ −500, ~p90), then within 5 bars aggressive buying "
    "(delta ≥ +300) with price back above the level and still within 2 pts of it. His 'most selling of the session "
    "followed by the most buying' read. Stop 2 pts beyond session low, 3R.",
    AND(gte(DELTA, 300), gt(CLOSE, RL()), touched(RL(), 8, 5)),
    sequence=[{"when": AND(touched(RL(), 4, 0), lte(DELTA, -500)), "withinBars": 5}])

specs["A3_buy_delta"] = base(
    "Rosato A3 — balance-low touch with buy delta",
    "Touch-and-reclaim of the 2-day range low with the touch bar's delta toward the bounce (≥ +300). "
    "The confirmation the key-levels study found works at session extremes, applied to the multi-day range.",
    AND(loc(), gte(DELTA, 300)))

# Book confirmation (heat map 'passive buyer at the level') — ticks mode + liquidity view.
specs["A4_passive_wall"] = base(
    "Rosato A4 — balance-low touch with resting bid wall",
    "Touch-and-reclaim of the 2-day range low with a resting bid of ≥ 100 contracts within 8 ticks of price on the "
    "1-second liquidity view (his heat-map 'passive buyer filling at the level'). Ticks mode.",
    AND(loc(), gt({"ind": "large_resting_size_near", "params": {"side": "bid", "min_size": 100, "within_ticks": 8}}, 0)))
specs["A4_passive_wall"]["execution"]["mode"] = "ticks"

# Geometry / filter single-variable children of A1
specs["A1_stop24"] = copy.deepcopy(specs["A1_absorption"])
specs["A1_stop24"]["name"] = "Rosato A1 — stop 24 ticks"
specs["A1_stop24"]["exit"]["stop"] = {"type": "ticks", "value": 24, "structure": None, "bufferTicks": 0, "period": 14}

specs["A1_stop40"] = copy.deepcopy(specs["A1_absorption"])
specs["A1_stop40"]["name"] = "Rosato A1 — stop 40 ticks"
specs["A1_stop40"]["exit"]["stop"] = {"type": "ticks", "value": 40, "structure": None, "bufferTicks": 0, "period": 14}

specs["A1_rr2"] = copy.deepcopy(specs["A1_absorption"])
specs["A1_rr2"]["name"] = "Rosato A1 — target 2R"
specs["A1_rr2"]["exit"]["target"] = {"type": "rr", "value": 2.0, "level": None}

specs["A1_tgt_sesshigh"] = copy.deepcopy(specs["A1_absorption"])
specs["A1_tgt_sesshigh"]["name"] = "Rosato A1 — target session high"
specs["A1_tgt_sesshigh"]["exit"]["target"] = {"type": "level", "value": None, "level": "session_high"}

specs["A1_balance100"] = copy.deepcopy(specs["A1_absorption"])
specs["A1_balance100"]["name"] = "Rosato A1 — only when 2-day range ≤ 100 pts"
specs["A1_balance100"]["filters"] = [within_ticks(RH(), RL(), 400)]

specs["A1_3day"] = copy.deepcopy(specs["A1_absorption"])
specs["A1_3day"]["name"] = "Rosato A1 — 3-day range"
specs["A1_3day"]["entry"]["trigger"] = AND(touched(RL(3), 4, 0), gt(CLOSE, RL(3)), lte(DELTA, -300))

specs["A1_delta500"] = copy.deepcopy(specs["A1_absorption"])
specs["A1_delta500"]["name"] = "Rosato A1 — sell delta ≤ −500"
specs["A1_delta500"]["entry"]["trigger"] = AND(loc(), lte(DELTA, -500))

specs["A1_bar_stop"] = copy.deepcopy(specs["A1_absorption"])
specs["A1_bar_stop"]["name"] = "Rosato A1 — stop under the signal bar"
specs["A1_bar_stop"]["exit"]["stop"] = {"type": "structure", "structure": "bar_low", "bufferTicks": 4, "value": None, "period": 14}

# ---------------------------------------------------------------- Setup B: initiative — breakout pullback
# Long: price broke above the 2-day range high, pulled back to within 1 pt of it and closed back above,
# within 30 bars of the break; the pullback bar shows aggressive buying.
retest = {"op": "retest", "args": [RH(), 4, 30]}

specs["B0_retest"] = base(
    "Rosato B0 — balance-high breakout pullback (control)",
    "Break above the 2-day range high, pullback to within 1 pt, close back above within 30 bars. No flow condition. "
    "Stop 5 pts (20 ticks), target 2R — his continuation geometry.",
    retest, direction="long",
    stop={"type": "ticks", "value": 20, "structure": None, "bufferTicks": 0, "period": 14},
    target={"type": "rr", "value": 2.0, "level": None})

specs["B1_retest_buyers"] = copy.deepcopy(specs["B0_retest"])
specs["B1_retest_buyers"]["name"] = "Rosato B1 — breakout pullback with buyers on the pullback"
specs["B1_retest_buyers"]["description"] = ("B0 plus the pullback bar's delta > 0 (aggressive buyers stepping in where the "
                                            "initiative buyers formed).")
specs["B1_retest_buyers"]["entry"]["trigger"] = AND(retest, gt(DELTA, 0))

specs["B2_retest_strong_break"] = copy.deepcopy(specs["B0_retest"])
specs["B2_retest_strong_break"]["name"] = "Rosato B2 — strong-delta breakout, then pullback"
specs["B2_retest_strong_break"]["description"] = ("Sequence: a close above the 2-day range high with delta ≥ +500 (the initiative "
                                                  "buyers), then the retest fires within 30 bars with delta > 0.")
specs["B2_retest_strong_break"]["entry"]["sequence"] = [{"when": AND(gt(CLOSE, RH()), gte(DELTA, 500)), "withinBars": 30}]
specs["B2_retest_strong_break"]["entry"]["trigger"] = AND(retest, gt(DELTA, 0))

specs["B1_swing_stop"] = copy.deepcopy(specs["B1_retest_buyers"])
specs["B1_swing_stop"]["name"] = "Rosato B1 — stop under the pullback swing low, 3R"
specs["B1_swing_stop"]["exit"]["stop"] = {"type": "structure", "structure": "swing_low", "bufferTicks": 4, "value": None, "period": 14}
specs["B1_swing_stop"]["exit"]["target"] = {"type": "rr", "value": 3.0, "level": None}

specs["B1_stop40"] = copy.deepcopy(specs["B1_retest_buyers"])
specs["B1_stop40"]["name"] = "Rosato B1 — stop 40 ticks"
specs["B1_stop40"]["exit"]["stop"] = {"type": "ticks", "value": 40, "structure": None, "bufferTicks": 0, "period": 14}

for key, spec in specs.items():
    spec["meta"]["variant"] = key
    (OUT / f"{key}.json").write_text(json.dumps(spec, indent=1))
print(f"wrote {len(specs)} specs to {OUT}")
