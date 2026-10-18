"""Immutable feature/price replay for the separately approved Nautilus test.

No historical front-month lookup, substitute bars, warm caches or live feed.
"""
import copy
import hashlib
import json
from datetime import date, timedelta

import pandas as pd

import database
from engine.mbo_features import FEATURES
from engine.feature_schema import features_for, frame_features
from engine.ml_signal import parse_frozen
from market import catalog as cat
from models import MLExperiment
from research_agent import memory
from research_ml import service
from research_ml.schema import parse_plan


def uses_mbo(strategy):
    def visit(x):
        if isinstance(x, dict):
            return x.get("ind") == "frozen_mbo_signal" or any(visit(v) for v in x.values())
        return isinstance(x, list) and any(visit(v) for v in x)
    return visit(strategy)


def load(strategy, *, allow_rejected=False):
    meta = strategy.get("meta", {}).get("mlDiscovery", {})
    with database.session_scope() as db:
        row = db.get(MLExperiment, meta.get("experimentId", ""))
        if (not row or row.status not in ({"candidate", "no_edge"} if allow_rejected else {"candidate"})
                or (row.decision_json or {}).get("action") != "approve"):
            raise ValueError("approved completed MBO experiment required")
        document, result = copy.deepcopy(row.document_json), copy.deepcopy(row.result_json)
        expected = row.digest
    result_digest = result.pop("resultDigest", None)
    if (memory.digest(document) != expected or meta.get("experimentDigest") != expected
            or result.get("experimentDigest") != expected or memory.digest(result) != result_digest
            or meta.get("resultDigest") != result_digest):
        raise ValueError("frozen MBO provenance changed")
    plan = parse_plan(document["plan"])
    if not plan.method.startswith("mbo_"):
        raise ValueError("raw MBO experiment required")
    if document["provenance"] != service.provenance() or service._scope(plan) != document["dataset"]["sessions"]:
        raise ValueError("frozen MBO code/config/sessions changed")
    if (strategy.get("entry", {}).get("trigger") != result["model"]["trigger"]
            or strategy.get("entry", {}).get("sequence") != [] or strategy.get("filters") != []
            or strategy.get("direction") != plan.direction
            or strategy.get("execution", {}).get("mode") != "bars"
            or strategy.get("instrument") != {"root": "ES", "symbol": "ES1!"}
            or strategy.get("timeframes") != {"primary": "1min", "context": []}):
        raise ValueError("strategy does not match frozen MBO inference contract")
    parse_frozen(result["model"]["trigger"]["args"][0]["params"]["model"])
    with service._snapshot_path(meta["experimentId"]).open("rb") as stream:
        content = stream.read(service.MAX_SNAPSHOT_BYTES + 1)
    if len(content) > service.MAX_SNAPSHOT_BYTES or hashlib.sha256(content).hexdigest() != document["dataset"]["sha256"]:
        raise ValueError("approved MBO snapshot changed")
    payload = json.loads(content)
    if payload["columns"] != service.core.COLUMNS + features_for(plan.featureVersion):
        raise ValueError("frozen MBO feature schema changed")
    bars = service.core.validate_bars(pd.DataFrame(payload["values"], columns=payload["columns"],
        index=pd.to_datetime(payload["timestamps"], unit="ns", utc=True)))
    return plan, document, bars


def context(strategy):
    plan, document, _ = load(strategy)
    sessions = document["dataset"]["sessions"]
    def window(start, end):
        days = [d for d in sessions if start <= date.fromisoformat(d) <= end]
        return [days[0], days[-1]]
    return {"windows": {"is": window(plan.start, plan.trainEnd),
                        "wf1": window(plan.trainEnd + timedelta(days=1), plan.validationEnd),
                        "wf2": window(plan.validationEnd + timedelta(days=1), plan.testEnd)},
        "inSampleSessions": sessions, "splitsSha256": memory.digest(sessions),
        "fixedContractSha256": memory.digest({"contract": plan.contract, "sources": document["dataset"]["sources"]}),
        "frontMonthSha256": None, "inventory": {"bars": [document["dataset"]["sha256"]], "ticks": []},
        "identityPolicy": "immutable_MBO_receive_time_snapshot",
        "provenance": document["provenance"], "dataset": document["dataset"],
        "warning": "reuses discovery partitions; descriptive execution validation, not new out-of-sample evidence"}


def replay(strategy, start, end, *, allow_rejected=False):
    plan, document, frame = load(strategy, allow_rejected=allow_rejected)
    if allow_rejected and (start <= plan.trainEnd or end > plan.validationEnd):
        raise ValueError("diagnostic execution is restricted to already exposed validation sessions")
    if start < plan.start or end > plan.testEnd or start > end:
        raise ValueError("Nautilus dates exceed approved MBO snapshot")
    days = frame.index.tz_convert("America/New_York").date
    return plan.contract, frame[(days >= start) & (days <= end)], document["dataset"]


def nautilus_day(frame, symbol, day):
    from nautilus_trader.model.data import Bar, BarType, TradeTick
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.enums import AggressorSide
    from nautilus_trader.model.identifiers import TradeId, InstrumentId
    part = frame[frame.index.tz_convert("America/New_York").date == day]
    bar_type = BarType.from_str(cat.bar_type_str(symbol))
    bars, rows, flows = [], {}, {}
    for ts, r in part.iterrows():
        close = ts.value + 60_000_000_000
        # One nanosecond after the preceding minute completes: causal signal
        # first, then next opening price. This is an execution-price event only.
        opening = ts.value + 1
        bars.append(TradeTick(InstrumentId.from_str(cat.instrument_id(symbol)), Price.from_str(f"{r.open:.2f}"),
            Quantity.from_int(1), AggressorSide.NO_AGGRESSOR, TradeId(str(opening)), opening, opening))
        bars.append(Bar(bar_type, *(Price.from_str(f"{r[c]:.2f}") for c in ("open", "high", "low", "close")),
                        Quantity.from_int(int(r.volume)), close, close))
        rows[close] = r[frame_features(frame.columns)].tolist()
        buy, sell = r.aggressive_buy_rate * 60, r.aggressive_sell_rate * 60
        flows[close] = (r.volume, buy - sell, buy, sell)
    return bars, rows, flows
