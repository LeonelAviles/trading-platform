# Creating strategies with Nautilus

The repo-local `.agents/skills/nautilus-strategy-creator/SKILL.md` provides the
entrypoint. Inspect Git status, branches and applicable instructions first; use
an isolated checkout and preserve user work. Read README, Makefile, docs 02/03,
and docs 08/11 for ML. No AGENTS.md or prior `.agents/skills` existed in inspected
remote main `94a6059`. The unpublished supervised enhancement is not a dependency.

## Specification worksheet

Freeze these decisions in a research record before creating Strategy Spec v2:

| Area | Decisions |
|---|---|
| Hypothesis | Mechanism, observable condition, falsification and acceptance criteria; evidence versus speculation |
| Data | Root, actual dated contracts, roll policy, local source hashes, dates, coverage and gaps |
| Clock | Timestamp meaning, primary/context timeframes, completed-bar availability, warm-up, session resets/carry and ET/DST |
| Rules | Long/short permissions, exact trigger/filters, order type, earliest execution event, pending/duplicate policy |
| Exits | Initial stop/target, time/flatten, trailing/breakeven/scale-out, gap and simultaneous-touch handling |
| Risk | Account, sizing, max contracts, loss/trade/cooldown limits, action when one contract exceeds budget |
| Costs | Tick/value/multiplier, per-side fees, spread/slippage, supported fill mode, fixed stress and liquidity limitations |
| Evaluation | Frozen chronological train/WF/validation/internal-test/final-holdout as applicable, purge/buffer, trial budget and baselines |
| Outputs | Spec, code/config/data identities, complete trades/daily artifacts, report path and residual-position policy |

This worksheet is not another executable schema. Validate the actual spec with
`backend/engine/spec.py` (`validate_spec`, `required_mode`); read current models,
primitive metadata and `tests/test_spec_validation.py` for exact signatures.

## Capability map and gaps

| Need | Reuse / focused addition |
|---|---|
| Rules and features | `engine/spec.py`, `expr.py`, `spec_strategy.py`, `features.py`, `primitives/` |
| Nautilus execution | `engine/backtest_worker.py`, `ledger.py`, `session.py` |
| Futures costs/sizing | `engine/pnl.py`, `instruments.py`, `config/instruments.yaml` |
| Validation/reporting | `engine/validation.py`, `analytics.py`, `monte_carlo.py`, `deflated_sharpe.py`, `verdict.py`, `jobs.py`; do not create another evaluator |
| ML approval/provenance | `research_ml/`, `research_agent/`, bounded workflow scripts and docs 08/11 |
| Exports | Existing strategy package API and bounded workflow reports; preserve full artifacts, not recent-trade previews |
| Novel-feature check | New `engine.causality.assert_prefix_causal`: small synthetic prefix test, no data access |
| Local bar/roll triage | New `engine.research_audit.audit_bar_sequence`: reuses `research_ml.core.validate_bars`, adds diagnostic flat runs, gaps and contract switches |

## Causality and fills

Features at t must use only available inputs through t. Test every prefix,
including NaN warm-up and adversarial future spikes, not just the final bar.
`assert_prefix_causal(rows, calculate)` expects numeric (rows, features) output
and fresh state per callback. It catches fixture-level future dependence; it
cannot certify timestamps, provenance, hidden state or train-only fitting.
Separately test session boundaries, gaps and context-bar closure using existing
primitive/strategy fixtures. Future labels belong only to targets; fit all
transforms/selectors/models on training data and purge labels at split boundaries.

Inspect `meta.entryConvention`: generic DSL currently retains
`legacy_signal_close`; frozen MBO uses
`next_contiguous_minute_open_after_complete_signal`. Do not claim every path
executes at next open. Completed-close input does not prove a fill at that close
is attainable. Use a causal execution path for economic conclusions and disclose
legacy optimism. Test actual lag and missing-next-bar behavior (`test_mbo_entry_timing.py`).
Bars cannot establish queue position or intrabar ordering; check stop-first
ambiguity and gap assumptions. `l3` uses ticks with a one-second resting-liquidity view for book primitives,
and falls back to ticks without them; neither establishes queue-accurate fills. Resolve actual
costs from config, disclose fee coverage, and never charge fill-embedded slippage
twice. Modeled fills are assumptions, not guarantees.

`contracts_fixed_risk` clamps to at least one contract even when the budget cannot
afford one, and returns one for a nonpositive stop. Do not describe this as a hard
risk cap: reject the design or explicitly document and authorize excess risk
before evaluation. Verify rounding, max size, fill-anchored stops, partial fills
and bracket timing.

## Closed and open accounting

