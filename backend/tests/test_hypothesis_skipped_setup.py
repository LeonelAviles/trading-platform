"""Hypothesis engine: provisional replay finds skipped setups, contradictions
and confirmations ask questions, answers are labelled (LLM scripted)."""

import json

import pytest

from agent.client import FakeAnthropic, LLM
from teaching import store
from teaching.hypothesis import HypothesisEngine, fires, label_answer, provisional_spec, skipped_candidates

NS = 1_000_000_000

OR_RULE = {"id": "r1", "text": "close above the 15-minute opening-range high with positive bar delta", "direction": "long",
           "expr": {"op": "and", "args": [
               {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]},
               {"op": "gt", "args": [{"field": "delta"}, 0]}]},
           "filters": [], "supports": [], "contradicts": [], "confidence": 0.7}


def _bars(n=60, start=None):
    """RTH 1-minute bars: flat OR, then a breakout with positive delta at bars 20 and 40."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    t0 = start or int(datetime(2026, 6, 12, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp())
    out = []
    for i in range(n):
        base = 5300.0
        close = base + (1.0 if i in (20, 40, 41) else -0.5 if i > 15 else 0.0)
        delta = 50 if i in (20, 40) else -20
        out.append({"time": t0 + 60 * i, "open": base, "high": max(base, close) + 0.25, "low": min(base, close) - 0.25,
                    "close": close, "volume": 100, "delta": delta, "buyVol": 50 + delta // 2, "sellVol": 50 - delta // 2})
    return out


def _script(tags=None, hyp=None):
    return [[("text", json.dumps(tags or {"location": "at OR high", "flow": "positive delta", "candle": "breakout", "timeBucket": "open", "tags": ["or_breakout"]}))],
            [("text", json.dumps(hyp or {"summary": "OR breakout longs", "rules": [OR_RULE], "latestTradeContradicts": None,
                                         "questions": {"confirm": "You buy OR-high breaks with positive delta — a confirmation?", "contradiction": None}}))]]


def test_provisional_spec_fires_on_qualifying_bars():
    bars = _bars()
    spec = provisional_spec(OR_RULE)
    fl = fires(spec, bars)
    times = [t for t, _ in fl]
    assert bars[20]["time"] in times and bars[40]["time"] in times
    assert bars[41]["time"] not in times          # negative delta
    assert skipped_candidates(fl, [bars[20]["time"]]) == [(bars[40]["time"], "long")]
    assert skipped_candidates(fl, [bars[20]["time"], bars[40]["time"] + 120]) == []


def test_skipped_setup_and_questions(db_engine, monkeypatch):
    import database
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(database, "engine", db_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=db_engine, autoflush=False, future=True))
    sess = store.create_session("ES1!", "ES", date_from="2026-06-12")
    bars = _bars()
    fake = FakeAnthropic(script=[])
    eng = HypothesisEngine(sess["id"], LLM(fake))
    snap = {"features": {"opening_range_high": 5300.25, "delta": 50}, "book": {"bids": [], "asks": []}, "bars": {"1min": bars[:21]}, "levels": {}}

    def persist(t):
        store.add_trade(sess["id"], direction=t["direction"], entry_ts=t["entryTs"], entry_price=t["entryPrice"], stop=None, target=None, trade_id=t["id"])
        return t

    # trade 1 at the first breakout -> first-trade question
    fake.script.extend(_script())
    t1 = persist({"id": "t1", "direction": "long", "entryTs": bars[20]["time"] * NS, "entryTime": bars[20]["time"], "entryPrice": 5301.25})
    q1 = eng.on_trade(t1, snap, bars[:21])
    assert q1 and q1["kind"] == "first"
    # no skipped setups yet: only bar 20 fired so far and it was traded
    assert not store.events(sess["id"], "skipped_setup")

    # trade 2: the rule now has 2 supports, but the one-question-per-two-trades
    # gap blocks a confirmation right after the first-trade question; the
    # provisional replay still records that bar 40 was skipped.
    hyp2 = {"summary": "OR breakout longs", "rules": [{**OR_RULE, "supports": ["t1", "t2"]}], "latestTradeContradicts": None,
            "questions": {"confirm": "You buy OR-high breaks with positive delta — a confirmation?", "contradiction": None}}
    fake.script.extend(_script(hyp=hyp2))
    t2 = persist({"id": "t2", "direction": "long", "entryTs": bars[50]["time"] * NS, "entryTime": bars[50]["time"], "entryPrice": 5300.0})
    assert eng.on_trade(t2, snap, bars[:51]) is None
    skipped = store.events(sess["id"], "skipped_setup")
    assert [e["payload"]["time"] for e in skipped] == [bars[40]["time"]]
    assert skipped[0]["payload"]["source"] == "candidate"

    # trade 3: gap satisfied -> confirmation question for the 2-support rule
    hyp3 = {**hyp2, "rules": [{**OR_RULE, "supports": ["t1", "t2", "t3"]}]}
    fake.script.extend(_script(hyp=hyp3))
    t3 = persist({"id": "t3", "direction": "long", "entryTs": bars[52]["time"] * NS, "entryTime": bars[52]["time"], "entryPrice": 5300.0})
    q2 = eng.on_trade(t3, snap, bars[:53])
    assert q2 and q2["kind"] == "confirm"

    # answer confirms the rule
    eng.on_answer(q2["id"], "yes, exactly")
    assert "r1" in eng.confirmed
    assert store.questions(sess["id"])[-1]["answer"] == "yes, exactly"

    # trade 4 contradicts -> contradiction question regardless of the gap
    hyp4 = {**hyp3, "rules": [{**OR_RULE, "supports": ["t1", "t2", "t3"], "contradicts": ["t4"]}], "latestTradeContradicts": "r1",
            "questions": {"confirm": None, "contradiction": "This one had negative delta behind it. What made you take it?"}}
    fake.script.extend(_script(hyp=hyp4))
    t4 = persist({"id": "t4", "direction": "long", "entryTs": bars[55]["time"] * NS, "entryTime": bars[55]["time"], "entryPrice": 5299.0})
    q3 = eng.on_trade(t4, snap, bars[:56])
    assert q3 and q3["kind"] == "contradiction" and "negative delta" in q3["question"]

    # skipped-setup answers are labelled
    hyp_events = store.events(sess["id"], "hypothesis_update")
    assert len(hyp_events) == 4 and hyp_events[-1]["payload"]["version"] == 4
    assert label_answer("I missed it, was looking away") == "missed"
    assert label_answer("that one is too loose, not a setup") == "rule_too_loose"
    assert label_answer("skipped it on purpose: news at 10:00") == "valid_skip"
    assert label_answer("whatever", explicit="missed") == "missed"


def test_llm_failure_degrades_gracefully(db_engine, monkeypatch):
    import database
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(database, "engine", db_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=db_engine, autoflush=False, future=True))
    sess = store.create_session("ES1!", "ES")

    class Boom:
        def create(self, **kw):
            raise RuntimeError("no credits")

    eng = HypothesisEngine(sess["id"], Boom())
    bars = _bars()
    store.add_trade(sess["id"], direction="long", entry_ts=bars[20]["time"] * NS, entry_price=5301.0, stop=None, target=None, trade_id="x")
    q = eng.on_trade({"id": "x", "direction": "long", "entryTs": bars[20]["time"] * NS, "entryTime": bars[20]["time"], "entryPrice": 5301.0},
                     {"features": {}, "book": {"bids": [], "asks": []}, "bars": {}, "levels": {}}, bars[:21])
    assert q and q["kind"] == "first"       # the first-trade question needs no model
    assert eng.hypothesis["rules"] == []
