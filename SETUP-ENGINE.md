# Backtest engine — NautilusTrader

Strategies are backtested with [NautilusTrader](https://nautilustrader.io), an
open-source Python/Rust trading platform. It runs **entirely in-process** —
no account, login, or Docker required.

## Install

Already installed into the backend venv:

```
pip install nautilus_trader
```

A Python 3.13 / Windows wheel is published (v1.230.0 here). The backend
reports `installed` + `version` at `/api/engine/status`; the Strategies page
shows a green "Engine: NautilusTrader <version>" badge when it's ready, and
disables **Run backtest** if the import fails.

## How a backtest runs

`POST /api/backtests` with a `strategyId` creates a job under `backtests/<id>/`
and a background thread runs the worker in a **subprocess** (isolated from the
API process):

```
python nautilus_backtest.py <strategy.json> <trades.json>
```

The worker (`backend/nautilus_backtest.py`):

1. loads the symbol's bars from `mbo-data/` via `data_store`,
2. builds a `BacktestEngine` with a `SIM` venue (margin account, USD) and a
   simulated equity instrument,
3. wrangles the 1-minute OHLCV into Nautilus `Bar`s,
4. runs a single **config-driven `Strategy`** that interprets the strategy
   document's conditions at runtime (no code generation) — the same closed
   vocabulary the builder UI offers (breakouts, SMA cross, RSI, consecutive
   candles, price levels; stop as %/points/ATR; target as R/%/points; session
   window; sizing),
5. records each round-trip as `{entryTime, entryPrice, exitTime, exitPrice,
   stopPrice, targetPrice, direction, qty, pnl, reason}` and writes them to
   `trades.json`.

The backend reads that back into the job as `source: "nautilus"`, and the
chart renders the trades (and reveals them progressively during replay).

## Modeling notes

- Entries are market orders filled by the engine on the signal bar; stop/target
  exits are evaluated intrabar against each bar's high/low and closed with a
  market order — so an exit fills at the actual simulated price, which may
  differ slightly from the exact stop/target level.
- Backtest bars are given unbounded volume so orders always fill in full; this
  tool models **decision logic, not liquidity**. (The chart's displayed volume
  is separate, from the real data.)
- All timestamps are UTC; strategy session windows are UTC.

## Adding data

Drop more Databento `*.ohlcv-1m.csv` / `*.mbo.csv` files into `mbo-data/`.
Every layer — API, chart, replay, and the backtest worker — reads whatever is
present; new days and symbols work with no code changes.
