""""Idea -> deterministic strategy" generation, using Claude as the model
and agent_tools as the tool layer.

Separate from the ongoing chat/analysis loop (still stubbed in main.py's
/api/chat/stream, pending a decision on the Hermes runtime) — this covers
the described workflow's first step: the user supplies name/symbol/
direction plus a free-text idea, and the model turns that into entry
conditions + stop/target by calling agent_tools.create_strategy.

A description that fully specifies both entry and exit becomes one
strategy. Anything less — a vague entry, a vague exit, or several named
approaches ("the breakout or the retest, figure out which is better") —
is a question a single create_strategy call can't answer, so the model
builds each plausible reading as its own strategy, backtests them, and
compares them pairwise (compare_backtests takes two job ids, so three
variants run as a knockout and the last comparison decides). The system
prompt branches on that explicitly; the loop below tracks *all*
strategies/backtests created in a run (not just the last one) so it can
build either response shape.

Gracefully "not configured" (raises LLMNotConfigured) when ANTHROPIC_API_KEY
isn't set, same pattern as the existing /api/chat/status stub.
"""

import os

import agent_tools
import nautilus_runner

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_TOOL_ROUNDS = 40

SYSTEM_PROMPT = """You are a quant engineer agent. A trader has given you a plain-English \
strategy idea. Your job is to turn it into deterministic strategies with create_strategy, \
narrow them down to the single best one, then improve that one through controlled \
single-variable backtest experiments — and report what you learned.

Rules:
- Call get_condition_vocabulary first if you're not certain of the exact condition \
  types/params — do not guess or invent a condition type that doesn't exist.
- Use the name, symbol, and direction given to you exactly as provided — do not \
  change them. Only conditions, stop, target, session, and sizing come from your \
  own judgment based on the idea.
- Prefer the simplest rule set that captures the idea. 1-3 entry conditions is \
  normal; don't add conditions the idea doesn't call for.
- Every strategy you create needs a concrete stop and target — never ask the trader \
  for them, and never leave them unset. Which values to try when the description \
  doesn't say is what the A/B branching below decides.
- When a risk-per-trade percentage is given, treat it as the trader's loss tolerance \
  on a single trade: keep the stop tight enough to respect it, and set `sizing` \
  (percent_equity) so a stop-out costs roughly that share of the account rather than \
  leaving the default full-equity size in place.

PHASE 1 — narrow to exactly one strategy.

Read the description first:

A) It pins down BOTH a specific entry trigger AND a specific exit (stop and/or \
   target), and names only one approach. Call create_strategy once, then \
   run_backtest on it — that is your baseline champion.

B) Anything else — the description leaves the entry vague, leaves the exit vague, \
   or names several approaches ("the breakout or the retest, which is better") — \
   the trader has delegated that choice to you, so don't guess at one reading:
   1. Call create_strategy once per variant — same symbol/direction/interval, name \
      suffixed with what makes it different, e.g. "(20-bar breakout)", "(2R target)". \
      Vary only the unspecified part: if the entry is given but the exit isn't, all \
      variants share that entry and differ in stop/target, and vice versa. Two \
      variants is usually enough; never build more than three.
   2. Call run_backtest on each one.
   3. Compare them with compare_backtests, which takes exactly two job ids — with \
      three variants run it as a knockout: compare the first two, then compare that \
      winner against the third.
   4. The survivor is your champion. Rank by expectancy (R) and profit factor, not \
      raw win rate — compare_backtests' `verdict` already accounts for that, so \
      defer to it. If the evidence is too thin to separate them, take the one with \
      the better expectancy and say plainly that the gap wasn't significant.

Either way you leave Phase 1 with ONE champion strategy and its backtest.

THE GOAL — what you are optimising toward, and when to stop.

The trader wants the strategy to return between 2% and 5% in the MAJORITY of the \
weeks it trades, and to finish positive overall. A losing day, or a losing week, is \
expected and fine — finishing down is not. get_weekly_performance(job_id) scores any \
backtest against exactly that and hands back `meetsGoal` and `verdict`; it is the \
pass/fail check, so call it on every backtest you judge and let it, not your own \
impression of the equity curve, decide whether you are done.

PHASE 2 — improve that one champion, scientifically.

Now run controlled experiments on the champion. The whole point is to learn what \
actually moves performance, so each experiment isolates a single cause:

- ONE VARIABLE AT A TIME. Every propose_strategy_revision must change exactly one \
  thing from the current champion: one condition's parameter, OR the stop type/value, \
  OR the target value, OR the session window, OR sizing. Never two at once — if you \
  change two and the result improves, you have learned nothing about which one did it.
- Form a hypothesis before each run, and put it in `rationale`: the variable, the \
  direction you are moving it, and what you expect ("widen ATR stop 1.5 -> 2.0; the \
  losers are mostly stop-outs that later reached target").
- Let the evidence pick the variable. Call compare_winners_vs_losers and \
  find_near_miss_entries on the champion's backtest to see where it is actually \
  losing, and test that variable rather than guessing at random. Log what you learn \
  with log_finding.
- Each experiment is: propose_strategy_revision (one change, off the CURRENT \
  champion) -> run_backtest -> compare_backtests against the current champion's job.
- Keep the winner. If the revision wins, it becomes the new champion and the next \
  experiment builds on it. If it doesn't, discard it and keep revising the previous \
  champion — a failed experiment is still a result, so report it.
- Stop as soon as get_weekly_performance says `meetsGoal` is true — that is the \
  finish line, not a checkpoint. Don't keep tuning a strategy that already qualifies.
- Otherwise run at most 5 experiments, and use your judgement to stop sooner: two or \
  three in a row that fail to improve the champion means you are out of ideas that \
  the data supports, and more runs will only overfit. Too few trades or too few weeks \
  to tell the difference is also a reason to stop, not a reason to run more.

FINISH — call finalize_strategy with the id of the champion and one line on why. It \
replies with the champion's real weekly scorecard; use those numbers in your report, \
not your own recollection.

Then write a plain-English report covering: what Phase 1 tested and which approach \
won, each Phase 2 experiment (the variable, the change, its effect, kept or \
discarded), the final rules, and where the strategy landed against the weekly goal.

If you run out of experiments without reaching the goal, say so outright: lead with \
"This strategy is not profitable" (or that it does not reach the 2-5% weekly target, \
if it is positive but short of it), give the actual weekly numbers, and list the \
variables you tested. Do NOT keep looping past your budget hoping for a better draw, \
do not present a losing strategy as promising, and do not pick the best of several \
bad runs and call it a success. A clear negative result is a useful answer; a \
flattering one is not.

Never call compare_backtests with only one strategy backtested, never skip \
run_backtest before comparing, and never change more than one variable per revision.

If the trader's message says the backtest engine is unavailable, none of this is \
possible: build the single strategy you judge most likely to work, call \
finalize_strategy on it, skip every backtest/compare/revision, and say in your \
report which alternatives and which variables you would have tested.
"""


