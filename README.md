# trading-platform

TradingView-style charting and strategy workbench over Databento market data,
with NautilusTrader as the backtest engine.

## Features

- **Candlestick chart** (TradingView's lightweight-charts) with volume,
  intervals 1m–1D, per-symbol drawing tools (trend line, horizontal line,
  rectangle, long/short position with SL/TP), full style editing, and a
  TradingView-style settings dialog.
- **Bar replay** — pick any bar and replay the session forward with
  play/pause/step/speed controls, marking up the chart as you go.
- **Strategy builder** — compose deterministic entry rules (breakouts, SMA
  crosses, RSI, consecutive candles, price levels, session window), stop
  (%, points, ATR) and target (R multiple, %, points), and sizing.
- **Backtests** — each strategy runs through the open-source
  [NautilusTrader](https://nautilustrader.io) engine, in-process, with no
  account or Docker (see [SETUP-ENGINE.md](SETUP-ENGINE.md)). Results render on
  the chart as trade shapes; during replay they reveal progressively as the
  clock passes each entry, so you can compare the engine's trades against your
  own calls (matched/missed count shown live).

## Running

```bash
# backend (port 8123)
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8123 --reload

# frontend (port 5173, proxies /api to the backend)
cd frontend
npm install
npm run dev
```

## Data

Drop Databento batch-download files into `mbo-data/`:
`*.ohlcv-1m.csv` (1-minute bars, used for 1m+ intervals) and `*.mbo.csv`
(raw MBO events, used for sub-minute resampling). Every layer — API, chart,
replay, backtest engine — picks up whatever files are present; multiple
days and symbols just work.
