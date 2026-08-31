"""Instrument resolution for the engine (PLATFORM-SPEC.md §5 Phase 2 task 1).

- `contract(symbol, as_of)`: the NautilusTrader `FuturesContract` for an
  outright, from instruments.yaml (delegates to market.catalog).
- `resolve_ranges(symbol, date_from, date_to)`: a continuous symbol becomes
  the list of (outright, first_date, last_date) runs it maps to over the
  window, from front_month.parquet; an outright is a single run. Backtests
  never hold across a roll boundary (forced flat at session end anyway).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from config.instruments import RootSpec, load_instruments
from market import catalog as cat
from market.paths import get_paths


@dataclass(frozen=True)
class ContractRange:
    symbol: str
    date_from: date
    date_to: date
    dates: tuple[date, ...]


def root_spec(symbol: str) -> RootSpec:
    spec = load_instruments().root_for_symbol(symbol)
    if spec is None:
        raise ValueError(f"unknown symbol '{symbol}'")
    return spec


def contract(symbol: str, as_of: date):
    return cat.contract_for(symbol, root_spec(symbol), as_of)


def session_dates(root: str, date_from: date, date_to: date) -> list[tuple[date, str]]:
    """(date, front symbol) for every ingested session of `root` in the window."""
    p = get_paths().front_month
    if not p.exists():
        return []
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT date, symbol FROM read_parquet(?) WHERE root = ? AND date >= ? AND date <= ? ORDER BY date",
            [str(p), root, date_from, date_to],
        ).fetchall()
    finally:
        con.close()
    return [(r[0] if isinstance(r[0], date) else r[0].date(), str(r[1])) for r in rows]


def resolve_ranges(symbol: str, date_from: date, date_to: date) -> list[ContractRange]:
    spec = root_spec(symbol)
    ins = load_instruments()
    if ins.is_continuous(symbol):
        pairs = session_dates(spec.root, date_from, date_to)
    else:
        pairs = [(d, symbol) for d, s in session_dates(spec.root, date_from, date_to) if _traded(spec.root, d, symbol)]
    runs: list[list] = []
    for d, sym in pairs:
        if runs and runs[-1][0] == sym:
            runs[-1][2].append(d)
        else:
            runs.append([sym, d, [d]])
    return [ContractRange(sym, ds[0], ds[-1], tuple(ds)) for sym, _, ds in runs]


def _traded(root: str, d: date, symbol: str) -> bool:
    part = get_paths().partition(get_paths().bars_1m_dir, root, d.isoformat()) / "part.parquet"
    if not part.exists():
        return False
    con = duckdb.connect()
    try:
        return con.execute("SELECT count(*) FROM read_parquet(?) WHERE symbol = ?", [str(part), symbol]).fetchone()[0] > 0
    finally:
        con.close()