# Not an agent_tools tool — it changes nothing, it just lets the model name the
# strategy this run settled on, so the envelope doesn't have to guess which of the
# variants and revisions is the one to load into the editor.
FINALIZE_TOOL = {
    "name": "finalize_strategy",
    "description": (
        "Declare which strategy this run settled on, after narrowing the candidates "
        "and running your single-variable experiments. Call it exactly once, last, "
        "with the id of the champion — that is the strategy the trader sees. It "
        "replies with that strategy's weekly scorecard (meetsGoal, verdict), which "
        "is what your report must be based on."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy_id": {"type": "string"},
            "reason": {"type": "string", "description": "one line on why this one won"},
        },
        "required": ["strategy_id", "reason"],
    },
}


class LLMNotConfigured(Exception):
    pass


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMNotConfigured("ANTHROPIC_API_KEY is not set")
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def generate_strategy(
    name: str, symbol: str, direction: str, prompt: str, interval: str = "1min",
    risk: float | str | None = None,
) -> dict:
    """Run a bounded tool-calling loop against Claude: build candidates from
    `prompt`, backtest them down to one champion, then improve that champion with
    single-variable experiments.

    Always returns the champion — {"strategy": {...}} , or {"directionGroup",
    "long", "short"} when direction == "both" and create_strategy saved a pair —
    plus "explanation", and whichever of these the run produced:
    - "variants": the Phase 1 candidates, each with its backtest summary.
    - "experiments": the Phase 2 revisions, each with its backtest summary and the
      `rationale` (the one variable it changed and why).
    - "comparison": the last compare_backtests result.
    - "goal": the champion's scorecard against the trader's target (2-5% in the
      majority of weeks traded, positive overall) — absent if nothing was
      backtested, e.g. the engine isn't installed.
    """
    client = _client()

    risk_line = f"risk per trade: {risk}% of account equity\n" if risk not in (None, "") else ""
    # Branch B needs real backtests; without the engine the model has to stay
    # on A rather than burning its tool rounds on run_backtest errors.
    engine_line = "" if nautilus_runner.engine_status().get("installed") else (
        "NOTE: the backtest engine is unavailable — you cannot run or compare backtests.\n"
    )
    user_msg = (
        f"name: {name!r}\nsymbol: {symbol!r}\ndirection: {direction!r}\ninterval: {interval!r}\n"
        f"{risk_line}{engine_line}\n"
        f"Strategy description:\n{prompt}"
    )
    messages = [{"role": "user", "content": user_msg}]

    created: list[dict] = []          # every successful create_strategy result, in order
    revised: list[dict] = []          # every propose_strategy_revision — the experiments
    jobs_by_strategy_id: dict = {}    # strategy id -> run_backtest result
    comparison = None                 # last compare_backtests result
    final_id = None                   # what finalize_strategy named

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=MODEL, max_tokens=1536, system=SYSTEM_PROMPT,
            tools=agent_tools.ANTHROPIC_TOOLS + [FINALIZE_TOOL], messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            text = "".join(b.text for b in response.content if b.type == "text")
            if not created:
                raise RuntimeError(f"model did not call create_strategy; final message: {text!r}")
            return _envelope(created, revised, jobs_by_strategy_id, comparison, final_id, text)

        tool_results = []
        for call in tool_calls:
            if call.name == "finalize_strategy":
                final_id = call.input.get("strategy_id")
                # Hand the real scorecard back rather than a bare ack, so the
                # closing report is written against the numbers, not a memory
                # of them — including when they say the strategy failed.
                result = {"finalized": final_id, **_goal_scorecard(final_id, jobs_by_strategy_id)}
            else:
                result = agent_tools.call_tool(call.name, call.input)
            if call.name == "create_strategy" and "error" not in result:
                created.append(result)
            elif call.name == "propose_strategy_revision" and "error" not in result:
                revised.append(result)  # carries its own `rationale` — the hypothesis it tested
            elif call.name == "run_backtest" and "error" not in result:
                jobs_by_strategy_id[call.input.get("strategy_id")] = result
            elif call.name == "compare_backtests" and "error" not in result:
                comparison = result
            tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": str(result)})
        messages.append({"role": "user", "content": tool_results})

    if created:
        return _envelope(created, revised, jobs_by_strategy_id, comparison, final_id, None)
    raise RuntimeError(f"model did not produce a strategy within {MAX_TOOL_ROUNDS} tool-call rounds")


