"""Futures PnL and sizing — pure functions (PLATFORM-SPEC.md §2 bugs 1–2, §4.4 sizing).

ES: 0.25 tick = $12.50, $50/point. NQ: 0.25 tick = $5, $20/point. Every
dollar figure in the platform goes through here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    tick_size: float
    tick_value: float
    multiplier: float
    commission_per_side: float = 0.0

    @classmethod
    def from_root(cls, root) -> "ContractSpec":
        return cls(root.tick_size, root.tick_value, root.multiplier, root.commission_per_side)


def direction_sign(direction: str) -> int:
    if direction == "long":
        return 1
    if direction == "short":
        return -1
    raise ValueError(f"direction must be long|short, got {direction!r}")


def pnl_points(entry: float, exit: float, direction: str) -> float:
    return (exit - entry) * direction_sign(direction)


def points_to_ticks(points: float, spec: ContractSpec) -> float:
    return points / spec.tick_size


def round_to_tick(price: float, spec: ContractSpec) -> float:
    return round(round(price / spec.tick_size) * spec.tick_size, 10)


def gross_pnl_usd(entry: float, exit: float, direction: str, contracts: int, spec: ContractSpec) -> float:
    return pnl_points(entry, exit, direction) * spec.multiplier * contracts


def commission_usd(contracts: int, spec: ContractSpec, sides: int = 2) -> float:
    return spec.commission_per_side * contracts * sides


def net_pnl_usd(entry: float, exit: float, direction: str, contracts: int, spec: ContractSpec) -> float:
    return gross_pnl_usd(entry, exit, direction, contracts, spec) - commission_usd(contracts, spec)


def r_multiple(entry: float, exit: float, stop: float | None, direction: str) -> float | None:
    """PnL in units of initial risk (entry→stop distance); None without a stop."""
    if stop is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return pnl_points(entry, exit, direction) / risk


def contracts_fixed_risk(account_size: float, risk_pct: float, stop_ticks: float, spec: ContractSpec,
                         max_contracts: int) -> int:
    """floor(account × risk% / (stop ticks × tick value)), min 1, max `max_contracts`."""
    if stop_ticks <= 0:
        return 1
    n = math.floor(account_size * risk_pct / 100.0 / (stop_ticks * spec.tick_value))
    return max(1, min(int(n), int(max_contracts)))


def contracts_vol_scaled(account_size: float, risk_pct: float, atr_points: float, spec: ContractSpec,
                         max_contracts: int) -> int:
    return contracts_fixed_risk(account_size, risk_pct, points_to_ticks(atr_points, spec), spec, max_contracts)


def apply_slippage(price: float, direction: str, ticks: float, spec: ContractSpec, entering: bool) -> float:
    """Worse price by `ticks`: buys pay up, sells hit lower. `entering` picks
    which side of the trade the fill is on."""
    sign = direction_sign(direction) * (1 if entering else -1)
    return round_to_tick(price + sign * ticks * spec.tick_size, spec)
