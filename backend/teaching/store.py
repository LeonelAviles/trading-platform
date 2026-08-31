"""SQLite persistence for teaching sessions (tables from §4.7)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

import database
from models import TeachingEvent, TeachingQuestion, TeachingSession, TeachingTrade


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_dict(r: TeachingSession) -> dict:
    return {
        "id": r.id, "symbol": r.symbol, "root": r.root, "dateFrom": r.date_from, "dateTo": r.date_to, "status": r.status,
        "notes": r.notes, "compiledStrategyId": r.compiled_strategy_id, "similarity": r.similarity_json, "createdAt": r.created_at,
    }


def _trade_dict(r: TeachingTrade) -> dict:
    return {
        "id": r.id, "sessionId": r.session_id, "direction": r.direction, "entryTs": r.entry_ts, "entryTime": r.entry_ts // 1_000_000_000,
        "entryPrice": r.entry_price, "stopPrice": r.stop_price, "targetPrice": r.target_price,
        "exitTs": r.exit_ts, "exitTime": (r.exit_ts // 1_000_000_000) if r.exit_ts else None, "exitPrice": r.exit_price,
        "exitReason": r.exit_reason, "pnlUsd": r.pnl_usd, "contracts": r.contracts, "confidence": r.confidence,
        "note": r.user_note, "snapshotPath": r.snapshot_path,
    }


def _event_dict(r: TeachingEvent) -> dict:
    return {"id": r.id, "sessionId": r.session_id, "ts": r.ts, "type": r.type, "payload": r.payload_json or {}}


def _question_dict(r: TeachingQuestion) -> dict:
    return {"id": r.id, "sessionId": r.session_id, "tradeId": r.trade_id, "replayTs": r.replay_ts, "kind": r.kind,
            "question": r.question, "answer": r.answer, "askedAt": r.asked_at, "answeredAt": r.answered_at}


# -- sessions ------------------------------------------------------------------

def create_session(symbol: str, root: str, date_from: str | None = None, notes: str | None = None) -> dict:
    with database.session_scope() as db:
        r = TeachingSession(symbol=symbol, root=root, date_from=date_from, notes=notes, status="active")
        db.add(r)
        db.flush()
        return _session_dict(r)


def get_session(session_id: str) -> dict | None:
    with database.session_scope() as db:
        r = db.get(TeachingSession, session_id)
        return _session_dict(r) if r else None


def list_sessions(limit: int = 50) -> list[dict]:
    with database.session_scope() as db:
        rows = db.execute(select(TeachingSession).order_by(TeachingSession.created_at.desc()).limit(limit)).scalars().all()
        out = []
        for r in rows:
            d = _session_dict(r)
            d["trades"] = db.execute(select(TeachingTrade).where(TeachingTrade.session_id == r.id)).scalars().all().__len__()
            out.append(d)
        return out


def update_session(session_id: str, **fields) -> dict:
    mapping = {"status": "status", "date_to": "date_to", "date_from": "date_from", "notes": "notes",
               "compiled_strategy_id": "compiled_strategy_id", "similarity_json": "similarity_json"}
    with database.session_scope() as db:
        r = db.get(TeachingSession, session_id)
        if r is None:
            raise KeyError(session_id)
        for k, v in fields.items():
            setattr(r, mapping[k], v)
        db.flush()
        return _session_dict(r)


def session_detail(session_id: str) -> dict | None:
    with database.session_scope() as db:
        r = db.get(TeachingSession, session_id)
        if r is None:
            return None
        trades = db.execute(select(TeachingTrade).where(TeachingTrade.session_id == session_id).order_by(TeachingTrade.entry_ts)).scalars().all()
        events = db.execute(select(TeachingEvent).where(TeachingEvent.session_id == session_id).order_by(TeachingEvent.ts, TeachingEvent.id)).scalars().all()
        qs = db.execute(select(TeachingQuestion).where(TeachingQuestion.session_id == session_id).order_by(TeachingQuestion.asked_at)).scalars().all()
        d = _session_dict(r)
        d["trades"] = [_trade_dict(t) for t in trades]
        d["events"] = [_event_dict(e) for e in events]
        d["questions"] = [_question_dict(q) for q in qs]
        runs = [e for e in d["events"] if e["type"] == "compile_started"]
        d["compileRunId"] = runs[-1]["payload"].get("runId") if runs else None
        return d


# -- trades --------------------------------------------------------------------

def add_trade(session_id: str, *, direction: str, entry_ts: int, entry_price: float, stop: float | None, target: float | None,
              contracts: int = 1, confidence: int | None = None, note: str | None = None, trade_id: str | None = None) -> dict:
    with database.session_scope() as db:
        r = TeachingTrade(session_id=session_id, direction=direction, entry_ts=int(entry_ts), entry_price=float(entry_price),
                          stop_price=stop, target_price=target, contracts=int(contracts), confidence=confidence, user_note=note)
        if trade_id:
            r.id = trade_id
        db.add(r)
        db.flush()
        return _trade_dict(r)


def close_trade(trade_id: str, *, exit_ts: int, exit_price: float, exit_reason: str, pnl_usd: float) -> dict | None:
    with database.session_scope() as db:
        r = db.get(TeachingTrade, trade_id)
        if r is None:
            return None
        r.exit_ts, r.exit_price, r.exit_reason, r.pnl_usd = int(exit_ts), float(exit_price), exit_reason, float(pnl_usd)
        db.flush()
        return _trade_dict(r)


def update_trade(trade_id: str, **fields) -> dict | None:
    mapping = {"snapshot_path": "snapshot_path", "confidence": "confidence", "note": "user_note",
               "stop": "stop_price", "target": "target_price"}
    with database.session_scope() as db:
        r = db.get(TeachingTrade, trade_id)
        if r is None:
            return None
        for k, v in fields.items():
            setattr(r, mapping[k], v)
        db.flush()
        return _trade_dict(r)


def trades(session_id: str) -> list[dict]:
    with database.session_scope() as db:
        rows = db.execute(select(TeachingTrade).where(TeachingTrade.session_id == session_id).order_by(TeachingTrade.entry_ts)).scalars().all()
        return [_trade_dict(t) for t in rows]


# -- events / questions ----------------------------------------------------------

def add_event(session_id: str, ts: int, type_: str, payload: dict | None = None) -> dict:
    with database.session_scope() as db:
        r = TeachingEvent(session_id=session_id, ts=int(ts), type=type_, payload_json=payload or {})
        db.add(r)
        db.flush()
        return _event_dict(r)


def events(session_id: str, type_: str | None = None) -> list[dict]:
    with database.session_scope() as db:
        q = select(TeachingEvent).where(TeachingEvent.session_id == session_id)
        if type_:
            q = q.where(TeachingEvent.type == type_)
        rows = db.execute(q.order_by(TeachingEvent.ts, TeachingEvent.id)).scalars().all()
        return [_event_dict(e) for e in rows]


def add_question(session_id: str, kind: str, question: str, *, trade_id: str | None = None, replay_ts: int | None = None) -> dict:
    with database.session_scope() as db:
        r = TeachingQuestion(session_id=session_id, kind=kind, question=question, trade_id=trade_id, replay_ts=replay_ts)
        db.add(r)
        db.flush()
        return _question_dict(r)


def answer_question(question_id: str, text: str) -> dict | None:
    with database.session_scope() as db:
        r = db.get(TeachingQuestion, question_id)
        if r is None:
            return None
        r.answer, r.answered_at = text, _now()
        db.flush()
        return _question_dict(r)


def questions(session_id: str) -> list[dict]:
    with database.session_scope() as db:
        rows = db.execute(select(TeachingQuestion).where(TeachingQuestion.session_id == session_id).order_by(TeachingQuestion.asked_at)).scalars().all()
        return [_question_dict(q) for q in rows]