def _envelope(
    created: list[dict], revised: list[dict], jobs_by_strategy_id: dict,
    comparison: dict | None, final_id: str | None, explanation: str | None,
) -> dict:
    """Fold a whole run — candidates, experiments, comparisons — into the one
    shape the UI renders: the champion in the editor, everything that was tried
    underneath it."""
    def with_backtest(s: dict) -> dict:
        return {**s, "backtest": jobs_by_strategy_id.get(s.get("id"))}

    out: dict = {"explanation": explanation}
    if len(created) >= 2:
        out["variants"] = [with_backtest(s) for s in created]
    if revised:
        out["experiments"] = [with_backtest(s) for s in revised]
    if comparison is not None:
        out["comparison"] = comparison

    pool = {s["id"]: s for s in created + revised if s.get("id")}
    champion = pool.get(final_id) or _best_by_expectancy(pool, jobs_by_strategy_id)
    if champion is None:
        # No finalize_strategy call and nothing backtested (engine down, or the
        # model ran out of rounds) — fall back to the last thing it built.
        champion = (revised or created)[-1]

    if "directionGroup" in champion:
        # create_strategy's two-sided save has no single id to load.
        return {**out, **champion}
    goal = _goal_scorecard(champion.get("id"), jobs_by_strategy_id)
    if goal:
        out["goal"] = goal
    return {**out, "strategy": champion}


