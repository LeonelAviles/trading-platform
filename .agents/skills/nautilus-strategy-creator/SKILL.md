---
name: nautilus-strategy-creator
description: Create, adapt, or review trading-platform Strategy Spec v2 strategies and causal features using the existing Nautilus execution and validation stack.
---

# Nautilus strategy creator

Read `docs/12-strategy-creation.md` from the repository root before implementation.
Keep NautilusTrader and Strategy Spec v2. Inspect current schema and code rather
than treating historical docs as an API. Start with explicit hypothesis, data,
clock, entry/exit, risk, costs and evaluation assumptions. Resolve missing blockers
and state conservative noncritical assumptions. Prefer a schema-valid spec over
new execution code; reuse registered primitives, FeatureContext and SpecRules.

For novel features use `engine.causality.assert_prefix_causal` on small synthetic
fixtures with fresh state per callback. Separately prove completed-bar availability,
warm-up, session resets and context timeframe boundaries. Prefix invariance does
not prove correct timestamps, provenance or train-only model fitting.

Freeze chronological partitions, trial budget and acceptance criteria before
outcomes. Existing WF blocks do not automatically refit models. Fit transforms
on training only and purge overlapping forward labels. Preserve final holdout
and record all trials and prior exposure. Do not initiate an economic search
merely because this skill was invoked.

Verify futures costs, supported fills, stop/sizing edge cases and closed/open
accounting. Distinguish synthetic end-of-data liquidation from venue fills.
Deliver reproducible evidence and limitations, never a profitability assertion.
This skill grants no authority for paid calls, downloads, S3, live trading,
publication or deployment; follow the current task's authorization.
