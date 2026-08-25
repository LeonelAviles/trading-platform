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