def _goal_scorecard(strategy_id: str | None, jobs_by_strategy_id: dict) -> dict:
    """How the strategy did against the trader's goal — 2-5% in the majority of
    weeks traded, positive overall — computed from its backtest rather than
    taken on the model's word. Empty when it was never backtested (no engine),
    since there is then nothing to judge."""
    job = jobs_by_strategy_id.get(strategy_id)
    if not job or not job.get("id"):
        return {}
    try:
        weekly = agent_tools.get_weekly_performance(job["id"])
    except Exception:
        return {}
    return {k: weekly[k] for k in (
        "meetsGoal", "verdict", "weeksTraded", "weeksAtOrAboveTarget", "weeksInTargetBand",
        "positiveWeeks", "targetHitRate", "netReturnPct", "endsPositive", "warnings",
    ) if k in weekly}


def _best_by_expectancy(pool: dict, jobs_by_strategy_id: dict) -> dict | None:
    """Highest-expectancy backtested strategy, as a stand-in when the model
    never named a champion. Expectancy (R) for the same reason
    compare_backtests ranks on it — it accounts for risk sizing."""
    best, best_score = None, None
    for sid, strategy in pool.items():
        job = jobs_by_strategy_id.get(sid)
        if not job or not job.get("id"):
            continue
        try:
            score = agent_tools.get_backtest_analytics(job["id"]).get("expectancyR")
        except Exception:
            continue
        if score is not None and (best_score is None or score > best_score):
            best, best_score = strategy, score
    return best


# --------------------------------------------------------------------------
# Conversational analyst loop (/api/chat, /api/chat/stream)
#
# Separate from generate_strategy() above, and deliberately so: that function
# is a one-shot with a fixed output envelope the UI renders as structured
# rules. This one is open-ended chat over the same toolset, so it keeps its
# own system prompt and returns prose. They share agent_tools and _client(),
# not a prompt.
# --------------------------------------------------------------------------

CHAT_SYSTEM_PROMPT = """You are a quant analyst embedded in a trading platform, \
talking to the trader who owns it. You have tools that read their real strategies, \
run real NautilusTrader backtests, and analyze real trades.

How to work:
- Ground answers in tool output. If a question can be settled by calling a tool, \
  call it rather than reasoning from memory about what their data probably says.
- Never invent strategy ids, job ids, or numbers. If you need an id, list first.
- Backtests are slow (up to ~3 minutes). Run one when the trader is asking a \
  question that genuinely needs fresh results; don't re-run a backtest whose \
  results you can already read with get_backtest or get_backtest_analytics.
- When evidence is thin — a handful of trades, a comparison the tool itself \
  flags as not significant — say so instead of drawing a confident conclusion. \
  compare_backtests returns a `verdict` field that already accounts for this; \
  defer to it rather than eyeballing win rate.
- Be brief and concrete. This is a side panel, not a report: a few sentences, \
  specific numbers, no restating the question back.

The trader's current chart context (symbol/interval) is provided with their \
message. Treat it as what they're looking at, not as a constraint — if they ask \
about a different symbol, answer about that one.
"""

