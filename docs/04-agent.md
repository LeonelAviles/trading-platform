# Stratos Research agent

Stratos Research is deliberately small. It searches approved research, explains
that research in plain English, creates valid Strategy Spec v2 documents when
asked, and runs the platform's existing in-sample/walk-forward validation.

Observational market questions stay in chat, answered by typed analysis tools
over frozen in-sample data. `get_daily_direction_stats` answers whether the
previous RTH day's direction predicts continuation or reversal.
`get_level_event_stats` is a general event study: what happens after the
session's FIRST break of a prior-day high/low/close, the opening range (any
length; 60 minutes is the initial balance), or session VWAP — break frequency,
continuation versus reversal into the close, follow-through and pullback
distributions, optionally conditioned on the breakout bar's order flow
(aggressor share, volume, narrow range). A `measure_until` reversal boundary
(back to the level, the range midpoint, or its other side) truncates the
excursions and adds reversal rate, follow-through before reversal, and time
to reversal — for questions like "how far does it run before coming back
into the range?". `get_data_coverage` reports which
symbols, dates, and book-liquidity days exist, and `list_backtest_jobs` shows
whether queued validation jobs are waiting, running, or finished (OOS results
stay hidden). None of these create a strategy, simulate entries or exits,
invoke Nautilus, or open the review chart. Those actions require an explicit
strategy or backtest request.

It does **not** browse the web, run repository code, access a shell, execute
orders, reveal OOS results during research, or promise that a strategy will be
profitable.

## Mental model

```
Machine Learning for Trading (pinned source)
                 ↓
SQLite full-text library + provenance graph
                 ↓
OpenAI Responses API (Stratos Research)
                 ↓ typed tools only
Strategy Spec v2 → NautilusTrader validation
                 ↓
Review chart + durable sequential research loop
```

The local knowledge graph is intentionally understandable:

- `repository CONTAINS document`
- `document HAS_SECTION section`
- every section stores its source path, heading, commit SHA, and URL

Raw research is guidance. A result produced by NautilusTrader is platform
evidence. The system prompt requires the agent to keep those two apart.

## Setup

1. Add an OpenAI project API key to `backend/.env`:

   ```dotenv
   OPENAI_API_KEY=your-project-key
   OPENAI_MODEL=gpt-6-astra
   ```

2. Install/update dependencies and start the app:

   ```bash
   make venv
   make dev
   ```

3. Open **Research** in the left navigation.

Chat responses stream as newline-delimited JSON from
`POST /api/agent/chat/stream`. The UI shows connection, reasoning, and tool
activity immediately, then renders answer text as it arrives. The legacy
`POST /api/agent/chat` JSON endpoint remains available for compatibility.

Agent lifecycle messages are written to the backend log without prompt text or
tool arguments. Each tool round records the thread ID, response ID, tool names,
and elapsed time, which makes slow requests diagnosable without logging user
content or secrets.

When `run_validation` is called, the stream also emits a workflow handoff. The
browser opens the first validation chart, carries the same thread into the
chart-side Stratos panel, and polls the durable workflow record while Nautilus
runs. The four queued jobs are IS/WF evidence windows for one candidate.
Stratos waits for all of them before it may create one child strategy.

Each child must change exactly one executable spec path, record that path and
its result-based rationale in `lineage`, and then complete its own four-window
validation. Research stops after a passing verdict, five changes, or three
consecutive non-improvements. The final workflow record is always either a
validated champion or “I couldn't find an edge.” OOS remains unavailable to
the research loop.

## Refresh the first research source

The checked-out research copy is gitignored under
`data/knowledge/repos/ml4t`. The first setup command downloads only the
research chapters, docs, examples and tests, then indexes them:

```bash
make knowledge-ml4t-bootstrap
```

To rebuild the index without downloading:

```bash
make knowledge-ml4t
```

The ingester reads Markdown, reStructuredText, Python, YAML, text, and Jupyter
notebooks. Notebook outputs, datasets, assets, caches, and generated environment
files are excluded. Re-ingestion atomically replaces the previous revision.

## Agent tools

1. `search_knowledge` — local BM25 search with pinned citations.
2. `get_strategy_language` — the exact schema and primitive registry.
3. `list_strategies` / `get_strategy` — read saved work.
4. `save_strategy` — validate and save, only after an explicit request.
5. `run_validation` — queue IS and walk-forward jobs, only after an explicit request.
6. `get_validation` — read validation without exposing OOS.
7. `get_daily_direction_stats` — RTH continuation/reversal probabilities,
   reported separately from trade win rate.
8. `conclude_research` — record the validated champion or no-edge conclusion.

OpenAI's Responses API is used because it supports reasoning models, conversation
state, and strongly typed custom tools in one maintained API.
