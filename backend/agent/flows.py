"""Run flows — what each `AgentRun.kind` does per tool round (PLATFORM-SPEC.md §5 Phase 4 task 2).

A flow supplies `init_state`, `system_prompt`, `tools`, `handle_tool` and
`finish`; `agent.runs._loop` drives it. The generate flow enforces the
validation protocol in code, not just in the prompt: variants capped at 6,
`run_backtest` = in-sample + walk-forward, OOS rows invisible until
`finalize_strategy`, a 5-change budget, early stop after 3 consecutive
non-improvements, and one OOS look per lineage.
"""

from __future__ import annotations

import json

import agent_tools
from agent import prompts
from agent import tools_v2 as T2
from knowledge import graph as kg
from knowledge.local_store import format_facts

CHANGE_BUDGET = 5
MAX_VARIANTS = 6
EARLY_STOP_NON_IMPROVEMENTS = 3


def _ok(result: dict) -> bool:
    return isinstance(result, dict) and "error" not in result


class GenerateFlow:
    kind = "generate"
    max_tokens = 8192

    # -- prompt --------------------------------------------------------------
    def init_state(self, input_: dict, state: dict) -> dict:
        prompt = input_.get("prompt", "")
        lines = [f"Strategy description:\n{prompt}", ""]
        if input_.get("name"):
            lines.append(f"name: {input_['name']!r} (use it; suffix variants)")
        lines.append(f"symbol: {input_.get('symbol') or 'ES1!'}")
        lines.append(f"direction: {input_.get('direction') or 'not specified — decide (may be both)'}")
        lines.append(f"primary timeframe: {input_.get('interval') or 'agent choice (one of 1min, 5min, 15min)'}")
        if input_.get("risk"):
            lines.append(f"risk per trade the trader mentioned: {input_['risk']}")
        facts = kg.search(prompt, k=12)
        state.update({
            "messages": [{"role": "user", "content": "\n".join(lines)}],
            "phase": "phase0", "knowledge": facts, "createdIds": [], "revisedIds": [], "jobs": {},
            "changesUsed": 0, "changeBudget": CHANGE_BUDGET, "nonImprovements": 0, "championId": None,
            "oosRevealed": [], "oosLooks": 0, "finalized": False, "ambiguity": None, "citations": [],
        })
        return state

    def system_prompt(self, input_: dict, state: dict):
        return [{"type": "text", "text": prompts.GENERATE_SYSTEM},
                {"type": "text", "text": format_facts(state.get("knowledge") or [])}]

    def tools(self, input_: dict, state: dict) -> list:
        return T2.anthropic_tools(include_ask=True, include_finalize=True, include_start_run=False)

    # -- tool handling ---------------------------------------------------------
    def handle_tool(self, run_id: str, input_: dict, state: dict, call: dict) -> dict:
        name, args = call["name"], dict(call.get("input") or {})
        if name == "ask_user":
            return {"pause": True, "question": {"text": args.get("question", ""), "options": args.get("options") or [], "kind": "agent"}}
        if name == "declare_variants":
            dims = args.get("dimensions") or []
            total = 1
            for d in dims:
                total *= max(1, len(d.get("options") or []))
            if len(dims) > 2 or any(len(d.get("options") or []) > 3 for d in dims) or total > MAX_VARIANTS:
                return {"result": {"error": f"at most 2 dimensions × 3 options and {MAX_VARIANTS} variants (got {len(dims)} dims, {total} variants)"}}
            state["ambiguity"] = dims
            state["phase"] = "phase1"
            return {"result": {"ok": True, "variants": total, "dimensions": dims}}
        if name == "create_strategy":
            if len(state["createdIds"]) >= MAX_VARIANTS:
                return {"result": {"error": f"variant cap of {MAX_VARIANTS} reached; backtest and narrow instead"}}
            spec = args.get("spec") or {}
            spec.setdefault("origin", {})
            spec["origin"] = {"type": "prompt", "sourceId": run_id}
            result = agent_tools.call_tool("create_strategy", {"spec": spec})
            if _ok(result):
                state["createdIds"].append(result["id"])
                if state["championId"] is None:
                    state["championId"] = result["id"]
            return {"result": result}
        if name == "run_backtest":
            result = T2.run_backtest_is_wf(args.get("strategy_id"), args.get("mode"), run_id=run_id)
            if _ok(result):
                state["jobs"][args.get("strategy_id")] = result["isJobId"]
                state["phase"] = "phase1" if not state["revisedIds"] else "phase2"
            return {"result": result}
        if name == "propose_strategy_revision":
            if state["changesUsed"] >= CHANGE_BUDGET:
                return {"result": {"error": f"change budget of {CHANGE_BUDGET} variables used; finalize_strategy or ask_user"}}
            changes = args.get("changes") or {}
            if len(changes) > 1 and not args.get("changed_variable"):
                return {"result": {"error": "one field per revision — name the single changed_variable (a unit pair counts as one)"}}
            result = agent_tools.call_tool("propose_strategy_revision", args)
            if _ok(result):
                state["changesUsed"] += 1
                state["revisedIds"].append(result["id"])
                state["phase"] = "phase2"
                kg.record_experiment(result["id"], args.get("base_strategy_id"), args.get("changed_variable") or ", ".join(changes),
                                     args.get("rationale"), {})
            return {"result": result}
        if name == "compare_backtests":
            result = agent_tools.call_tool("compare_backtests", args)
            if _ok(result):
                self._track_champion(state, args, result)
            return {"result": result}
        if name in ("get_backtest", "get_backtest_analytics", "get_win_rate", "get_weekly_performance", "get_trade_features",
                    "compare_winners_vs_losers", "get_regime_breakdown", "get_monte_carlo", "get_findings"):
            blocked = T2.oos_guard(args.get("job_id"), state)
            if blocked:
                return {"result": blocked}
            return {"result": agent_tools.call_tool(name, args)}
        if name == "finalize_strategy":
            sid = args.get("strategy_id") or state.get("championId")
            result = T2.finalize(sid, args.get("reason", ""), state, run_id=run_id)
            if _ok(result):
                state["finalized"] = True
                state["championId"] = sid
                state["phase"] = "report"
            return {"result": result}
        if name == "search_knowledge":
            result = agent_tools.call_tool(name, args)
            for f in (result.get("result") if isinstance(result, dict) else []) or []:
                state["citations"].append({"id": f.get("id"), "credibility": f.get("credibility"), "source": f.get("source")})
            return {"result": result}
        return {"result": agent_tools.call_tool(name, args)}

    def _track_champion(self, state: dict, args: dict, result: dict) -> None:
        jobs = state["jobs"]
        by_job = {v: k for k, v in jobs.items()}
        a, b = args.get("job_id_a"), args.get("job_id_b")
        winner_job = result.get("winner") or result.get("winnerJobId")
        if winner_job not in (a, b):
            verdict = str(result.get("verdict", "")).lower()
            winner_job = b if ("b" in verdict and "a" not in verdict.split("b")[0][-3:]) else a
        winner = by_job.get(winner_job)
        champion = state.get("championId")
        challenger = by_job.get(b if winner_job == a else a)
        if state["revisedIds"] and challenger in state["revisedIds"]:
            if winner == challenger:
                state["nonImprovements"] = 0
                state["championId"] = winner
            else:
                state["nonImprovements"] = state.get("nonImprovements", 0) + 1
                if state["nonImprovements"] >= EARLY_STOP_NON_IMPROVEMENTS:
                    result["advice"] = f"{EARLY_STOP_NON_IMPROVEMENTS} consecutive experiments did not improve the champion — stop experimenting and finalize (or ask_user)."
        elif winner:
            state["championId"] = winner if champion is None or champion in (by_job.get(a), by_job.get(b)) else champion

    def finish(self, input_: dict, state: dict, text: str | None) -> dict:
        state["report"] = {
            "text": text, "championId": state.get("championId"), "createdIds": state["createdIds"], "revisedIds": state["revisedIds"],
            "ambiguity": state.get("ambiguity"), "changesUsed": state["changesUsed"], "finalized": state.get("finalized"),
            "finalizeResult": state.get("finalizeResult"), "citations": state.get("citations"),
            "knowledgeAvailable": state.get("knowledge"), "jobs": state["jobs"],
        }
        state["phase"] = "done"
        return state


class ChatActionFlow(GenerateFlow):
    """A chat-spawned run: same protocol, the request text is the prompt."""

    kind = "chat_action"


class TeachingCompileFlow(GenerateFlow):
    """Placeholder until Phase 6: compiles a teaching session into a spec via the same loop."""

    kind = "teaching_compile"


_FLOWS = {"generate": GenerateFlow(), "chat_action": ChatActionFlow(), "teaching_compile": TeachingCompileFlow()}


def get_flow(kind: str):
    if kind not in _FLOWS:
        raise ValueError(f"unknown run kind {kind!r}")
    return _FLOWS[kind]
