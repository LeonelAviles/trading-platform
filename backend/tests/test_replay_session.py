"""Event ordering, coalescing and control of the replay session with a fake clock."""

import asyncio

import pytest

from engine.pnl import ContractSpec
from replay.session import Layers, ReplaySession
from replay.sources import FrameSource

NS = 1_000_000_000
SPEC = ContractSpec(0.25, 12.5, 50, 0.0)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += max(s, 0.001)
        await asyncio.sleep(0)


def _session(mbo, sink, **kw):
    src = FrameSource(mbo, "ESM6")
    clock = FakeClock()
    from_ts = kw.pop("from_ts", src.first_ts + 5 * 60 * NS)
    layers = kw.pop("layers", Layers(book=True, trades=True, bars=["1min", "5min"]))
    s = ReplaySession(src, from_ts=from_ts, speed=kw.pop("speed", 1), layers=layers, send=sink.send, spec=SPEC,
                      clock=clock, sleep=clock.sleep, **kw)
    return s, src, clock


class Sink:
    def __init__(self):
        self.msgs = []

    async def send(self, m):
        self.msgs.append(m)

    def of(self, t):
        return [m for m in self.msgs if m["type"] == t]


async def _run_for(s, clock, seconds, *, cmds=()):
    task = asyncio.create_task(s.run())
    for c in cmds:
        s.command(c)
    end = clock.t + seconds
    while clock.t < end and not task.done():
        # a paused session never sleeps, so the fake clock must tick here too
        clock.t += 0.005
        await asyncio.sleep(0)
    s.stop()
    await asyncio.wait_for(task, 5)


