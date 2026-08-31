"""Anthropic client wrapper — model tiers, prompt caching, usage logging, budget guard
(PLATFORM-SPEC.md §4.9).

- `LLM.create(...)` = `client.messages.create` with `cache_control` on the
  static system blocks and the last tool, usage written to `llm_usage` with
  a cost estimate from the price table in `settings` (seeded with
  placeholders the owner fills in from Anthropic's pricing page — nothing is
  hardcoded in code paths).
- Budget: at 95 % of `LLM_MONTHLY_BUDGET_USD` every call raises
  `BudgetExhausted` (agent runs move to `budget_exhausted`, chat answers
  without tools); research calls stop at `LLM_DAILY_RESEARCH_BUDGET_USD`.
- `FakeAnthropic` scripts responses for tests: no tokens spent, prompts recorded.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import database
from models import LlmUsage, Setting, new_id, utc_now

PRICES_KEY = "llm.prices"
BUDGET_KEY = "llm.budget"
# Placeholders ($ per million tokens) — labelled as estimates in the UI; the
# owner edits them on the Research page. Kept in settings, not code.
DEFAULT_PRICES = {
    "claude-sonnet-5": {"in": 3.0, "out": 15.0, "cacheRead": 0.3, "cacheWrite": 3.75, "placeholder": True},
    "claude-opus-5": {"in": 15.0, "out": 75.0, "cacheRead": 1.5, "cacheWrite": 18.75, "placeholder": True},
    "claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0, "cacheRead": 0.1, "cacheWrite": 1.25, "placeholder": True},
}


class BudgetExhausted(Exception):
    pass


class LLMNotConfigured(Exception):
    pass


def models() -> dict:
    return {
        "reasoning": os.environ.get("ANTHROPIC_MODEL_REASONING") or os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5",
        "fast": os.environ.get("ANTHROPIC_MODEL_FAST") or "claude-haiku-4-5-20251001",
    }


def budget() -> dict:
    with database.session_scope() as db:
        row = db.get(Setting, BUDGET_KEY)
        stored = dict(row.value_json) if row and isinstance(row.value_json, dict) else {}
    return {
        "monthlyUsd": float(stored.get("monthlyUsd") or os.environ.get("LLM_MONTHLY_BUDGET_USD") or 100),
        "dailyResearchUsd": float(stored.get("dailyResearchUsd") or os.environ.get("LLM_DAILY_RESEARCH_BUDGET_USD") or 1.5),
        "hardCapFraction": float(stored.get("hardCapFraction") or 0.95),
    }


def prices() -> dict:
    with database.session_scope() as db:
        row = db.get(Setting, PRICES_KEY)
        if row is None or not row.value_json:
            db.add(Setting(key=PRICES_KEY, value_json=DEFAULT_PRICES))
            return dict(DEFAULT_PRICES)
        return dict(row.value_json)


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0, table: dict | None = None) -> float:
    table = table or prices()
    p = table.get(model) or next((v for k, v in table.items() if model.startswith(k.rsplit("-", 1)[0])), None) or {"in": 3.0, "out": 15.0, "cacheRead": 0.3, "cacheWrite": 3.75}
    return (tokens_in * p.get("in", 0) + tokens_out * p.get("out", 0) + cache_read * p.get("cacheRead", 0) + cache_write * p.get("cacheWrite", 0)) / 1e6


def spend(since: datetime, purpose_prefix: str | None = None) -> float:
    with database.session_scope() as db:
        q = db.query(LlmUsage).filter(LlmUsage.ts >= since.isoformat(timespec="seconds"))
        if purpose_prefix:
            q = q.filter(LlmUsage.purpose.like(f"{purpose_prefix}%"))
        return float(sum(r.cost_usd for r in q.all()))


def month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def usage_summary() -> dict:
    b = budget()
    m = spend(month_start())
    d = spend(day_start(), "research")
    with database.session_scope() as db:
        rows = db.query(LlmUsage).filter(LlmUsage.ts >= month_start().isoformat(timespec="seconds")).all()
        by_purpose: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        for r in rows:
            for key, bucket in ((r.purpose, by_purpose), (r.model, by_model)):
                b_ = bucket.setdefault(key, {"calls": 0, "tokensIn": 0, "tokensOut": 0, "cacheRead": 0, "costUsd": 0.0})
                b_["calls"] += 1
                b_["tokensIn"] += r.tokens_in
                b_["tokensOut"] += r.tokens_out
                b_["cacheRead"] += r.cache_read
                b_["costUsd"] += r.cost_usd
    return {
        "monthSpendUsd": round(m, 4), "monthlyBudgetUsd": b["monthlyUsd"], "monthFraction": round(m / b["monthlyUsd"], 4) if b["monthlyUsd"] else None,
        "hardCapFraction": b["hardCapFraction"], "capped": m >= b["hardCapFraction"] * b["monthlyUsd"],
        "researchDaySpendUsd": round(d, 4), "dailyResearchBudgetUsd": b["dailyResearchUsd"],
        "researchCapped": d >= b["dailyResearchUsd"],
        "byPurpose": by_purpose, "byModel": by_model, "prices": prices(), "models": models(),
        "estimate": True,
    }


def check_budget(purpose: str) -> None:
    b = budget()
    if spend(month_start()) >= b["hardCapFraction"] * b["monthlyUsd"]:
        raise BudgetExhausted(f"monthly LLM budget: {b['hardCapFraction'] * 100:.0f}% of ${b['monthlyUsd']:.0f} reached")
    if purpose.startswith("research") and spend(day_start(), "research") >= b["dailyResearchUsd"]:
        raise BudgetExhausted(f"daily research budget ${b['dailyResearchUsd']:.2f} reached")


def log_usage(model: str, purpose: str, usage, agent_run_id: str | None = None) -> float:
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cost = estimate_cost(model, tokens_in, tokens_out, cache_read, cache_write)
    with database.session_scope() as db:
        db.add(LlmUsage(id=new_id(), ts=utc_now(), model=model, purpose=purpose, tokens_in=tokens_in, tokens_out=tokens_out,
                        cache_read=cache_read, cache_write=cache_write, cost_usd=cost, agent_run_id=agent_run_id))
    return cost


def _with_cache(system, tools):
    """system -> content blocks with cache_control on the last static block;
    tools -> cache_control on the last tool definition."""
    if isinstance(system, str):
        sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    else:
        sys_blocks = [dict(b) for b in system]
        if sys_blocks:
            sys_blocks[-1] = {**sys_blocks[-1], "cache_control": {"type": "ephemeral"}}
    tool_defs = None
    if tools:
        tool_defs = [dict(t) for t in tools]
        tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}
    return sys_blocks, tool_defs


class LLM:
    """One place every model call goes through."""

    def __init__(self, client=None):
        self._client = client
        self.models = models()

    def client(self):
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise LLMNotConfigured("ANTHROPIC_API_KEY is not set")
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def model_for(self, tier: str) -> str:
        return self.models["fast" if tier == "fast" else "reasoning"]

    def create(self, *, purpose: str, system, messages: list, tools: list | None = None, tier: str = "reasoning",
               max_tokens: int = 1536, agent_run_id: str | None = None, cache: bool = True, **extra):
        check_budget(purpose)
        model = extra.pop("model", None) or self.model_for(tier)
        sys_blocks, tool_defs = _with_cache(system, tools) if cache else (system, tools)
        kwargs = {"model": model, "max_tokens": max_tokens, "system": sys_blocks, "messages": messages, **extra}
        if tool_defs:
            kwargs["tools"] = tool_defs
        response = self.client().messages.create(**kwargs)
        if getattr(response, "usage", None) is not None:
            log_usage(model, purpose, response.usage, agent_run_id)
        return response

    def stream(self, *, purpose: str, system, messages: list, tools: list | None = None, tier: str = "reasoning",
               max_tokens: int = 1536, agent_run_id: str | None = None, **extra):
        """Context manager like `client.messages.stream`; usage is logged by `finish()`."""
        check_budget(purpose)
        model = extra.pop("model", None) or self.model_for(tier)
        sys_blocks, tool_defs = _with_cache(system, tools)
        kwargs = {"model": model, "max_tokens": max_tokens, "system": sys_blocks, "messages": messages, **extra}
        if tool_defs:
            kwargs["tools"] = tool_defs
        llm = self

        class _Stream:
            def __enter__(self_):
                self_.inner = llm.client().messages.stream(**kwargs).__enter__()
                return self_

            def __exit__(self_, *a):
                return self_.inner.__exit__(*a)

            @property
            def text_stream(self_):
                return self_.inner.text_stream

            def get_final_message(self_):
                msg = self_.inner.get_final_message()
                if getattr(msg, "usage", None) is not None:
                    log_usage(model, purpose, msg.usage, agent_run_id)
                return msg

        return _Stream()


# ----------------------------------------------------------------------------
# Scripted fake for tests
# ----------------------------------------------------------------------------

@dataclass
class FakeAnthropic:
    """`messages.create` returns the next scripted response; every request is recorded.

    A scripted response is a list of blocks: `("text", "...")` or
    `("tool_use", name, input_dict)`. Usage is fixed per call so budget tests
    are deterministic."""

    script: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    tokens_in: int = 1000
    tokens_out: int = 200
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
            if not self.script:
                blocks = [("text", "(script exhausted)")]
            else:
                blocks = self.script.pop(0)
        content = []
        for i, b in enumerate(blocks):
            if b[0] == "text":
                content.append(SimpleNamespace(type="text", text=b[1], model_dump=lambda t=b[1]: {"type": "text", "text": t}))
            else:
                tid = f"toolu_{len(self.calls)}_{i}"
                content.append(SimpleNamespace(type="tool_use", id=tid, name=b[1], input=dict(b[2]),
                                               model_dump=lambda t=tid, n=b[1], inp=dict(b[2]): {"type": "tool_use", "id": t, "name": n, "input": inp}))
        stop = "tool_use" if any(b[0] == "tool_use" for b in blocks) else "end_turn"
        usage = SimpleNamespace(input_tokens=self.tokens_in, output_tokens=self.tokens_out, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=content, usage=usage, stop_reason=stop, model=kwargs.get("model"))


def block_to_dict(b: Any) -> dict:
    if isinstance(b, dict):
        return b
    if hasattr(b, "model_dump"):
        d = b.model_dump()
        return {k: v for k, v in d.items() if k in ("type", "text", "id", "name", "input")} if d.get("type") in ("text", "tool_use") else d
    return {"type": getattr(b, "type", "text"), "text": getattr(b, "text", "")}
