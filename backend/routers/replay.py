"""Replay routes (PLATFORM-SPEC.md §4.11): `/ws/replay`, the replay-cache
warmer and its listing. One active session — a new `start` replaces it."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import data_store
from engine.pnl import ContractSpec
from replay import warm
from replay.session import Layers, ReplaySession
from replay.sources import DaySource

router = APIRouter(tags=["replay"])
NS = 1_000_000_000

# warm progress per (root, date)
_warm_state: dict[str, dict] = {}
_warm_lock = threading.Lock()
_active: dict = {"session": None, "task": None}


def _key(root: str, d: str) -> str:
    return f"{root}:{d}"


def _warm_thread(root: str, d: str) -> None:
    key = _key(root, d)

    def progress(pct: int):
        with _warm_lock:
            _warm_state[key]["pct"] = pct

    try:
        warm.warm_day(root, d, progress=progress)
        with _warm_lock:
            _warm_state[key].update({"status": "done", "pct": 100})
        data_store.reset()
    except Exception as e:  # noqa: BLE001
        with _warm_lock:
            _warm_state[key].update({"status": "error", "error": str(e)})


def start_warm(root: str, d: str) -> dict:
    key = _key(root, d)
    with _warm_lock:
        cur = _warm_state.get(key)
        if cur and cur["status"] == "running":
            return cur
        if warm.is_cached(root, d):
            _warm_state[key] = {"status": "done", "pct": 100}
            return _warm_state[key]
        if warm.raw_file_for(root, d) is None:
            raise HTTPException(404, f"no raw MBO file for {root} {d} (restore it with scripts/restore.py)")
        _warm_state[key] = {"status": "running", "pct": 0, "startedAt": datetime.now(timezone.utc).isoformat()}
    threading.Thread(target=_warm_thread, args=(root, d), daemon=True, name=f"warm-{key}").start()
    return _warm_state[key]


class WarmBody(BaseModel):
    root: str
    date: str


@router.post("/api/data/replay-cache/warm")
def warm_endpoint(body: WarmBody):
    return {"root": body.root, "date": body.date, **start_warm(body.root, body.date)}


@router.get("/api/data/replay-cache")
def replay_cache():
    p = warm.get_paths()
    days = warm.list_cached(p)
    with _warm_lock:
        warming = {k: dict(v) for k, v in _warm_state.items() if v["status"] != "done"}
    return {"days": days, "bytes": sum(d["bytes"] for d in days), "capGb": p.replay_cache_max_gb, "warming": warming}


@router.delete("/api/data/replay-cache/{root}/{d}")
def evict_day(root: str, d: str):
    import shutil

    dd = warm.day_dir(root, d)
    if not dd.exists():
        raise HTTPException(404, "not cached")
    shutil.rmtree(dd, ignore_errors=True)
    return {"ok": True}


# ----------------------------------------------------------------------------

async def _wait_warm(ws: WebSocket, root: str, d: str) -> bool:
    key = _key(root, d)
    last = -1
    while True:
        with _warm_lock:
            st = dict(_warm_state.get(key) or {"status": "done", "pct": 100})
        if st["status"] == "error":
            await ws.send_json({"type": "error", "message": f"warm failed: {st.get('error')}"})
            return False
        if st["pct"] != last:
            await ws.send_json({"type": "preparing", "pct": st["pct"]})
            last = st["pct"]
        if st["status"] == "done":
            return True
        await asyncio.sleep(0.5)


async def _start(ws: WebSocket, msg: dict) -> ReplaySession | None:
    symbol = msg.get("symbol") or "ES1!"
    from_ts = int(msg.get("fromTs") or 0)
    if from_ts <= 0:
        await ws.send_json({"type": "error", "message": "fromTs (unix ns) is required"})
        return None
    layers = Layers.from_dict(msg.get("layers"))
    day = datetime.fromtimestamp(from_ts / NS, tz=timezone.utc).date()
    try:
        src = DaySource(symbol, day)
    except HTTPException as e:
        await ws.send_json({"type": "error", "message": str(e.detail)})
        return None
    if layers.book and not src.has_mbo():
        try:
            start_warm(src.root, str(day))
        except HTTPException as e:
            await ws.send_json({"type": "preparing", "pct": 100, "note": f"no MBO for this day ({e.detail}); book layer off"})
            layers.book = False
        else:
            if not await _wait_warm(ws, src.root, str(day)):
                return None
            data_store.reset()
    if src.has_mbo():
        warm.touch(src.root, day)
    spec = ContractSpec.from_root(src.spec)
    hooks = None
    tsid = msg.get("teachingSessionId")
    if tsid:
        hooks = _teaching_hooks(tsid, src, msg)
        if hooks is None:
            await ws.send_json({"type": "error", "message": f"teaching session {tsid!r} not found"})
            return None
    session = ReplaySession(src, from_ts=from_ts, speed=float(msg.get("speed") or 1), layers=layers,
                            send=ws.send_json, spec=spec, teaching_session_id=tsid, hooks=hooks)
    return session


def _teaching_hooks(session_id: str, src: DaySource, msg: dict):
    from replay.teaching_hooks import TeachingHooks
    from teaching import store as tstore
    from teaching.hypothesis import HypothesisEngine

    sess = tstore.get_session(session_id)
    if sess is None:
        return None
    defaults = msg.get("teaching") or {}
    from config.instruments import load_instruments

    ins = load_instruments()
    hyp = HypothesisEngine(session_id, symbol=src.requested_symbol, root=src.root, tick_size=src.tick_size,
                           rth_start=ins.session.rth_start, rth_end=ins.session.rth_end,
                           stop_ticks=int(defaults.get("stopTicks") or 20), target_ticks=int(defaults.get("targetTicks") or 40))
    if not sess.get("dateFrom"):
        tstore.update_session(session_id, date_from=str(src.day))
    return TeachingHooks(session_id, symbol=src.requested_symbol, root=src.root, tick_size=src.tick_size,
                         rth_start=ins.session.rth_start, rth_end=ins.session.rth_end, hypothesis=hyp,
                         pause_on_question=bool(defaults.get("pauseOnQuestion", True)), loop=asyncio.get_event_loop())


def _cancel_active() -> None:
    s, t = _active["session"], _active["task"]
    if s is not None:
        s.stop()
    if t is not None and not t.done():
        t.cancel()
    _active["session"] = None
    _active["task"] = None


@router.websocket("/ws/replay")
async def replay_ws(ws: WebSocket):
    await ws.accept()
    session: ReplaySession | None = None
    task: asyncio.Task | None = None
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "start":
                _cancel_active()
                if task is not None and not task.done():
                    task.cancel()
                session = await _start(ws, msg)
                if session is None:
                    continue
                task = asyncio.create_task(session.run())
                _active["session"], _active["task"] = session, task
                if msg.get("autoplay", True):
                    session.command({"type": "resume"})
            elif t == "ping":
                await ws.send_json({"type": "pong"})
            elif session is None:
                await ws.send_json({"type": "error", "message": "send start first"})
            else:
                session.command(msg)
    except WebSocketDisconnect:
        pass
    finally:
        if session is not None:
            session.stop()
        if task is not None:
            try:
                await asyncio.wait_for(task, 2)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                task.cancel()
        if _active["session"] is session:
            _active["session"], _active["task"] = None, None
        if session is not None and hasattr(session.src, "close"):
            session.src.close()