def test_ready_then_ordered_events(synth_mbo):
    sink = Sink()
    s, src, clock = _session(synth_mbo, sink)

    async def go():
        await _run_for(s, clock, 30, cmds=[{"type": "resume"}])

    asyncio.run(go())
    assert sink.msgs[0]["type"] == "ready"
    ready = sink.msgs[0]
    assert ready["bookMode"] == "l3" and ready["book"]["bids"] and ready["book"]["asks"]
    assert set(ready["bars"]) == {"1min", "5min"}
    # from_ts sits exactly on a minute boundary: no print of that minute has
    # been replayed yet, so the newest bar is the previous closed one.
    assert ready["bars"]["1min"][-1]["time"] == (s.from_ts // NS) // 60 * 60 - 60
    assert all(a["time"] < b["time"] for a, b in zip(ready["bars"]["1min"], ready["bars"]["1min"][1:]))
    trades = [t for m in sink.of("trades") for t in m["items"]]
    assert len(trades) > 20
    assert all(a["ts"] <= b["ts"] for a, b in zip(trades, trades[1:]))
    assert all(t["ts"] >= s.from_ts for t in trades)
    clocks = [m["ts"] for m in sink.of("clock")]
    assert clocks == sorted(clocks)
    # 30 wall seconds at 1x -> ~30 exchange seconds
    assert abs((s.clock_ts - s.from_ts) / NS - 30) < 1.5


def test_coalescing_rates(synth_mbo):
    sink = Sink()
    s, src, clock = _session(synth_mbo, sink, speed=5)

    async def go():
        await _run_for(s, clock, 20, cmds=[{"type": "resume"}])

    asyncio.run(go())
    wall = 20
    assert len(sink.of("book")) <= wall * 10 + 2
    partial = [m for m in sink.of("bar") if not m["closed"]]
    assert len(partial) <= wall * 4 * 2 + 4
    assert len(sink.of("footprint")) <= wall * 2 + 2
    closed = [m for m in sink.of("bar") if m["closed"] and m["tf"] == "1min"]
    # 20 wall s at 5x = 100 exchange s -> at least one 1-minute close, none duplicated
    assert len(closed) >= 1
    assert len({m["bar"]["time"] for m in closed}) == len(closed)
    # a closed bar's numbers equal the reference 1-minute bar
    ref = {b["time"]: b for b in src.bars_before("1min", src.last_ts // NS + 1)}
    for m in closed:
        r = ref[m["bar"]["time"]]
        assert (m["bar"]["open"], m["bar"]["high"], m["bar"]["low"], m["bar"]["close"], m["bar"]["volume"]) == (
            r["open"], r["high"], r["low"], r["close"], r["volume"])
        assert m["bar"]["delta"] == r["delta"]


def test_seek_lands_on_timestamp(synth_mbo):
    sink = Sink()
    s, src, clock = _session(synth_mbo, sink)
    target = src.first_ts + 21 * 60 * NS + 7 * NS

    async def go():
        await _run_for(s, clock, 3, cmds=[{"type": "seek", "ts": target}])

    asyncio.run(go())
    readies = sink.of("ready")
    assert len(readies) == 2
    r = readies[1]
    assert r["clock"] == target
    assert r["bars"]["1min"][-1]["time"] == (target // NS) // 60 * 60
    # book equals the brute-force reference at the seek point
    from tests import synth
    ref_bids, _ = synth.book_at(synth_mbo, "ESM6", target - 1, depth=5)
    assert [[round(p, 4), v] for p, v in ref_bids] == [[round(p, 4), v] for p, v in r["book"]["bids"][:5]]
    # cvd equals the sum of deltas of every print before the target
    t = src._trades[src._trades["ts_recv"] < target]
    assert r["cvd"] == int((t["side"] == "B").mul(t["size"]).sum() - (t["side"] == "A").mul(t["size"]).sum())


def test_step_tick_and_bar(synth_mbo):
    sink = Sink()
    s, src, clock = _session(synth_mbo, sink)

    async def go():
        await _run_for(s, clock, 2, cmds=[{"type": "step", "unit": "tick", "n": 1}, {"type": "step", "unit": "tick", "n": 2},
                                          {"type": "step", "unit": "bar", "n": 1}])

    asyncio.run(go())
    trades = [t for m in sink.of("trades") for t in m["items"]]
    # the first two step commands produced exactly three prints before the bar step
    first_three = sorted(src._trades[src._trades["ts_recv"] >= s.from_ts]["ts_recv"].head(3).tolist())
    assert [t["ts"] for t in trades[:3]] == first_three
    closed = [m for m in sink.of("bar") if m["closed"] and m["tf"] == "1min"]
    assert len(closed) == 1
    assert closed[0]["bar"]["time"] == (s.from_ts // NS) // 60 * 60


def test_speed_above_25_degrades_book_to_approx(synth_mbo):
    sink = Sink()
    s, src, clock = _session(synth_mbo, sink)

    async def go():
        await _run_for(s, clock, 6, cmds=[{"type": "resume"}, {"type": "speed", "value": 100}])

    asyncio.run(go())
    modes = sink.of("mode")
    assert modes and modes[-1]["bookMode"] == "approx"
    approx = [m for m in sink.of("book") if m.get("approx")]
    assert approx and all(m["bids"] for m in approx)
    # trades keep flowing in the degraded mode, in order
    trades = [t for m in sink.of("trades") for t in m["items"]]
    assert len(trades) > 50 and all(a["ts"] <= b["ts"] for a, b in zip(trades, trades[1:]))


def test_closed_footprint_matches_bar(synth_mbo):
    sink = Sink()
    s, src, clock = _session(synth_mbo, sink, speed=5)

    async def go():
        await _run_for(s, clock, 30, cmds=[{"type": "resume"}])

    asyncio.run(go())
    closed_fp = [m for m in sink.of("footprint") if m["closed"]]
    closed_bars = {m["bar"]["time"]: m["bar"] for m in sink.of("bar") if m["closed"] and m["tf"] == "1min"}
    assert closed_fp
    for fp in closed_fp:
        bar = closed_bars[fp["time"]]
        assert sum(l["bid"] + l["ask"] for l in fp["levels"]) == bar["volume"]
        assert sum(l["ask"] - l["bid"] for l in fp["levels"]) == bar["delta"]
        assert fp["poc"] is not None


def test_ws_protocol_end_to_end(client, synth_mbo, monkeypatch):
    """`/ws/replay` over a FrameSource: preparing/ready/clock/trades, pause, seek."""
    from routers import replay as rr

    class Src(FrameSource):
        def __init__(self, symbol, day):
            super().__init__(synth_mbo, "ESM6")
            from config.instruments import load_instruments

            self.spec = load_instruments().roots["ES"]

    monkeypatch.setattr(rr, "DaySource", Src)
    src = FrameSource(synth_mbo, "ESM6")
    from_ts = src.first_ts + 3 * 60 * NS
    with client.websocket_connect("/ws/replay") as ws:
        ws.send_json({"type": "start", "symbol": "ES1!", "fromTs": from_ts, "speed": 10,
                      "layers": {"book": True, "trades": True, "bars": ["1min"]}})
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["symbol"] == "ESM6" and ready["bookMode"] == "l3"
        seen = set()
        for _ in range(60):
            m = ws.receive_json()
            seen.add(m["type"])
            if {"clock", "trades", "book"} <= seen:
                break
        assert {"clock", "trades", "book"} <= seen
        ws.send_json({"type": "pause"})
        target = src.first_ts + 12 * 60 * NS
        ws.send_json({"type": "seek", "ts": target})
        for _ in range(200):
            m = ws.receive_json()
            if m["type"] == "ready":
                assert m["clock"] == target
                break
        else:
            raise AssertionError("no ready after seek")
        ws.send_json({"type": "speed", "value": 3})
        for _ in range(50):
            m = ws.receive_json()
            if m["type"] == "error":
                assert "speed" in m["message"]
                break
        else:
            raise AssertionError("no error for an invalid speed")
