"""Hypothesis engine (PLATFORM-SPEC.md Phase 6.3/6.4).

After each snapshot: the fast model tags the setup, the reasoning model
maintains `hypothesis_json` (candidate rules with supporting / contradicting
trade ids and a confidence). The question policy is code, not prompt:

- first trade;
- a rule with ≥2 supports that has not been confirmed yet (once per rule);
- the newest trade contradicts a rule with ≥2 supports;
- a skipped setup: whenever the hypothesis updates, the strongest rule is
  compiled into a provisional spec and evaluated with `SpecRules` over the
  bars already replayed (no Nautilus); a bar where it fires with no user
  trade within ±3 primary bars becomes `skipped_setup(candidate)`.

Rate limit: at most one question per two trades unless a contradiction.
Answers to skipped-setup questions are labelled `valid_skip`, `missed` or
`rule_too_loose` (explicit label from the UI, or keywords in free text).
"""

from __future__ import annotations

import json
import re

from teaching import prompts, store
from teaching.snapshot import compact_for_prompt

NS = 1_000_000_000
QUESTION_GAP_TRADES = 2
SKIP_WINDOW_BARS = 3


def _json_from(text: str) -> dict:
    from agent.research import _json_from as parse

    try:
        return parse(text) or {}
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}


def _text_of(response) -> str:
    return "".join(getattr(b, "text", "") or "" for b in getattr(response, "content", []) if getattr(b, "type", "") == "text")


def label_answer(text: str, explicit: str | None = None) -> str:
    if explicit in ("valid_skip", "missed", "rule_too_loose"):
        return explicit
    t = (text or "").lower()
    if any(w in t for w in ("missed", "didn't see", "did not see", "oops", "should have")):
        return "missed"
    if any(w in t for w in ("too loose", "not a setup", "wouldn't", "would not", "never", "not valid", "bad rule")):
        return "rule_too_loose"
    return "valid_skip"


def provisional_spec(rule: dict, *, symbol: str = "ES1!", root: str = "ES", stop_ticks: int = 20, target_ticks: int = 40,
                     rth_start: str = "09:30", rth_end: str = "16:00") -> dict:
    direction = rule.get("direction") or "long"
    return {
        "schemaVersion": 2, "name": f"provisional {rule.get('id', 'rule')}", "origin": {"type": "teaching"},
        "instrument": {"root": root, "symbol": symbol}, "timeframes": {"primary": "1min", "context": []},
        "direction": direction if direction in ("long", "short", "both") else "long",
        "session": {"entryWindow": {"start": rth_start, "end": rth_end}, "flattenAt": rth_end},
        "entry": {"trigger": rule.get("expr"), "orderType": "market", "timeoutBars": 1},
        "filters": list(rule.get("filters") or []),
        "exit": {"stop": {"type": "ticks", "value": stop_ticks}, "target": {"type": "ticks", "value": target_ticks}},
        "sizing": {"type": "fixed_contracts", "value": 1, "maxContracts": 1},
        "constraints": {"maxTradesPerDay": 50, "cooldownBars": 0, "stopAfterConsecutiveLosses": 50},
        "execution": {"mode": "bars"},
    }


def fires(spec: dict, bars_1m: list[dict], *, tick_size: float = 0.25, rth_start: str = "09:30", rth_end: str = "16:00") -> list[tuple[int, str]]:
    """(bar open time, direction) for every primary bar on which the spec's
    trigger + filters fire. Bars-mode evaluation with the shared SpecRules."""
    from engine.rules import Bar
    from engine.spec import validate_spec
    from engine.spec_strategy import SpecRules

    if not spec.get("entry", {}).get("trigger"):
        return []
    if validate_spec(spec, rth_start=rth_start, rth_end=rth_end):
        return []
    rules = SpecRules(spec, tick_size=tick_size, rth_start=rth_start, rth_end=rth_end)
    out = []
    for i, b in enumerate(bars_1m):
        bar = Bar(b["open"], b["high"], b["low"], b["close"], b.get("volume", 0), b.get("delta", 0), b.get("buyVol", 0),
                  b.get("sellVol", 0), (b["time"] + 60) * NS, b["time"] * NS, i)
        rules.on_bar(bar)
        d = rules.signal(bar)
        if d:
            out.append((int(b["time"]), d))
    return out


def skipped_candidates(fire_list: list[tuple[int, str]], user_entry_times: list[int], *, primary_seconds: int = 60,
                       window_bars: int = SKIP_WINDOW_BARS) -> list[tuple[int, str]]:
    tol = window_bars * primary_seconds
    return [(t, d) for t, d in fire_list if not any(abs(t - u) <= tol for u in user_entry_times)]


