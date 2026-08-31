import json
from pathlib import Path

from engine import spec as S
from engine.v1_to_v2 import convert_v1_to_v2

LEGACY_DIR = Path(__file__).resolve().parent.parent / "strategies"


def test_legacy_files_round_trip():
    files = sorted(LEGACY_DIR.glob("*.json")) if LEGACY_DIR.exists() else []
    fixtures = [json.loads(f.read_text()) for f in files] or [{
        "id": "abc", "name": "ORB", "symbol": "ES1!", "direction": "long", "riskPerTradePercent": 2,
        "conditions": [{"type": "breaks_high", "lookback": 15}], "stop": {"type": "percent", "value": 0.3},
        "target": {"type": "rr", "value": 2.5}, "session": {"start": "13:30", "end": "16:00"},
        "sizing": {"type": "percent_equity", "value": 95}}]
    for v1 in fixtures:
        v2 = convert_v1_to_v2(v1)
        assert S.validate_spec(v2) == [], (v1.get("name"), S.validate_spec(v2))
        assert v2["id"] == v1["id"] and v2["direction"] == v1["direction"]
        assert v2["session"]["entryWindow"]["start"] == "09:30"
        assert v2["entry"]["trigger"]["op"] == "gt"
        assert v2["exit"]["target"]["type"] == "rr"
        assert v2["sizing"]["type"] == "fixed_risk" and v2["sizing"]["value"] == float(v1.get("riskPerTradePercent", 0.5))
        assert v2["meta"]["convertedFrom"] == "v1"


def test_condition_mapping():
    v1 = {"name": "x", "symbol": "ES1!", "direction": "short", "interval": "5min",
          "conditions": [{"type": "sma_cross_below", "fast": 9, "slow": 21}, {"type": "rsi_below", "period": 14, "value": 30},
                         {"type": "cvd_falling", "lookback": 10}, {"type": "rel_volume_above", "lookback": 20, "value": 1.5},
                         {"type": "consecutive", "count": 2, "color": "red"}],
          "stop": {"type": "atr", "mult": 1.5, "period": 14}, "target": {"type": "fixed_points", "value": 8},
          "session": {"start": "14:30", "end": "19:00"}, "sizing": {"type": "fixed_qty", "value": 2}}
    v2 = convert_v1_to_v2(v1)
    assert S.validate_spec(v2) == []
    trig = v2["entry"]["trigger"]
    assert trig["op"] == "and" and [a["op"] for a in trig["args"]] == ["cross_below", "lt", "lt", "gt", "eq"]
    assert v2["timeframes"]["primary"] == "5min"
    assert v2["exit"]["stop"] == {"type": "atr", "value": 1.5, "period": 14}
    assert v2["exit"]["target"] == {"type": "points", "value": 8.0}
    assert v2["sizing"] == {"type": "fixed_contracts", "value": 2, "maxContracts": 2}
    assert v2["session"]["entryWindow"] == {"start": "10:30", "end": "15:00"}
