"""Strategy Spec v2 — the shared strategy schema (PLATFORM-SPEC.md §4.4).

Pydantic models validate shape and types; `validate_spec()` adds the checks
that need the registry and the session config (unknown primitive, wrong
param, `tf` not in timeframes, entry window outside RTH, target `level`
without a level, …) and returns human-readable errors. `json_schema()` is
exported to `frontend/src/spec/schema.json` (scripts/export_spec_schema.py)
and served by `get_spec_schema` together with the primitive docs.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engine import expr as expr_mod
from engine.session import parse_hhmm, window_inside_rth
from engine.verdict import DEFAULT_PASS_CRITERIA

SCHEMA_VERSION = 2
TIMEFRAMES = ("1min", "5min", "15min", "30min", "1h", "4h", "1D")
STATUSES = ("draft", "testing", "candidate", "forward_test", "live", "rejected", "retired")
STRUCTURES = ("swing_low", "swing_high", "or_low", "or_high", "session_low", "session_high", "bar_low", "bar_high")
LEVELS = ("session_high", "session_low", "vah", "val", "poc", "prior_day_high", "prior_day_low", "or_high", "or_low", "vwap")

Expr = Any   # validated structurally by expr.check()


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Origin(_M):
    type: Literal["prompt", "teaching", "manual"] = "manual"
    sourceId: str | None = None


class Lineage(_M):
    parentId: str | None = None
    changedVariable: str | None = None
    rationale: str | None = None
    trialIndex: int = 0


class Instrument(_M):
    root: str = "ES"
    symbol: str = "ES1!"


class Timeframes(_M):
    primary: Literal[TIMEFRAMES] = "1min"
    context: list[Literal[TIMEFRAMES]] = Field(default_factory=list)


class Window(_M):
    start: str
    end: str


class Session(_M):
    entryWindow: Window = Field(default_factory=lambda: Window(start="09:30", end="15:30"))
    noTradeWindows: list[Window] = Field(default_factory=list)
    flattenAt: str = "15:58"


class SequenceStep(_M):
    when: Expr
    withinBars: int = 20


class Entry(_M):
    trigger: Expr
    sequence: list[SequenceStep] = Field(default_factory=list)
    orderType: Literal["market", "limit", "stop"] = "market"
    limitOffsetTicks: int = 0
    stopOffsetTicks: int = 1
    timeoutBars: int = 3


class Stop(_M):
    type: Literal["atr", "ticks", "points", "percent", "structure"] = "ticks"
    value: float | None = 20
    period: int = 14
    structure: Literal[STRUCTURES] | None = None
    bufferTicks: int = 2


class Target(_M):
    type: Literal["rr", "ticks", "points", "level"] = "rr"
    value: float | None = 2.0
    level: Literal[LEVELS] | None = None


class Trailing(_M):
    type: Literal["atr", "ticks"] = "ticks"
    value: float = 8
    period: int = 14
    activateAtR: float = 1.0


class Breakeven(_M):
    atR: float = 1.0
    offsetTicks: int = 1


class TimeStop(_M):
    bars: int


class ScaleOut(_M):
    atR: float
    fraction: float


class Exit(_M):
    stop: Stop = Field(default_factory=Stop)
    target: Target = Field(default_factory=Target)
    trailing: Trailing | None = None
    breakeven: Breakeven | None = None
    timeStop: TimeStop | None = None
    scaleOut: list[ScaleOut] = Field(default_factory=list)


class Sizing(_M):
    type: Literal["fixed_risk", "fixed_contracts", "vol_scaled"] = "fixed_risk"
    value: float = 0.5
    maxContracts: int = 5
    period: int = 14


class Constraints(_M):
    maxTradesPerDay: int = 3
    cooldownBars: int = 5
    stopAfterConsecutiveLosses: int = 2
    maxConcurrentPositions: int = 1


class Execution(_M):
    mode: Literal["bars", "ticks", "l3"] = "ticks"
    slippageTicksOverride: int | None = None


class PassCriteria(_M):
    minTradesInSample: int = DEFAULT_PASS_CRITERIA["minTradesInSample"]
    minTradesOutOfSample: int = DEFAULT_PASS_CRITERIA["minTradesOutOfSample"]
    minProfitFactor: float = DEFAULT_PASS_CRITERIA["minProfitFactor"]
    minExpectancyR: float = DEFAULT_PASS_CRITERIA["minExpectancyR"]
    maxDrawdownPct: float = DEFAULT_PASS_CRITERIA["maxDrawdownPct"]
    minWalkForwardWindowsPositive: int = DEFAULT_PASS_CRITERIA["minWalkForwardWindowsPositive"]
    minOosProfitFactor: float = DEFAULT_PASS_CRITERIA["minOosProfitFactor"]
    maxMonteCarloDrawdown95Pct: float = DEFAULT_PASS_CRITERIA["maxMonteCarloDrawdown95Pct"]
    minDeflatedSharpeProb: float | None = None


class RiskProfile(_M):
    proposedBy: Literal["agent", "user", "default"] = "default"
    rationale: str | None = None
    accountSize: float = 100000
    riskPerTradePct: float = 0.5
    maxContracts: int = 5
    dailyLossLimitPct: float = 2.0
    weeklyLossLimitPct: float = 5.0
    maxTradesPerDay: int = 5
    stopAfterConsecutiveLosses: int = 3
    weeklyTargetPct: float | None = None
    passCriteria: PassCriteria = Field(default_factory=PassCriteria)


class StrategySpec(_M):
    schemaVersion: Literal[2] = 2
    id: str | None = None
    name: str
    description: str | None = None
    origin: Origin = Field(default_factory=Origin)
    lineage: Lineage = Field(default_factory=Lineage)
    status: Literal[STATUSES] = "draft"
    instrument: Instrument = Field(default_factory=Instrument)
    timeframes: Timeframes = Field(default_factory=Timeframes)
    direction: Literal["long", "short", "both"] = "long"
    session: Session = Field(default_factory=Session)
    entry: Entry
    filters: list[Expr] = Field(default_factory=list)
    exit: Exit = Field(default_factory=Exit)
    sizing: Sizing = Field(default_factory=Sizing)
    constraints: Constraints = Field(default_factory=Constraints)
    execution: Execution = Field(default_factory=Execution)
    risk: RiskProfile = Field(default_factory=RiskProfile)
    # free-form, kept for the UI/agent (v1 `rationale`, `basedOn`, notes)
    meta: dict[str, Any] = Field(default_factory=dict)


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------

def _fmt_pydantic(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "spec"
        msg = err["msg"]
        if err["type"] == "extra_forbidden":
            msg = "unknown field"
        out.append(f"{loc}: {msg}")
    return out


def _hhmm_ok(s: str) -> bool:
    try:
        parse_hhmm(s)
        return True
    except Exception:
        return False


def validate_spec(spec: dict, *, rth_start: str = "09:30", rth_end: str = "16:00") -> list[str]:
    """Human-readable problems; empty list = valid."""
    try:
        m = StrategySpec.model_validate(spec)
    except ValidationError as e:
        return _fmt_pydantic(e)
    errs: list[str] = []
    tfs = [m.timeframes.primary] + [t for t in m.timeframes.context if t != m.timeframes.primary]
    from engine.features import TF_MINUTES

    for t in m.timeframes.context:
        if TF_MINUTES[t] <= TF_MINUTES[m.timeframes.primary] or TF_MINUTES[t] % TF_MINUTES[m.timeframes.primary]:
            errs.append(f"timeframes.context: '{t}' must be a coarser multiple of the primary '{m.timeframes.primary}'")

    from config.instruments import load_instruments

    ins = load_instruments()
    root = ins.root_for_symbol(m.instrument.symbol)
    if root is None:
        errs.append(f"instrument.symbol: unknown symbol '{m.instrument.symbol}'")
    elif root.root != m.instrument.root:
        errs.append(f"instrument.root: '{m.instrument.root}' does not match symbol '{m.instrument.symbol}' (root {root.root})")

    for label, w in [("session.entryWindow", m.session.entryWindow)] + [(f"session.noTradeWindows[{i}]", w) for i, w in enumerate(m.session.noTradeWindows)]:
        if not (_hhmm_ok(w.start) and _hhmm_ok(w.end)):
            errs.append(f"{label}: times must be HH:MM (New York)")
        elif not window_inside_rth(w.start, w.end, rth_start, rth_end):
            errs.append(f"{label}: {w.start}–{w.end} must sit inside RTH {rth_start}–{rth_end} with start < end")
    if not _hhmm_ok(m.session.flattenAt):
        errs.append("session.flattenAt: must be HH:MM")
    elif not (parse_hhmm(rth_start) < parse_hhmm(m.session.flattenAt) <= parse_hhmm(rth_end)):
        errs.append(f"session.flattenAt: {m.session.flattenAt} must be inside RTH and after the open")
    elif _hhmm_ok(m.session.entryWindow.end) and parse_hhmm(m.session.entryWindow.end) > parse_hhmm(m.session.flattenAt):
        errs.append("session.entryWindow.end: must not be after flattenAt")

    errs += expr_mod.check(m.entry.trigger, tfs, "entry.trigger")
    for i, s in enumerate(m.entry.sequence):
        errs += expr_mod.check(s.when, tfs, f"entry.sequence[{i}].when")
        if s.withinBars < 1:
            errs.append(f"entry.sequence[{i}].withinBars: must be ≥ 1")
    for i, f in enumerate(m.filters):
        errs += expr_mod.check(f, tfs, f"filters[{i}]")
    for label, e in (("entry.trigger", m.entry.trigger), *((f"filters[{i}]", f) for i, f in enumerate(m.filters))):
        if expr_mod.is_leaf(e) and not isinstance(e, bool):
            errs.append(f"{label}: must be a boolean expression, not a bare value")

    st = m.exit.stop
    if st.type == "structure" and not st.structure:
        errs.append("exit.stop.structure: required when stop.type is 'structure'")
    if st.type != "structure" and (st.value is None or st.value <= 0):
        errs.append("exit.stop.value: must be > 0")
    tg = m.exit.target
    if tg.type == "level" and not tg.level:
        errs.append("exit.target.level: required when target.type is 'level'")
    if tg.type != "level" and (tg.value is None or tg.value <= 0):
        errs.append("exit.target.value: must be > 0")
    if m.exit.timeStop is not None and m.exit.timeStop.bars < 1:
        errs.append("exit.timeStop.bars: must be ≥ 1")
    for i, so in enumerate(m.exit.scaleOut):
        if not (0 < so.fraction < 1):
            errs.append(f"exit.scaleOut[{i}].fraction: must be between 0 and 1")
    if m.sizing.value <= 0 or m.sizing.maxContracts < 1:
        errs.append("sizing: value must be > 0 and maxContracts ≥ 1")
    if m.entry.timeoutBars < 1:
        errs.append("entry.timeoutBars: must be ≥ 1")
    return errs


def normalize(spec: dict) -> dict:
    """Fill defaults and return a plain dict (raises on shape errors)."""
    m = StrategySpec.model_validate(spec)
    d = m.model_dump(mode="json")
    if not d.get("id"):
        d["id"] = uuid.uuid4().hex[:12]
    return d


def required_mode(spec: dict) -> str:
    """Cheapest execution mode that can evaluate the rules: `ticks` when any
    trade- or book-updated primitive is referenced or the entry is not a
    market order, else `bars`."""
    from engine.primitives.base import get_class

    exprs = [spec.get("entry", {}).get("trigger")] + [s.get("when") for s in spec.get("entry", {}).get("sequence", [])] + list(spec.get("filters", []))
    for e in exprs:
        if e is None:
            continue
        for name, _, _ in expr_mod.referenced_primitives(e):
            try:
                if get_class(name).update_on in ("trade", "book"):
                    return "ticks"
            except KeyError:
                continue
    if spec.get("entry", {}).get("orderType", "market") != "market":
        return "ticks"
    return "bars"


def json_schema() -> dict:
    schema = StrategySpec.model_json_schema()
    schema["title"] = "StrategySpec v2"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["x-operators"] = sorted(expr_mod.OPS)
    schema["x-fields"] = list(expr_mod.FIELDS)
    schema["x-timeframes"] = list(TIMEFRAMES)
    return schema


def primitive_docs() -> list[dict]:
    from engine.primitives.base import describe_all

    return describe_all()
