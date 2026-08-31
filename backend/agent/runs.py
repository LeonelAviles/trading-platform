"""AgentRun — the persisted, resumable agent loop (PLATFORM-SPEC.md §5 Phase 4 task 1).

    queued → running ⇄ paused_for_user → done | error | budget_exhausted | cancelled

State (`agent_runs.state_json`) holds the whole conversation (content blocks
as plain dicts), the ids the run created, tool bookkeeping (change budget,
consecutive non-improvements, OOS reveals) and an event log, and is
persisted after every tool round — a backend restart calls `resume_pending()`
and continues from the last tool boundary. Progress and questions are pushed
to subscribers (`/ws/agent/:runId` polls `events()`), and `ask_user` flips
the run to `paused_for_user` until `answer()` arrives.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timezone

import database
from agent import client as llm_client
from models import AgentRun as AgentRunRow, new_id, utc_now

MAX_ROUNDS = 60
MAX_EVENTS = 400
_threads: dict[str, threading.Thread] = {}
_cancel: set[str] = set()
_lock = threading.Lock()
_llm_override = None   # tests inject an LLM built on FakeAnthropic


def set_llm(llm) -> None:
    global _llm_override
    _llm_override = llm


def _llm():
    return _llm_override or llm_client.LLM()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# Rows
# ----------------------------------------------------------------------------

def _row_to_dict(r: AgentRunRow, with_state: bool = False) -> dict:
    st = r.state_json or {}
    d = {
        "id": r.id, "kind": r.kind, "status": r.status, "input": r.input_json, "question": r.question_json, "answer": r.answer_json,
        "tokensIn": r.tokens_in, "tokensOut": r.tokens_out, "costUsd": round(r.cost_usd, 4), "createdAt": r.created_at, "updatedAt": r.updated_at,
        "progress": {k: st.get(k) for k in ("phase", "round", "changesUsed", "changeBudget", "nonImprovements", "championId", "createdIds",
                                             "revisedIds", "oosLooks", "finalized", "error")},
        "report": st.get("report"), "eventCount": len(st.get("events", [])),
    }
    if with_state:
        d["events"] = st.get("events", [])
        d["state"] = {k: v for k, v in st.items() if k != "messages"}
    return d


def get(run_id: str, with_state: bool = False) -> dict | None:
    with database.session_scope() as db:
        r = db.get(AgentRunRow, run_id)
        return _row_to_dict(r, with_state) if r else None


def list_runs(limit: int = 50) -> list[dict]:
    with database.session_scope() as db:
        rows = db.query(AgentRunRow).order_by(AgentRunRow.created_at.desc()).limit(limit).all()
        return [_row_to_dict(r) for r in rows]


def events(run_id: str, since: int = 0) -> list[dict]:
    with database.session_scope() as db:
        r = db.get(AgentRunRow, run_id)
        if r is None:
            return []
        ev = (r.state_json or {}).get("events", [])
        return [e for e in ev if e["seq"] > since]


def _load(run_id: str) -> tuple[dict, dict]:
    with database.session_scope() as db:
        r = db.get(AgentRunRow, run_id)
        if r is None:
            raise KeyError(run_id)
        return dict(r.input_json or {}), json.loads(json.dumps(r.state_json or {}))


def _save(run_id: str, state: dict, **fields) -> None:
    with database.session_scope() as db:
        r = db.get(AgentRunRow, run_id)
        if r is None:
            return
        r.state_json = state
        for k, v in fields.items():
            setattr(r, k, v)
        r.updated_at = _now()


def _emit(state: dict, run_id: str, type_: str, **payload) -> None:
    ev = state.setdefault("events", [])
    seq = (ev[-1]["seq"] + 1) if ev else 1
    ev.append({"seq": seq, "ts": _now(), "type": type_, **payload})
    if len(ev) > MAX_EVENTS:
        del ev[: len(ev) - MAX_EVENTS]


# ----------------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------------

def create(kind: str, input_: dict) -> dict:
    run_id = new_id()
    with database.session_scope() as db:
        db.add(AgentRunRow(id=run_id, kind=kind, status="queued", input_json=input_, state_json={"events": [], "phase": "queued"},
                           created_at=utc_now(), updated_at=utc_now()))
    return get(run_id)


def start(run_id: str) -> dict:
    with _lock:
        t = _threads.get(run_id)
        if t is not None and t.is_alive():
            return get(run_id)
        t = threading.Thread(target=_loop, args=(run_id,), daemon=True, name=f"agent-run-{run_id}")
        _threads[run_id] = t
        t.start()
    return get(run_id)


def start_run(kind: str, input_: dict) -> dict:
    run = create(kind, input_)
    return start(run["id"])


def answer(run_id: str, text: str) -> dict:
    with database.session_scope() as db:
        r = db.get(AgentRunRow, run_id)
        if r is None:
            raise KeyError(run_id)
        if r.status != "paused_for_user":
            raise ValueError(f"run {run_id} is {r.status}, not paused")
        st = dict(r.state_json or {})
        pending = st.get("pendingToolUse")
        if pending:
            st.setdefault("messages", []).append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": pending, "content": json.dumps({"answer": text})}]})
            st["pendingToolUse"] = None
        st.setdefault("answers", []).append({"question": r.question_json, "answer": text, "ts": _now()})
        _emit(st, run_id, "answer", text=text)
        r.state_json = st
        r.answer_json = {"text": text, "ts": _now()}
        r.question_json = None
        r.status = "queued"
        r.updated_at = _now()
    return start(run_id)


def cancel(run_id: str) -> dict:
    _cancel.add(run_id)
    with database.session_scope() as db:
        r = db.get(AgentRunRow, run_id)
        if r and r.status in ("queued", "paused_for_user", "running"):
            st = dict(r.state_json or {})
            _emit(st, run_id, "cancelled")
            r.state_json = st
            r.status = "cancelled"
            r.updated_at = _now()
    return get(run_id)


def resume_pending() -> list[str]:
    """Restart runs that were mid-flight when the backend went down."""
    ids = []
    with database.session_scope() as db:
        for r in db.query(AgentRunRow).filter(AgentRunRow.status.in_(["queued", "running"])).all():
            ids.append(r.id)
    for rid in ids:
        start(rid)
    return ids


def is_alive(run_id: str) -> bool:
    t = _threads.get(run_id)
    return t is not None and t.is_alive()


# ----------------------------------------------------------------------------
# The loop
# ----------------------------------------------------------------------------

def _loop(run_id: str) -> None:
    from agent import flows

    try:
        input_, state = _load(run_id)
    except KeyError:
        return
    kind = get(run_id)["kind"]
    flow = flows.get_flow(kind)
    llm = _llm()
    if not state.get("messages"):
        state = flow.init_state(input_, state)
        _emit(state, run_id, "started", kind=kind)
    _save(run_id, state, status="running")
    try:
        for _ in range(MAX_ROUNDS):
            # Cancel may come from this process (flag) or from another one (row status).
            if run_id in _cancel or (get(run_id) or {}).get("status") == "cancelled":
                _cancel.discard(run_id)
                _emit(state, run_id, "cancelled")
                _save(run_id, state, status="cancelled")
                return
            state["round"] = state.get("round", 0) + 1
            response = llm.create(purpose=f"agent.{kind}", system=flow.system_prompt(input_, state), messages=state["messages"],
                                  tools=flow.tools(input_, state), max_tokens=flow.max_tokens, agent_run_id=run_id)
            blocks = [llm_client.block_to_dict(b) for b in response.content]
            state["messages"].append({"role": "assistant", "content": blocks})
            _accumulate_usage(run_id, response)
            texts = [b["text"] for b in blocks if b.get("type") == "text" and b.get("text")]
            for t in texts:
                _emit(state, run_id, "text", text=t[:4000])
            tool_calls = [b for b in blocks if b.get("type") == "tool_use"]
            stop_reason = getattr(response, "stop_reason", None)
            if not tool_calls and (stop_reason == "max_tokens" or not "".join(texts).strip()):
                # Cut off mid-message (or an empty turn): nudge once per round instead of finishing.
                state["nudges"] = state.get("nudges", 0) + 1
                if state["nudges"] > 3:
                    state["error"] = "model produced no usable output after 3 nudges"
                    _emit(state, run_id, "error", message=state["error"])
                    _save(run_id, state, status="error")
                    return
                _emit(state, run_id, "nudge", reason=stop_reason or "empty")
                state["messages"].append({"role": "user", "content": (
                    "Your last message was cut off by the length limit or was empty. Continue from where you stopped; "
                    "keep tool inputs compact (omit spec fields that equal their defaults).")})
                _save(run_id, state, status="running")
                continue
            if not tool_calls:
                state = flow.finish(input_, state, "\n\n".join(texts))
                _emit(state, run_id, "done", report=state.get("report"))
                _save(run_id, state, status="done")
                return
            results = []
            paused = False
            for call in tool_calls:
                _emit(state, run_id, "tool", name=call["name"], input=_short(call.get("input")))
                outcome = flow.handle_tool(run_id, input_, state, call)
                if outcome.get("pause"):
                    # ask_user: persist the pending tool_use so the answer can be threaded back.
                    state["pendingToolUse"] = call["id"]
                    _emit(state, run_id, "question", question=outcome["question"])
                    # Results for earlier tool calls in this round must be delivered before pausing;
                    # Anthropic requires every tool_use to get a result, so include the ones we have
                    # and leave the ask_user result for `answer()`.
                    if results:
                        state["messages"].append({"role": "user", "content": results})
                        results = []
                    _save(run_id, state, status="paused_for_user", question_json=outcome["question"])
                    paused = True
                    break
                result = outcome["result"]
                _emit(state, run_id, "tool_result", name=call["name"], result=_short(result))
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(result, default=str)[:20000]})
                if outcome.get("stop"):
                    state["messages"].append({"role": "user", "content": results})
                    state = flow.finish(input_, state, outcome.get("report_text"))
                    _emit(state, run_id, "done", report=state.get("report"))
                    _save(run_id, state, status="done")
                    return
            if paused:
                return
            state["messages"].append({"role": "user", "content": results})
            _save(run_id, state, status="running")
        state["error"] = f"stopped after {MAX_ROUNDS} rounds"
        _emit(state, run_id, "error", message=state["error"])
        _save(run_id, state, status="error")
    except llm_client.BudgetExhausted as e:
        state["error"] = str(e)
        _emit(state, run_id, "budget_exhausted", message=str(e))
        _save(run_id, state, status="budget_exhausted")
    except llm_client.LLMNotConfigured as e:
        state["error"] = str(e)
        _emit(state, run_id, "error", message=str(e))
        _save(run_id, state, status="error")
    except Exception as e:  # noqa: BLE001
        state["error"] = f"{type(e).__name__}: {e}"
        state["traceback"] = traceback.format_exc()[-4000:]
        _emit(state, run_id, "error", message=state["error"])
        _save(run_id, state, status="error")


def _accumulate_usage(run_id: str, response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    with database.session_scope() as db:
        r = db.get(AgentRunRow, run_id)
        if r is None:
            return
        r.tokens_in += int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        r.tokens_out += int(getattr(usage, "output_tokens", 0) or 0)
        r.cost_usd += llm_client.estimate_cost(getattr(response, "model", "") or "", int(getattr(usage, "input_tokens", 0) or 0),
                                               int(getattr(usage, "output_tokens", 0) or 0), int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                                               int(getattr(usage, "cache_creation_input_tokens", 0) or 0))


def _short(v, n: int = 600):
    s = json.dumps(v, default=str) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


def wait(run_id: str, timeout_s: float = 60.0, poll: float = 0.1) -> dict:
    """Tests/scripts: block until the run leaves running/queued."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = get(run_id)
        if r and r["status"] not in ("queued", "running"):
            return r
        time.sleep(poll)
    return get(run_id)
