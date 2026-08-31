"""Teaching-mode routes (PLATFORM-SPEC.md §6 / Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

import data_store
from teaching import compile as tc
from teaching import snapshot as snap_mod
from teaching import store

router = APIRouter(prefix="/api/teaching", tags=["teaching"])


@router.get("/sessions")
def list_sessions(limit: int = 50):
    return store.list_sessions(limit)


@router.post("/sessions")
def create_session(body: dict = Body(default={})):
    symbol = body.get("symbol") or "ES1!"
    try:
        spec, _ = data_store.resolve(symbol)
    except HTTPException:
        raise HTTPException(400, f"unknown symbol {symbol!r}")
    return store.create_session(symbol, spec.root, date_from=body.get("dateFrom"), notes=body.get("notes"))


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    d = store.session_detail(session_id)
    if d is None:
        raise HTTPException(404, "session not found")
    return d


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str, body: dict = Body(default={})):
    """End the session and start the compile run; returns {runId}."""
    d = store.session_detail(session_id)
    if d is None:
        raise HTTPException(404, "session not found")
    if body.get("notes"):
        store.update_session(session_id, notes=body["notes"])
    if not d["trades"] and not body.get("force"):
        store.update_session(session_id, status="ended")
        return {"runId": None, "status": "ended", "message": "no trades — nothing to compile"}
    run = tc.start_compile_run(session_id)
    return {"runId": run["id"], "status": "compiling"}


@router.post("/sessions/{session_id}/compile")
def compile_again(session_id: str):
    if store.get_session(session_id) is None:
        raise HTTPException(404, "session not found")
    run = tc.start_compile_run(session_id)
    return {"runId": run["id"], "status": "compiling"}


@router.post("/sessions/{session_id}/answer")
def answer(session_id: str, body: dict = Body(...)):
    q = store.answer_question(body.get("questionId", ""), body.get("text") or "")
    if q is None:
        raise HTTPException(404, "question not found")
    if q["kind"] == "skipped_setup":
        from teaching.hypothesis import label_answer

        store.add_event(session_id, (q.get("replayTs") or 0), "skipped_setup_label",
                        {"label": label_answer(body.get("text") or "", body.get("label")), "reason": body.get("text"), "questionId": q["id"]})
    return q


@router.post("/sessions/{session_id}/annotate")
def annotate(session_id: str, body: dict = Body(...)):
    t = store.update_trade(body.get("tradeId", ""), **{k: body[k] for k in ("confidence", "note") if k in body})
    if t is None:
        raise HTTPException(404, "trade not found")
    return t


@router.post("/sessions/{session_id}/labels")
def label(session_id: str, body: dict = Body(...)):
    """Label an unmatched engine entry: valid_skip | missed | rule_too_loose."""
    if body.get("label") not in ("valid_skip", "missed", "rule_too_loose"):
        raise HTTPException(400, "label must be valid_skip | missed | rule_too_loose")
    return tc.label_false_positive(session_id, int(body.get("entryTime") or 0), body["label"], body.get("reason"))


@router.post("/sessions/{session_id}/pick")
def pick(session_id: str, body: dict = Body(...)):
    if not body.get("strategyId"):
        raise HTTPException(400, "strategyId is required")
    return tc.pick(session_id, body["strategyId"])


@router.get("/sessions/{session_id}/snapshots/{key}")
def snapshot(session_id: str, key: str):
    d = store.session_detail(session_id)
    if d is None:
        raise HTTPException(404, "session not found")
    rel = None
    for t in d["trades"]:
        if t["id"] == key:
            rel = t["snapshotPath"]
        elif f"{t['id']}.exit" == key:
            rel = f"teaching/{session_id}/{key}.json.gz"
    if rel is None and key.startswith("mark-"):
        rel = f"teaching/{session_id}/{key}.json.gz"
    if rel is None:
        raise HTTPException(404, "snapshot not found")
    try:
        return snap_mod.read(rel)
    except FileNotFoundError:
        raise HTTPException(404, "snapshot file missing")
