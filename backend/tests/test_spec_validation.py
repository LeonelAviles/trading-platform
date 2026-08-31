import json
from pathlib import Path

from engine import spec as S

ORB = {
    "schemaVersion": 2, "name": "ORB 15m — breakout", "instrument": {"root": "ES", "symbol": "ES1!"},
    "timeframes": {"primary": "1min", "context": []}, "direction": "both",
    "session": {"entryWindow": {"start": "09:45", "end": "11:30"}, "flattenAt": "15:58"},
    "entry": {"trigger": {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]},
              "orderType": "market", "timeoutBars": 1},
    "filters": [],
    "exit": {"stop": {"type": "structure", "structure": "or_low", "bufferTicks": 2},
             "target": {"type": "rr", "value": 2.0}, "trailing": None, "breakeven": None, "timeStop": None, "scaleOut": []},
    "sizing": {"type": "fixed_risk", "value": 0.5, "maxContracts": 5},
    "constraints": {"maxTradesPerDay": 1, "cooldownBars": 0, "stopAfterConsecutiveLosses": 1, "maxConcurrentPositions": 1},
    "execution": {"mode": "ticks"},
}


def test_orb_example_validates_and_normalizes():
    assert S.validate_spec(ORB) == []
    n = S.normalize(ORB)
    assert n["id"] and n["risk"]["accountSize"] == 100000 and n["risk"]["passCriteria"]["minTradesInSample"] == 100
    assert n["status"] == "draft" and n["lineage"]["trialIndex"] == 0
    assert S.required_mode(ORB) == "bars"


def _bad(**over):
    spec = json.loads(json.dumps(ORB))
    for k, v in over.items():
        cur = spec
        parts = k.split(".")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = v
    return spec


def test_validation_errors_are_readable():
    assert any("unknown primitive" in e for e in S.validate_spec(_bad(**{"entry.trigger": {"op": "gt", "args": [{"ind": "magic"}, 1]}})))
    assert any("must be int" in e for e in S.validate_spec(_bad(**{"entry.trigger": {"op": "gt", "args": [{"ind": "sma", "params": {"period": "x"}}, 1]}})))
    assert any("tf '15min' is not in timeframes" in e for e in S.validate_spec(_bad(**{"filters": [{"op": "gt", "args": [{"ind": "ema", "params": {"period": 9, "tf": "15min"}}, 1]}]})))
    assert any("inside RTH" in e for e in S.validate_spec(_bad(**{"session.entryWindow": {"start": "08:00", "end": "11:00"}})))
    assert any("exit.target.level" in e for e in S.validate_spec(_bad(**{"exit.target": {"type": "level"}})))
    assert any("exit.stop.structure" in e for e in S.validate_spec(_bad(**{"exit.stop": {"type": "structure"}})))
    assert any("unknown field" in e for e in S.validate_spec(_bad(bogus=1)))
    assert any("direction" in e for e in S.validate_spec(_bad(direction="sideways")))
    assert any("coarser multiple" in e for e in S.validate_spec(_bad(**{"timeframes": {"primary": "5min", "context": ["1min"]}})))
    assert any("does not match symbol" in e for e in S.validate_spec(_bad(**{"instrument": {"root": "NQ", "symbol": "ES1!"}})))
    assert any("flattenAt" in e for e in S.validate_spec(_bad(**{"session": {"entryWindow": {"start": "09:45", "end": "15:59"}, "flattenAt": "15:58"}})))
    assert any("boolean expression" in e for e in S.validate_spec(_bad(**{"entry.trigger": {"field": "close"}})))


def test_required_mode_detects_orderflow_and_order_types():
    tick_spec = _bad(**{"filters": [{"op": "gte", "args": [{"ind": "absorption", "params": {"side": "bid"}}, 1]}]})
    assert S.required_mode(tick_spec) == "ticks"
    assert S.required_mode(_bad(**{"entry.orderType": "limit"})) == "ticks"


def test_schema_export_and_primitive_docs():
    schema = S.json_schema()
    assert schema["title"] == "StrategySpec v2" and "gt" in schema["x-operators"]
    assert "entry" in schema["properties"] and "risk" in schema["properties"]
    docs = S.primitive_docs()
    names = {d["name"] for d in docs}
    assert {"opening_range_high", "absorption"} <= names
    exported = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "spec" / "schema.json"
    if exported.exists():
        assert json.loads(exported.read_text())["schema"]["properties"].keys() == schema["properties"].keys()
