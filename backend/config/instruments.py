"""Typed loader for config/instruments.yaml (PLATFORM-SPEC.md §4.2).

Every module that needs tick size, multiplier, commission, the RTH window or
a root's continuous symbol reads it from here — never hardcode ES numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "instruments.yaml"


@dataclass(frozen=True)
class RootSpec:
    root: str
    name: str
    tick_size: float
    tick_value: float
    multiplier: float
    currency: str
    continuous: str
    outright_regex: str
    commission_per_side: float
    initial_margin: float
    _pattern: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_pattern", re.compile(self.outright_regex))

    def is_outright(self, symbol: str) -> bool:
        return bool(self._pattern.match(symbol))

    def points_to_usd(self, points: float, contracts: int = 1) -> float:
        return points * self.multiplier * contracts

    def ticks_to_usd(self, ticks: float, contracts: int = 1) -> float:
        return ticks * self.tick_value * contracts

    def to_dict(self) -> dict:
        return {
            "root": self.root, "name": self.name, "tickSize": self.tick_size,
            "tickValue": self.tick_value, "multiplier": self.multiplier, "currency": self.currency,
            "continuous": self.continuous, "outrightRegex": self.outright_regex,
            "commissionPerSide": self.commission_per_side, "initialMargin": self.initial_margin,
        }


@dataclass(frozen=True)
class SessionSpec:
    timezone: str
    rth_start: str
    rth_end: str
    flatten_before_close_minutes: int

    def to_dict(self) -> dict:
        return {
            "timezone": self.timezone, "rth": {"start": self.rth_start, "end": self.rth_end},
            "flattenBeforeCloseMinutes": self.flatten_before_close_minutes,
        }


@dataclass(frozen=True)
class CostSpec:
    slippage_ticks_market: int
    slippage_ticks_stop: int
    limit_fill_rule: str

    def to_dict(self) -> dict:
        return {
            "slippageTicksMarket": self.slippage_ticks_market,
            "slippageTicksStop": self.slippage_ticks_stop,
            "limitFillRule": self.limit_fill_rule,
        }


@dataclass(frozen=True)
class Instruments:
    roots: dict[str, RootSpec]
    session: SessionSpec
    costs: CostSpec
    defaults: dict

    # -- symbol resolution ---------------------------------------------------

    def root_for_symbol(self, symbol: str) -> RootSpec | None:
        """`ES1!` -> ES, `ESM6` -> ES, `MESM6` -> MES, `ESM6-ESU6` -> None."""
        for spec in self.roots.values():
            if symbol == spec.continuous:
                return spec
        for spec in self.roots.values():
            if spec.is_outright(symbol):
                return spec
        return None

    def is_continuous(self, symbol: str) -> bool:
        return any(symbol == s.continuous for s in self.roots.values())

    def is_outright(self, symbol: str) -> bool:
        return any(s.is_outright(symbol) for s in self.roots.values())

    def continuous_symbols(self) -> list[str]:
        return [s.continuous for s in self.roots.values()]

    def to_dict(self) -> dict:
        return {
            "roots": {k: v.to_dict() for k, v in self.roots.items()},
            "session": self.session.to_dict(),
            "costs": self.costs.to_dict(),
            "defaults": dict(self.defaults),
        }


def _parse(raw: dict) -> Instruments:
    roots = {
        name: RootSpec(
            root=name, name=str(r["name"]), tick_size=float(r["tick_size"]),
            tick_value=float(r["tick_value"]), multiplier=float(r["multiplier"]),
            currency=str(r.get("currency", "USD")), continuous=str(r["continuous"]),
            outright_regex=str(r["outright_regex"]),
            commission_per_side=float(r.get("commission_per_side", 0.0)),
            initial_margin=float(r.get("initial_margin", 0.0)),
        )
        for name, r in raw["roots"].items()
    }
    s = raw.get("session", {})
    session = SessionSpec(
        timezone=str(s.get("timezone", "America/New_York")),
        rth_start=str(s.get("rth", {}).get("start", "09:30")),
        rth_end=str(s.get("rth", {}).get("end", "16:00")),
        flatten_before_close_minutes=int(s.get("flatten_before_close_minutes", 2)),
    )
    c = raw.get("costs", {})
    costs = CostSpec(
        slippage_ticks_market=int(c.get("slippage_ticks_market", 1)),
        slippage_ticks_stop=int(c.get("slippage_ticks_stop", 1)),
        limit_fill_rule=str(c.get("limit_fill_rule", "trade_through")),
    )
    return Instruments(roots=roots, session=session, costs=costs, defaults=dict(raw.get("defaults", {})))


@lru_cache(maxsize=4)
def load_instruments(path: Path | str | None = None) -> Instruments:
    p = Path(path) if path else CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        return _parse(yaml.safe_load(f))


def get_root(root: str) -> RootSpec:
    spec = load_instruments().roots.get(root)
    if spec is None:
        raise KeyError(f"unknown root '{root}'")
    return spec