MAX_CHAT_TOOL_ROUNDS = 8


def chat_status() -> dict:
    """What /api/chat/status reports. Never raises — 'not configured' is a
    normal state the UI renders as an offline badge."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"connected": False, "model": None, "reason": "ANTHROPIC_API_KEY is not set"}
    return {"connected": True, "model": MODEL, "reason": None}


def _chat_messages(messages: list[dict], context: dict | None) -> list[dict]:
    """Frontend history -> Anthropic messages.

    Chart context rides on the last user turn rather than in the system prompt
    because it changes as the trader moves around the app; pinning it to the
    turn it belongs to keeps earlier answers honest about what was on screen
    when they were given.
    """
    wire = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if not wire:
        raise ValueError("No message to send.")
    if context:
        bits = ", ".join(f"{k}={v}" for k, v in context.items() if v)
        if bits:
            wire[-1] = {**wire[-1], "content": f"[chart context: {bits}]\n\n{wire[-1]['content']}"}
    return wire


def stream_chat(messages: list[dict], context: dict | None = None):
    """Yield SSE event dicts for one assistant turn.

    Event shapes are fixed by the frontend's streamChat() reader (api.js):
    {"type": "delta", "text"} | {"type": "tool", "name"} |
    {"type": "error", "message"} | {"type": "done"}.

    Errors are yielded as events, not raised: the response has already
    started streaming by the time most failures happen, so a mid-stream
    exception would leave the UI with a half-written bubble and no
    explanation. The caller closes with "done" either way.
    """
    try:
        client = _client()
        wire = _chat_messages(messages, context)
    except LLMNotConfigured as e:
        yield {"type": "error", "message": f"The assistant is not configured: {e}"}
        return
    except ValueError as e:
        yield {"type": "error", "message": str(e)}
        return

    try:
        for _ in range(MAX_CHAT_TOOL_ROUNDS):
            with client.messages.stream(
                model=MODEL, max_tokens=1536, system=CHAT_SYSTEM_PROMPT,
                tools=agent_tools.ANTHROPIC_TOOLS, messages=wire,
            ) as stream:
                for text in stream.text_stream:
                    yield {"type": "delta", "text": text}
                response = stream.get_final_message()

            tool_calls = [b for b in response.content if b.type == "tool_use"]
            if not tool_calls:
                return

            wire.append({"role": "assistant", "content": response.content})
            results = []
            for call in tool_calls:
                yield {"type": "tool", "name": call.name}
                result = agent_tools.call_tool(call.name, call.input)
                results.append({
                    "type": "tool_result", "tool_use_id": call.id, "content": str(result),
                })
            wire.append({"role": "user", "content": results})

        yield {"type": "error", "message": (
            f"Stopped after {MAX_CHAT_TOOL_ROUNDS} tool rounds without a final answer."
        )}
    except Exception as e:
        yield {"type": "error", "message": f"Assistant error: {type(e).__name__}: {e}"}


def chat(messages: list[dict], context: dict | None = None) -> dict:
    """Non-streaming sibling of stream_chat, for /api/chat and the backtest
    insights route. Collapses the same event stream into one message so the
    two paths can't drift in behaviour."""
    text, error = [], None
    for event in stream_chat(messages, context):
        if event["type"] == "delta":
            text.append(event["text"])
        elif event["type"] == "error":
            error = event["message"]
    content = "".join(text).strip()
    if error and not content:
        return {"role": "assistant", "content": error, "error": True}
    return {"role": "assistant", "content": content, "error": False}
