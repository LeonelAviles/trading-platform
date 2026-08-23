""""Idea -> deterministic strategy" generation, using Claude as the model
and agent_tools as the tool layer.

Separate from the ongoing chat/analysis loop (still stubbed in main.py's
/api/chat/stream, pending a decision on the Hermes runtime) — this covers
the described workflow's first step: the user supplies name/symbol/
direction plus a free-text idea, and the model turns that into entry
conditions + stop/target by calling agent_tools.create_strategy.

Most ideas describe one approach and stop there. But when an idea names
multiple distinct entry approaches and asks which is better ("the breakout
or the retest, figure out which one is better"), a single create_strategy
call can't answer that — the model needs to build each as its own
strategy, backtest each, and compare them. The system prompt branches on
that explicitly rather than trying to force everything through one
create_strategy call; the loop below tracks *all* strategies/backtests
created in a run (not just the last one) so it can build either response
shape.

Gracefully "not configured" (raises LLMNotConfigured) when ANTHROPIC_API_KEY
isn't set, same pattern as the existing /api/chat/status stub.
"""

import os

import agent_tools

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_TOOL_ROUNDS = 14

SYSTEM_PROMPT = """You are a quant engineer agent. A trader has given you a plain-English \
strategy idea; your job is to translate it into one or more deterministic strategies using \
create_strategy, then briefly explain what you built.

Rules:
- Call get_condition_vocabulary first if you're not certain of the exact condition \
  types/params — do not guess or invent a condition type that doesn't exist.
- Use the name, symbol, and direction given to you exactly as provided — do not \
  change them. Only conditions, stop, target, session, and sizing come from your \
  own judgment based on the idea.
- Prefer the simplest rule set that captures the idea. 1-3 entry conditions is \
  normal; don't add conditions the idea doesn't call for.
- If the idea is vague on stop/target, pick sensible defaults (e.g. ATR-based stop, \
  2R target) rather than asking — the trader can adjust in the UI afterward.

Most ideas describe ONE approach — call create_strategy once and stop there. After \
it succeeds, respond with a short (2-4 sentence) plain-English explanation of the \
rules you chose (the trader will also see the structured rules rendered in the UI).

Only when the idea explicitly names multiple distinct entry approaches and asks you \
to compare them or pick the better one — e.g. "the breakout or the retest, figure out \
which one is better" — do this instead:
1. Call create_strategy once per approach (same name suffixed "(<approach>)", same \
   symbol/direction/interval, each with its own conditions).
2. Call run_backtest on each one.
3. Call compare_backtests on the two resulting job ids.
4. Respond with a verdict grounded in what compare_backtests actually returned — \
   if it says evidence is insufficient (too few trades, not statistically \
   significant), say so plainly rather than picking a winner anyway. Don't just \
   restate win rate; expectancy (R) and profit factor matter more, and \
   compare_backtests' `verdict` field already accounts for that — defer to it.
Never call compare_backtests with only one strategy created, and never skip \
run_backtest before comparing — you need real trades to compare, not just the rules.
"""


class LLMNotConfigured(Exception):
    pass


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMNotConfigured("ANTHROPIC_API_KEY is not set")
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def generate_strategy(name: str, symbol: str, direction: str, prompt: str, interval: str = "1min") -> dict:
    """Run a bounded tool-calling loop against Claude to turn `prompt` into
    one or more saved, and possibly backtested-and-compared, strategies.

    Returns one of:
    - {"strategy": {...}, "explanation": "..."} — the common case, one approach.
    - {"directionGroup", "long", "short", "explanation"} — direction == "both"
      (create_strategy's own two-sided save; still one approach).
    - {"variants": [{...strategy, "backtest": {...job summary}}, ...],
       "comparison": {...compare_backtests result...}, "explanation": "..."}
      — the idea named multiple approaches and the model compared them.
    """
    client = _client()

    user_msg = (
        f"name: {name!r}\nsymbol: {symbol!r}\ndirection: {direction!r}\ninterval: {interval!r}\n\n"
        f"Strategy idea:\n{prompt}"
    )
    messages = [{"role": "user", "content": user_msg}]

    created: list[dict] = []          # every successful create_strategy result, in order
    jobs_by_strategy_id: dict = {}    # strategy id -> run_backtest result
    comparison = None

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=MODEL, max_tokens=1536, system=SYSTEM_PROMPT,
            tools=agent_tools.ANTHROPIC_TOOLS, messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            text = "".join(b.text for b in response.content if b.type == "text")
            if not created:
                raise RuntimeError(f"model did not call create_strategy; final message: {text!r}")
            return _envelope(created, jobs_by_strategy_id, comparison, text)

        tool_results = []
        for call in tool_calls:
            result = agent_tools.call_tool(call.name, call.input)
            if call.name == "create_strategy" and "error" not in result:
                created.append(result)
            elif call.name == "run_backtest" and "error" not in result:
                jobs_by_strategy_id[call.input.get("strategy_id")] = result
            elif call.name == "compare_backtests" and "error" not in result:
                comparison = result
            tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": str(result)})
        messages.append({"role": "user", "content": tool_results})

    if created:
        return _envelope(created, jobs_by_strategy_id, comparison, None)
    raise RuntimeError(f"model did not produce a strategy within {MAX_TOOL_ROUNDS} tool-call rounds")


def _envelope(created: list[dict], jobs_by_strategy_id: dict, comparison: dict | None, explanation: str | None) -> dict:
    if comparison is not None and len(created) >= 2:
        variants = [{**s, "backtest": jobs_by_strategy_id.get(s.get("id"))} for s in created]
        return {"variants": variants, "comparison": comparison, "explanation": explanation}

    generated = created[-1]
    if "directionGroup" in generated:
        return {**generated, "explanation": explanation}
    return {"strategy": generated, "explanation": explanation}
