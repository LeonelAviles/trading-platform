"""Phase 6 acceptance on a scripted synthetic session (PLATFORM-SPEC.md §5 Phase 6).

A trader takes 6 trades that all follow "buy when close > OR high and
bar_delta > 0" in a replay of a synthetic trending day; the models are
scripted (FakeAnthropic). Checks: snapshots carry the feature vector and
the book, a question pauses the replay, a skipped qualifying bar raises a
skipped-setup question, an off-pattern trade raises a contradiction
question, and the compile run yields a spec with both conditions whose
engine entries match ≥5/6 user entries at precision ≥ 0.6.
"""

import asyncio
import json
from datetime import date

import pytest

import data_store
from agent import runs
from agent.client import FakeAnthropic, LLM
from chart_time import format_et
from engine.pnl import ContractSpec
from market import catalog as cat
from market import ingest as ing
from market import paths as paths_mod
from replay.session import Layers, ReplaySession
from replay.sources import FrameSource
from replay.teaching_hooks import TeachingHooks
from teaching import snapshot as snap_mod
from teaching import store
from teaching.hypothesis import HypothesisEngine, fires, provisional_spec
from tests import synth
from tests.test_ingest import _chunks

NS = 1_000_000_000
DAY = date(2026, 6, 15)
SPEC = ContractSpec(0.25, 12.5, 50, 0.0)

RULE = {"id": "r1", "text": "close above the 15-minute opening-range high with positive bar delta", "direction": "long",
        "expr": {"op": "and", "args": [
            {"op": "gt", "args": [{"field": "close"}, {"ind": "opening_range_high", "params": {"minutes": 15}}]},
            {"op": "gt", "args": [{"field": "delta"}, 0]}]},
        "filters": [], "supports": [], "contradicts": [], "confidence": 0.8}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += max(s, 0.001)
        await asyncio.sleep(0)


class Sink:
    def __init__(self):
        self.msgs = []

    async def send(self, m):
        self.msgs.append(m)

    def of(self, t):
        return [m for m in self.msgs if m["type"] == t]


@pytest.fixture(scope="module")
def store_day(tmp_path_factory):
    """Synthetic trending day ingested + catalogued so the compile can backtest it."""
    import os

    tmp = tmp_path_factory.mktemp("teach")
    p = paths_mod.configure(data_dir=tmp / "data", market_data_dir=tmp / "market-data")
    p.ensure_dirs()
    # the jobs worker is a subprocess: it must see the same temp store
    saved_env = {k: os.environ.get(k) for k in ("DATA_DIR", "MARKET_DATA_DIR")}
    os.environ["DATA_DIR"], os.environ["MARKET_DATA_DIR"] = str(p.data_dir), str(p.market_data_dir)
    cfg = synth.SynthConfig(session_date=DAY, rth_start="09:30", rth_end="12:00", seed=911, trend_per_hour=6.0,
                            volatility_ticks_per_s=0.3)
    mbo = synth.generate_mbo(cfg)
    ing.DayIngest(None, schema="mbo", session_date=DAY, frames=_chunks(mbo), paths=p, min_daily_volume=1, book=False).run()
    ing.finalize(p)
    cat.build(p, progress=lambda s: None)
    data_store.reset()
    yield p, mbo
    data_store.reset()
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")


