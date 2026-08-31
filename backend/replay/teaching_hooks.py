"""Glue between a `ReplaySession` and teaching mode (PLATFORM-SPEC.md Phase 6).

Keeps a `FeatureContext` fed with the replayed bars/prints/book so every
snapshot carries the full primitive feature vector, persists trades and
marks, and runs the hypothesis engine off the replay thread — questions come
back through `session.command({"type": "_question", ...})`, which the
session turns into a `question` message (and a pause, when configured).
"""

from __future__ import annotations

import asyncio
import threading
import traceback

from engine.features import BarRec, FeatureContext, Trade
from teaching import snapshot as snap_mod
from teaching import store

NS = 1_000_000_000


class TeachingHooks:
    def __init__(self, session_id: str, *, symbol: str, root: str, tick_size: float, rth_start: str = "09:30",
                 rth_end: str = "16:00", hypothesis=None, pause_on_question: bool = True, loop=None):
        self.session_id = session_id
        self.symbol, self.root, self.tick = symbol, root, tick_size
        self.ctx = FeatureContext("1min", ["5min", "15min"], tick_size=tick_size, rth_start=rth_start, rth_end=rth_end)
        self.hyp = hypothesis
        self.pause_on_question = pause_on_question
        self.loop = loop
        self.session = None
        self._fed_until = None
        self._lock = threading.Lock()
        self.trade_ids: dict[str, str] = {}   # sim position id -> teaching trade id
        self.questions: list[dict] = []
        self.pending_threads: list[threading.Thread] = []

    # -- feeds (replay thread) -----------------------------------------------------
    def attach(self, session) -> None:
        self.session = session
        self.loop = self.loop or asyncio.get_event_loop()
        self.warm(session.closed_history.get("1min") or [])

    def warm(self, bars_1m: list[dict]) -> None:
        for b in bars_1m:
            if self._fed_until is not None and b["time"] <= self._fed_until:
                continue
            self.on_bar_closed("1min", b)

    def on_bar_closed(self, tf: str, b: dict) -> None:
        if tf != "1min":
            return
        rec = BarRec(b["open"], b["high"], b["low"], b["close"], b.get("volume", 0), b.get("delta", 0), b.get("buyVol", 0),
                     b.get("sellVol", 0), b["time"] * NS, (b["time"] + 60) * NS)
        self.ctx.on_bar(rec)
        self._fed_until = b["time"]

    def on_trade(self, ts: int, price: float, size: int, side: str) -> None:
        self.ctx.on_trade(Trade(int(ts), float(price), int(size), side))

    def on_book(self, bids, asks, ts: int) -> None:
        try:
            self.ctx.set_book([(float(p), float(s)) for p, s in bids[:20]], [(float(p), float(s)) for p, s in asks[:20]], ts)
        except Exception:
            pass

    # -- events --------------------------------------------------------------------
    def _snapshot(self, kind: str, ts: int, key: str, position=None, trade=None, extra=None) -> tuple[dict, str]:
        s = self.session
        bars = {tf: s.closed_history[tf] + [b.to_dict() for b in s.bars[tf]] for tf in s.layers.bars}
        one = bars.get("1min") or []
        cvd_series = [{"time": b["time"], "cvd": b["cvd"]} for b in one if b.get("cvd") is not None]
        book = s.book.top(20) if s.book_mode else (s.src.approx_book(s.clock_ts) or ([], []))
        snap = snap_mod.build(ts=ts, kind=kind, symbol=self.symbol, root=self.root, bars=bars,
                              footprints=self._closed_footprints(), live_footprint=s._footprint_payload(),
                              book=book, trades=s.last_trades, vap=s.vap, cvd_series=cvd_series, ctx=self.ctx,
                              position=position, trade=trade, extra=extra)
        path = snap_mod.write(self.session_id, key, snap)
        return snap, path

    def _closed_footprints(self) -> dict:
        return dict(getattr(self.session, "closed_footprints", {}) or {})

    def on_fill(self, position: dict, ts: int) -> None:
        """Entry fill: persist the trade, snapshot, then hypothesise in a thread."""
        tr = store.add_trade(self.session_id, direction=position["direction"], entry_ts=position["entryTs"],
                             entry_price=position["entryPrice"], stop=position.get("stop"), target=position.get("target"),
                             contracts=position.get("contracts", 1), trade_id=position["id"])
        self.trade_ids[position["id"]] = tr["id"]
        snap, path = self._snapshot("entry", ts, f"{tr['id']}", position=position)
        store.update_trade(tr["id"], snapshot_path=path)
        tr["snapshotPath"] = path
        if self.hyp is not None:
            bars = list(self.session.closed_history.get("1min") or [])
            self._run_async(lambda: self.hyp.on_trade(tr, snap, bars), ts)

    def on_exit(self, trade: dict, ts: int) -> None:
        tid = self.trade_ids.get(trade["id"], trade["id"])
        store.close_trade(tid, exit_ts=trade["exitTs"], exit_price=trade["exitPrice"], exit_reason=trade["reason"], pnl_usd=trade["pnl"])
        self._snapshot("exit", ts, f"{tid}.exit", trade=trade)
        if self.hyp is not None:
            for t in self.hyp.trades:
                if t["id"] == tid:
                    t.update({"exitPrice": trade["exitPrice"], "exitReason": trade["reason"], "pnlUsd": trade["pnl"]})

    def on_annotate(self, trade_id: str, confidence=None, note=None) -> None:
        tid = self.trade_ids.get(trade_id, trade_id)
        fields = {}
        if confidence is not None:
            fields["confidence"] = int(confidence)
        if note is not None:
            fields["note"] = str(note)
        if fields:
            store.update_trade(tid, **fields)
        if self.hyp is not None:
            for t in self.hyp.trades:
                if t["id"] == tid:
                    t.update({k: v for k, v in fields.items()})

    def on_modify(self, position: dict) -> None:
        tid = self.trade_ids.get(position["id"])
        if tid:
            store.update_trade(tid, stop=position.get("stop"), target=position.get("target"))

    def on_mark(self, kind: str, payload: dict | None, ts: int) -> dict:
        payload = dict(payload or {})
        ev = store.add_event(self.session_id, ts, kind if kind in ("skipped_setup", "level", "annotation") else "annotation",
                             {**payload, "source": "user"})
        if kind == "skipped_setup":
            _, path = self._snapshot("mark", ts, f"mark-{ev['id']}", extra={"mark": payload})
            if self.hyp is not None:
                self.hyp.on_mark(ts, {**payload, "snapshotPath": path})
        return ev

    def on_answer(self, question_id: str, text: str, label: str | None = None) -> None:
        if self.hyp is not None:
            self.hyp.on_answer(question_id, text, label)
        else:
            store.answer_question(question_id, text)

    # -- threading -------------------------------------------------------------------
    def _run_async(self, fn, ts: int) -> None:
        def worker():
            try:
                q = fn()
            except Exception as e:  # noqa: BLE001
                store.add_event(self.session_id, ts, "annotation", {"note": f"hypothesis error: {e}", "trace": traceback.format_exc()[-1500:]})
                return
            if q:
                self._deliver(q)

        t = threading.Thread(target=worker, daemon=True, name=f"teach-{self.session_id}")
        self.pending_threads.append(t)
        t.start()

    def _deliver(self, q: dict) -> None:
        self.questions.append(q)
        msg = {"type": "_question", "id": q["id"], "kind": q["kind"], "text": q["question"], "tradeId": q.get("tradeId"),
               "pauseReplay": self.pause_on_question, "payload": q.get("payload") or {}}
        if self.session is None:
            return
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.session.command, msg)
        else:
            self.session.command(msg)

    def join(self, timeout: float = 10.0) -> None:
        for t in self.pending_threads:
            t.join(timeout)