class HypothesisEngine:
    def __init__(self, session_id: str, llm=None, *, symbol: str = "ES1!", root: str = "ES", tick_size: float = 0.25,
                 rth_start: str = "09:30", rth_end: str = "16:00", stop_ticks: int = 20, target_ticks: int = 40):
        self.session_id = session_id
        self._llm = llm
        self.symbol, self.root, self.tick = symbol, root, tick_size
        self.rth = (rth_start, rth_end)
        self.stop_ticks, self.target_ticks = stop_ticks, target_ticks
        self.hypothesis: dict = {"rules": [], "summary": ""}
        self.tags: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.answers: list[dict] = []
        self.marks: list[dict] = []
        self.confirmed: set[str] = set()
        self.asked_confirm: set[str] = set()
        self.asked_skips: set[int] = set()
        self.pending_skips: list[dict] = []     # candidates not yet asked about
        self.questions_asked = 0
        self.trades_at_last_question = -QUESTION_GAP_TRADES
        self.open_questions: dict[str, dict] = {}
        self.version = 0

    # -- llm ------------------------------------------------------------------
    def llm(self):
        if self._llm is None:
            from agent import runs

            self._llm = runs._llm()
        return self._llm

    def _ask_model(self, system: str, user: str, tier: str, purpose: str, max_tokens: int = 1200) -> dict:
        try:
            resp = self.llm().create(purpose=purpose, system=system, messages=[{"role": "user", "content": user}],
                                     tier=tier, max_tokens=max_tokens, cache=False)
        except Exception as e:  # noqa: BLE001 — budget / config errors degrade to no tags
            store.add_event(self.session_id, 0, "annotation", {"note": f"llm unavailable: {e}"})
            return {}
        return _json_from(_text_of(resp))

    # -- steps ----------------------------------------------------------------
    def tag(self, trade: dict, snapshot: dict) -> dict:
        tags = self._ask_model(prompts.TAG_SYSTEM, json.dumps({"trade": trade, "snapshot": compact_for_prompt(snapshot)}, default=str),
                               "fast", "teaching.tag", 600)
        self.tags[trade["id"]] = tags
        store.add_event(self.session_id, trade.get("entryTs") or 0, "setup_tags", {"tradeId": trade["id"], "tags": tags})
        return tags

    def update(self, ts: int) -> dict:
        payload = {
            "trades": [{**{k: t.get(k) for k in ("id", "direction", "entryTime", "entryPrice", "stopPrice", "targetPrice",
                                                 "exitPrice", "exitReason", "pnlUsd", "note", "confidence")},
                        "tags": self.tags.get(t["id"]), "features": (t.get("features") or {})} for t in self.trades],
            "answers": self.answers, "skippedMarks": self.marks, "previousHypothesis": self.hypothesis,
        }
        hyp = self._ask_model(prompts.HYPOTHESIS_SYSTEM, json.dumps(payload, default=str), "reasoning", "teaching.hypothesis", 2000)
        if hyp.get("rules") is not None:
            self.hypothesis = hyp
            self.version += 1
            store.add_event(self.session_id, ts, "hypothesis_update", {"version": self.version, **hyp})
        return self.hypothesis

    def _rule(self, rid: str | None) -> dict | None:
        return next((r for r in self.hypothesis.get("rules") or [] if r.get("id") == rid), None)

    def _strongest(self) -> dict | None:
        rules = [r for r in self.hypothesis.get("rules") or [] if r.get("expr")]
        if not rules:
            return None
        return max(rules, key=lambda r: (len(r.get("supports") or []), float(r.get("confidence") or 0)))

    def _gap_ok(self) -> bool:
        return len(self.trades) - self.trades_at_last_question >= QUESTION_GAP_TRADES

    def _emit_question(self, kind: str, text: str, ts: int, trade_id: str | None = None, payload: dict | None = None) -> dict:
        q = store.add_question(self.session_id, kind, text, trade_id=trade_id, replay_ts=ts)
        q["payload"] = payload or {}
        self.open_questions[q["id"]] = q
        self.questions_asked += 1
        self.trades_at_last_question = len(self.trades)
        return q

    def decide_question(self, trade: dict, ts: int) -> dict | None:
        hyp = self.hypothesis
        qs = hyp.get("questions") or {}
        if len(self.trades) == 1:
            return self._emit_question("first", prompts.FIRST_QUESTION, ts, trade["id"])
        cid = hyp.get("latestTradeContradicts")
        rule = self._rule(cid) if cid else None
        if rule is None:
            for r in hyp.get("rules") or []:
                if trade["id"] in (r.get("contradicts") or []) and len(r.get("supports") or []) >= 2:
                    rule = r
                    break
        if rule is not None and len(rule.get("supports") or []) >= 2:
            text = qs.get("contradiction") or prompts.CONTRADICTION_FALLBACK.format(rule=rule.get("text", rule.get("id")))
            return self._emit_question("contradiction", text, ts, trade["id"], {"ruleId": rule.get("id")})
        if not self._gap_ok():
            return None
        for r in sorted(hyp.get("rules") or [], key=lambda r: -len(r.get("supports") or [])):
            rid = r.get("id")
            if len(r.get("supports") or []) >= 2 and rid not in self.confirmed and rid not in self.asked_confirm:
                self.asked_confirm.add(rid)
                text = qs.get("confirm") or prompts.CONFIRM_FALLBACK.format(rule=r.get("text", rid))
                return self._emit_question("confirm", text, ts, trade["id"], {"ruleId": rid})
        return None

    def provisional_replay(self, bars_1m: list[dict], ts: int) -> list[dict]:
        """Skipped-setup candidates from the strongest rule; events + at most one question."""
        rule = self._strongest()
        if rule is None or not bars_1m:
            return []
        spec = provisional_spec(rule, symbol=self.symbol, root=self.root, stop_ticks=self.stop_ticks, target_ticks=self.target_ticks,
                                rth_start=self.rth[0], rth_end=self.rth[1])
        try:
            fl = fires(spec, bars_1m, tick_size=self.tick, rth_start=self.rth[0], rth_end=self.rth[1])
        except Exception as e:  # noqa: BLE001 — a bad provisional expr is not fatal
            store.add_event(self.session_id, ts, "annotation", {"note": f"provisional rule not evaluable: {e}"})
            return []
        entries = [int(t["entryTime"]) for t in self.trades]
        # every new candidate becomes an event; the question budget is applied
        # separately (maybe_skip_question asks about the newest one only)
        cands = [c for c in skipped_candidates(fl, entries) if c[0] not in self.asked_skips]
        out = []
        for t, d in cands:
            self.asked_skips.add(t)
            ev = store.add_event(self.session_id, t * NS, "skipped_setup", {"source": "candidate", "time": t, "direction": d,
                                                                             "ruleId": rule.get("id"), "ruleText": rule.get("text")})
            out.append(ev)
            self.pending_skips.append(ev)
        return out

    def maybe_skip_question(self, ts: int) -> dict | None:
        """Ask about the most recent unasked skipped candidate when the budget allows."""
        if not self.pending_skips or not self._gap_ok():
            return None
        from chart_time import format_et

        c = self.pending_skips[-1]["payload"]
        self.pending_skips = []
        text = prompts.SKIPPED_QUESTION.format(time=format_et(c["time"]), direction=c["direction"], rule=c.get("ruleText") or c.get("ruleId"))
        return self._emit_question("skipped_setup", text, ts, None, {"time": c["time"], "direction": c["direction"], "ruleId": c.get("ruleId")})

    # -- public entry points ------------------------------------------------------
    def on_trade(self, trade: dict, snapshot: dict, bars_1m: list[dict]) -> dict | None:
        t = dict(trade)
        t["features"] = {k: v for k, v in (snapshot.get("features") or {}).items() if v is not None}
        self.trades.append(t)
        self.tag(t, snapshot)
        ts = int(t.get("entryTs") or 0)
        self.update(ts)
        q = self.decide_question(t, ts)
        self.provisional_replay(bars_1m, ts)
        if q is None:
            q = self.maybe_skip_question(ts)
        return q

    def on_mark(self, ts: int, payload: dict) -> None:
        self.marks.append({"ts": ts, **(payload or {})})

    def on_answer(self, question_id: str, text: str, label: str | None = None) -> dict | None:
        q = store.answer_question(question_id, text)
        if q is None:
            return None
        meta = self.open_questions.pop(question_id, {}).get("payload", {})
        entry = {"kind": q["kind"], "question": q["question"], "answer": text, **meta}
        if q["kind"] == "confirm" and meta.get("ruleId") and re.search(r"\b(yes|yeah|right|correct|exactly|that's it)\b", text.lower()):
            self.confirmed.add(meta["ruleId"])
            entry["confirmed"] = True
        if q["kind"] == "skipped_setup":
            entry["label"] = label_answer(text, label)
            store.add_event(self.session_id, int(meta.get("time", 0)) * NS, "skipped_setup_label",
                            {"time": meta.get("time"), "label": entry["label"], "reason": text, "ruleId": meta.get("ruleId")})
        self.answers.append(entry)
        return entry