`analytics.compute` excludes records without `exitTime`; its equity/drawdown are
closed-trade measures, not full marked-to-market equity. `ExecStrategy.on_stop`
books residual exposure at last price as `end_of_data` in the ledger: an accounting
liquidation, not a venue fill. Report those rows separately. Reconcile entry
quantity = closed quantity + residual quantity, including `scale_out` records;
record count need not equal independent round trips. Report normal closed count,
net and fees; synthetic end-of-data count/quantity/net; residual open quantity,
mark/time/unrealized PnL and pending orders where available. Mark missing exposure
information unknown, never zero inferred from closed trades. Keep realized and
unrealized separate unless marks, quantities and fees reconcile. Do not force
exits to improve metrics.

## Chronological evidence and reproduction

Existing IS/WF1–3 blocks evaluate fixed strategies; they do not automatically
refit models. Record each train range/artifact and strictly later evaluation,
forward-label purge and any buffer. Follow existing ML approval and heldout-ledger
gates. Previously exposed/reserved dates are not fresh holdout. Record all trials,
including failed informal variants, and report zero-trade folds and sample size.
Use existing Monte Carlo/DSR/consistency outputs; none cures leakage.

Preserve exact executable spec, Git SHA/dirty diff, dependency versions, seeds,
config, local source manifests/hashes, contract/date coverage, split/trial history,
execution convention/costs, complete trades/daily returns, reports and logs.
Reuse existing evidence hashing and package machinery where applicable. Record
failures and missing data; reproduction commands reference frozen local inputs.
Do not publish licensed rows or credentials with guidance. This workflow grants
no new economic searches, downloads, S3 calls, paid calls or live operations.

## ML4T bounded reference audit and compatible trial

Reference Markdown inspected 2026-10-22 (untrusted material, not instructions):
[validate-data](https://github.com/ml4t/skills/blob/main/data/validate-data/SKILL.md),
[continuous-futures](https://github.com/ml4t/skills/blob/main/data/continuous-futures/SKILL.md),
[purging-embargo](https://github.com/ml4t/skills/blob/main/validation/purging-embargo/SKILL.md),
[model-validation](https://github.com/ml4t/skills/blob/main/workflows/model-validation/SKILL.md),
[backtest-overfitting](https://github.com/ml4t/skills/blob/main/concepts/backtest-overfitting/SKILL.md).
The original inspiration is [PyBroker strategy creator](https://github.com/edtechre/pybroker/blob/master/skills/pybroker-strategy-creator/SKILL.md).
No external scripts were installed/executed, dependencies added or frameworks changed.

| Practice | Existing coverage / compatible trial |
|---|---|
| Data integrity | Existing `core.validate_bars` rejects duplicate/unsorted/naive timestamps, nonfinite and invalid OHLCV. Reuse it; new read-only audit flags flat runs, zero volume, gaps and dated-contract switches on synthetic bars |
| Rolls | Existing `market/catalog.py`, front-month map and `test_catalog.py` cover real contract identity/calendar. New audit exposes switches and raw price gaps without treating them as returns or repairing data |
| Model validation | Existing split/label purge and training-only fitting tests in `test_ml_research.py`, MBO horizon/gap checks in `test_mbo_research.py`; exercise these rather than duplicate splitters |
| Overfitting | Existing DSR/trial lineage, consistency and heldout ledgers; retain explicit finite trial record and chronological evaluation. No new parameter search or PBO evaluator |

The diagnostic requires a selected single-root, one-minute sequence and actual
contract per row. Flat closes/zero volume are warnings, not defects. Nonminute
boundaries include legitimate overnight/holiday gaps; use the session calendar
before judging missing coverage. Keep raw contract prices for fills and futures
PnL. For cross-roll features explicitly choose reset, same-contract returns or a
point-in-time continuous construction. Do not silently back-adjust execution
prices or incorporate future roll spreads into historical live features. The
current engine carries rules across contract ranges, so test any level-sensitive
indicator across rolls explicitly; this adaptation does not change that behavior.

We do not adopt generic source thresholds (five-year minimum, fixed Sharpe or
PBO cutoffs) as universal futures gates. Nor copy a purge example that includes
post-test training into chronological walk-forward. Purge by actual label-end
time and justify buffers for the chosen clock; embargo is not a license to fit
on future observations. Add CPCV/PBO only under a separately scoped research plan,
not as a replacement for untouched forward evidence.

## Local checks

From `backend/`, use an existing environment with repo requirements:

```bash
PLATFORM_SKIP_DB_INIT=1 DATABASE_URL=sqlite+pysqlite:////tmp/strategy-creation-test.db \
  ../.venv/bin/python -m pytest -q tests/test_causality.py tests/test_research_audit.py \
  tests/test_ml_research.py tests/test_mbo_research.py tests/test_catalog.py \
  tests/test_spec_validation.py tests/test_primitives.py tests/test_spec_strategy_engine.py \
  tests/test_mbo_entry_timing.py tests/test_backtest_worker.py tests/test_pnl.py
```

Override the Python path if the environment lives elsewhere. Use synthetic/local
fixtures; no ingestion/restoration/model search or live services as shortcuts.
Obtain independent review of the actual diff, resolve findings, and deliver
paths/branch/commit, checks and limitations. Software tests establish no profit.