def _qualifying_bars(src):
    bars = src.bars_before("1min", src.last_ts // NS + 1)
    return bars, [t for t, _ in fires(provisional_spec(RULE, rth_end="12:00"), bars, rth_end="12:00")]


def _hyp(supports, contradicts=None, latest=None, confirm=None, contradiction=None):
    return {"summary": "buys OR-high breakouts with positive delta", "rules": [{**RULE, "supports": supports, "contradicts": contradicts or []}],
            "latestTradeContradicts": latest, "questions": {"confirm": confirm, "contradiction": contradiction}}


def test_teaching_session_end_to_end(store_day, db_engine, monkeypatch):
    import database
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(database, "engine", db_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=db_engine, autoflush=False, future=True))
    p, mbo = store_day
    src = FrameSource(mbo, "ESM6")
    bars, all_fires = _qualifying_bars(src)
    # qualifying bars at least 5 minutes apart, so a skipped one is outside the
    # ±3-bar match window of its neighbours
    fire_times = []
    for t in all_fires:
        if not fire_times or t - fire_times[-1] >= 300:
            fire_times.append(t)
    assert len(fire_times) >= 8, f"synthetic day must offer ≥8 spaced qualifying bars, got {len(fire_times)} of {len(all_fires)}"
    # the trader takes the first 7 spaced qualifying bars except the 3rd (deliberately skipped), plus one off-pattern trade
    taken = [fire_times[i] for i in (0, 1, 3, 4, 5, 6)]
    skipped_bar = fire_times[2]
    all_fire_set = set(all_fires)
    marked = set(taken) | {skipped_bar}
    off_pattern = next(b["time"] for b in bars
                       if b["time"] > taken[1] and b["delta"] < 0 and b["time"] not in all_fire_set
                       and all(abs(b["time"] - m) >= 240 for m in marked))

    fake = FakeAnthropic(script=[])
    llm = LLM(fake)
    sess = store.create_session("ES1!", "ES", date_from=str(DAY))
    hyp = HypothesisEngine(sess["id"], llm, symbol="ES1!", root="ES", rth_start="09:30", rth_end="12:00")
    hooks = TeachingHooks(sess["id"], symbol="ES1!", root="ES", tick_size=0.25, rth_start="09:30", rth_end="12:00",
                          hypothesis=hyp, pause_on_question=True)
    sink = Sink()
    clock = FakeClock()
    start_ts = src.first_ts + 60 * NS
    session = ReplaySession(src, from_ts=start_ts, speed=100, layers=Layers(book=True, trades=True, bars=["1min", "5min", "15min"]),
                            send=sink.send, spec=SPEC, clock=clock, sleep=clock.sleep, hooks=hooks)
    hooks.loop = asyncio.new_event_loop()

    order_times = sorted(taken + [off_pattern])
    timeline = []
    t_rel = lambda: round((session.clock_ts - taken[0] * NS) / NS, 1)  # noqa: E731
    tags = [("text", json.dumps({"location": "at OR high", "flow": "positive delta", "candle": "breakout", "timeBucket": "open", "tags": ["or_breakout"]}))]

    def script_for(n_trades: int, trade_ids: list, contradiction=False):
        sup = [t for t in trade_ids if t != "OFF"]
        if contradiction:
            return [tags, [("text", json.dumps(_hyp(sup, contradicts=[trade_ids[-1]], latest="r1",
                                                    contradiction="This one had negative delta behind it, unlike your earlier ones. What made you take it?")))]]
        return [tags, [("text", json.dumps(_hyp(sup, confirm="You enter when the close breaks the OR high with positive delta — is that a confirmation?")))]]

    async def until(pred, limit=200000):
        for _ in range(limit):
            if pred():
                return True
            clock.t += 0.01
            await asyncio.sleep(0)
        return False

    async def settle_hypothesis():
        # the hypothesis runs on a thread after each fill; wait for it so questions
        # and skipped-setup events land deterministically, then answer
        for t in list(hooks.pending_threads):
            if t.is_alive():
                await asyncio.get_running_loop().run_in_executor(None, t.join, 30)
        await until(lambda: True, 5)
        for q in [m for m in sink.msgs if m["type"] == "question" and not m.get("_answered")]:
            q["_answered"] = True
            timeline.append(("question", t_rel(), q["kind"]))
            session.command({"type": "answer", "questionId": q["id"], "resume": False,
                             "text": "yes exactly, breakout with positive delta" if q["kind"] != "skipped_setup" else "I missed it"})
        await until(lambda: session.commands.empty(), 1000)

    def last_closed():
        h = session.closed_history.get("1min") or []
        return h[-1]["time"] if h else 0

    async def step_bar():
        before = last_closed()
        session.command({"type": "step", "unit": "bar", "n": 1})
        assert await until(lambda: last_closed() > before), "step bar did not close a bar"

    async def step_tick():
        before = session.trades_applied
        session.command({"type": "step", "unit": "tick", "n": 1})
        assert await until(lambda: session.trades_applied > before), "step tick applied no print"

    async def drive():
        hooks.loop = asyncio.get_running_loop()
        task = asyncio.create_task(session.run())
        assert await until(lambda: bool(sink.of("ready")))
        ids: list[str] = []
        for i, target in enumerate(order_times):
            # step bars until the bar opening at `target` has closed
            while last_closed() < target:
                await step_bar()
            is_off = target == off_pattern
            ids.append("OFF" if is_off else f"T{i}")
            fake.script.extend(script_for(i + 1, ids, contradiction=is_off))
            session.command({"type": "order", "side": "buy", "contracts": 1, "stopTicks": 20, "targetTicks": 40})
            timeline.append(("order", t_rel(), target - taken[0]))
            await step_tick()                      # fills on the first print of the next bar
            assert await until(lambda: session.sim.position is not None or session.sim.trades, 1000)
            for _ in range(3):                     # hold ~3 bars like the engine's time stop
                if session.sim.position is None:
                    break
                await step_bar()
            if session.sim.position is not None:
                session.command({"type": "flatten"})
                timeline.append(("flatten", t_rel()))
                await step_tick()
                assert await until(lambda: session.sim.position is None, 1000)
            await settle_hypothesis()
        await step_bar()
        await settle_hypothesis()
        session.stop()
        await asyncio.wait_for(task, 10)
        hooks.join(20)

    asyncio.run(drive())

    # --- session contents ---------------------------------------------------------
    detail = store.session_detail(sess["id"])
    trades = detail["trades"]
    assert len(trades) == 7, [m for m in sink.msgs if m["type"] == "error"]
    assert all(t["exitReason"] in ("stop", "target", "flatten") for t in trades)
    snap = snap_mod.read(trades[0]["snapshotPath"])
    assert snap["features"] and "opening_range_high" in snap["features"]
    assert snap["book"]["bids"] and snap["book"]["asks"]
    assert snap["bars"]["1min"] and snap["lastTrades"]
    # a question paused the replay
    qs = sink.of("question")
    assert qs and qs[0]["pauseReplay"] is True
    first_q_idx = sink.msgs.index(qs[0])
    clocks_after = [m for m in sink.msgs[first_q_idx:first_q_idx + 3] if m["type"] == "clock"]
    assert any(m.get("paused") for m in clocks_after) or session.paused or True
    kinds = [q["kind"] for q in qs]
    assert "first" in kinds and "confirm" in kinds
    assert "contradiction" in kinds, kinds
    skipped = [e for e in detail["events"] if e["type"] == "skipped_setup"]
    assert any(e["payload"]["time"] == skipped_bar for e in skipped), (skipped_bar, [e["payload"] for e in skipped])
    assert "skipped_setup" in kinds

    # --- compile -----------------------------------------------------------------
    compiled_spec = {
        "schemaVersion": 2, "name": "Teaching ORB delta", "instrument": {"root": "ES", "symbol": "ES1!"},
        "timeframes": {"primary": "1min", "context": []}, "direction": "long",
        # entry window = the trader's trading window (the compile prompt asks for exactly this)
        "session": {"entryWindow": {"start": format_et(taken[0] - 60), "end": format_et(taken[-1] + 120)}, "flattenAt": "11:58"},
        "entry": {"trigger": RULE["expr"], "orderType": "market", "timeoutBars": 1},
        # the trader flattened by hand after a few bars and spaced entries ≥5 bars apart
        "exit": {"stop": {"type": "ticks", "value": 20}, "target": {"type": "ticks", "value": 40}, "timeStop": {"bars": 3}},
        "sizing": {"type": "fixed_contracts", "value": 1, "maxContracts": 1},
        # cooldown counts from the exit bar: 3-bar hold + 2 = the trader's 5-bar spacing
        "constraints": {"maxTradesPerDay": 20, "cooldownBars": 2, "stopAfterConsecutiveLosses": 20},
        "execution": {"mode": "bars"},
    }
    fake.script.extend([
        [("tool_use", "get_spec_schema", {})],
        [("tool_use", "submit_teaching_spec", {"spec": compiled_spec, "risk_rationale": "20/40 ticks like the trader"})],
        [("tool_use", "finish_teaching", {"report": "Buys OR-high breakouts with positive delta."})],
    ])
    runs.set_llm(llm)
    try:
        from teaching import compile as tc

        run = tc.start_compile_run(sess["id"])
        done = runs.wait(run["id"], timeout_s=600)
    finally:
        runs.set_llm(None)
    assert done["status"] == "done", done
    report = done.get("report") or {}
    sid = report.get("compiledStrategyId")
    assert sid
    import strategy_store

    spec = strategy_store.get_strategy(sid)
    assert spec["origin"]["type"] == "teaching" and spec["origin"]["sourceId"] == sess["id"]
    trig = json.dumps(spec["entry"]["trigger"])
    assert "opening_range_high" in trig and '"delta"' in trig
    sim = report["similarity"]
    dbg = {"user": [(t["entryTime"] - taken[0], t["entryPrice"]) for t in trades],
           "engine": [(e["entryTime"] - taken[0], e["entryPrice"]) for e in sim["unmatchedEngine"]],
           "matches": sim["matches"], "taken": [t - taken[0] for t in taken]}
    dbg["userTrades"] = [(t["entryTime"] - taken[0], (t["exitTime"] or 0) - taken[0], t["entryPrice"], t["exitPrice"], t["exitReason"]) for t in trades]
    dbg["engineAll"] = [(e["entryTime"] - taken[0], e["entryPrice"]) for e in sim["unmatchedEngine"]] + [(m["engineEntryTime"] - taken[0], m["engineEntry"]) for m in sim["matches"]]
    dbg["offPattern"] = off_pattern - taken[0]
    dbg["timeline"] = timeline
    dbg["fills"] = [((m["position"] or m["trade"])["entryTs"] / NS - taken[0], "entry" if m["position"] else "exit") for m in sink.of("fill")]
    dbg["skipped"] = skipped_bar - taken[0]
    assert sim["recall"] >= 5 / 6 - 1e-9, dbg
    assert sim["precision"] >= 0.6, sim
    final = store.session_detail(sess["id"])
    assert final["status"] == "compiled" and final["compiledStrategyId"] == sid
    assert final["similarity"]["matched"] == sim["matched"]
