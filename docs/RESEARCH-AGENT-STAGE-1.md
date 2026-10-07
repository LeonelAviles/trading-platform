# Research agent: approved experiments and evidence memory

This stage is backend/API-only. It extends the existing BM25 retrieval and SQLite
workflow instead of introducing GraphRAG, another agent framework, or a second
execution engine. It supports historical **ES1! RTH** research, bars/ticks only.
No live trading, source purchasing, ingestion repair, UI redesign, or automated
parameter sweep is included.

## User-control contract

A chat request to test creates a proposal; it does not authorize execution.
The model has no ingestion or decision tool. The direct user HTTP boundary is the
same single-user application boundary as the existing platform (this change does
not add authentication or multi-tenant isolation).

1. Read `GET /api/agent/research/context` and the existing spec/primitive schema.
2. `POST /api/agent/threads/{threadId}/proposals` with:

   ```json
   {
     "hypothesis": "The user's exact proposed hypothesis",
     "strategy": {"schemaVersion": 2, "name": "An incomplete draft is allowed"},
     "testPlan": {
       "windows": ["is", "wf1", "wf2", "wf3"],
       "objective": "User-chosen research objective",
       "comparison": "User-chosen comparison or descriptive only",
       "uncertaintyPolicy": "Unresolved; descriptive only",
       "multipleTestingPolicy": "No significance claim; external trials unknown",
       "dataPolicy": "metadata_inventory_not_immutable_snapshot"
     },
     "concepts": ["user-provided concept"],
     "passageIds": [],
     "parentId": null
   }
   ```

   This example deliberately remains a **draft**: missing trading choices and
   absent data produce explicit `blockers`, not invented defaults. A complete
   proposal includes every execution/risk field and primitive parameter, even
   disabled `null`/`[]` options. Existing schema defaults are legacy conveniences,
   not evidence of user choices. Overnight sessions and L3 are unsupported here.
   The worker does not implement weekly controls or volatility-scaled sizing:
   nonzero `weeklyLossLimitPct`, non-null `weeklyTargetPct`, `vol_scaled`, and
   more than one concurrent position block execution. Explicitly disabling an
   unsupported control requires the user's choice; the agent must not choose it.
   Duplicated risk/sizing/constraint settings must agree.
   Structural stops and level targets currently block exact-approved execution:
   their worker fallback distances and implicit swing lookbacks are unsupported.
   Primitive lookbacks/counts must be positive, applicable size/distance parameters
   nonnegative, and numeric choices finite and correctly typed; null is accepted
   only for a documented optional price parameter. Both bars and ticks support effective slippage of only
   0 or 1 (the entry FillModel cannot express larger magnitudes); a null override resolves to the configuration captured in
   `document.effectiveExecution`, which also states commission per side.
   Inference via a deflated-Sharpe threshold is blocked while total search trials
   are unknown; no default significance policy is chosen.

3. Show the **whole returned `document`**, including hypothesis, strategy, test
   plan, dataset/config inventory and passages. Responses also contain `id`,
   `threadId`, `parentId`, `digest`, `blockers`, `status`, `decision`, `strategyId`,
   `runId`, `evidence`, `createdAt`. The digest covers the entire immutable document.
4. `POST /api/agent/threads/{threadId}/proposals/{id}/decision`:

   ```json
   {"digest":"<exact returned SHA-256>","action":"approve","reason":"User's review reason"}
   ```

   Actions: `approve`, `reject_stop`, `reject_revise`. Decisions are immutable;
   identical retries are idempotent. A changed decision requires a new proposal.
   `reject_revise` allows preparing another proposal, never running it.
   `reject_stop` prevents subsequent execution in that thread; an already approved
   running batch is not cancelled. Draft approval, digest mismatch, and conflicting
   decisions return HTTP 409. Invalid request shape is 422; semantic input errors 400.
5. `POST /api/agent/threads/{threadId}/proposals/{id}/queue` (no body).
   Requires the recorded exact approval. A retry returns the same strategy/batch;
   it never grants another attempt. Stale dataset/config or workflow state returns
   409 with no jobs created. A changed input needs a fresh proposal and approval.
   UI can navigate to `GET .../workflow`'s `chartJobId` after queue success.
6. `GET .../proposals?limit=20&offset=0` (newest first, max 100), `GET .../proposals/{id}` and `GET .../workflow` expose
   history and progress. The thread endpoint also includes a `research` field.
   Proposal status `queued` records consumed approval; individual `evidence`
   statuses and the workflow describe completion/failure.

The selected test windows must be sufficient for the explicitly supplied
`minWalkForwardWindowsPositive`; no missing window is counted as passed. Required
but unavailable report metrics produce `untestable`, never pass/champion.

Every approved child is a new strategy version, links its parent proposal, and
must specify one logical changed executable choice, matching `lineage.changedVariable`,
`lineage.parentId`, incremented `trialIndex`, and a rationale. Synchronized aliases
count as one choice with canonical paths `risk.riskPerTradePct` (alias sizing.value
for fixed risk), `sizing.maxContracts`, `constraints.maxTradesPerDay`, and
`constraints.stopAfterConsecutiveLosses`. Exact approved snapshots retain both
fields. Array changes recurse to indexed paths such as `filters[0].args[1]`;
changing multiple independent array parameters remains forbidden. Prior proposals,
rejection reasons, exact job IDs and strategy snapshots remain available. The
controller enforces the existing five-change / three-non-improvement budget.
Automatic completion analysis is read-only and waits for user review. Technical
and missing-data failures are blocked/inconclusive, not evidence of no edge.

## Persistence and queue ordering

Migration `e6f8a0b2c4d6` adds `research_proposals`, `research_decisions`, and
`research_evidence`. Foreign keys retain strategy/job evidence; no old approval
is synthesized. Legacy manual strategy normalization remains compatible. Agent
strategy versions are immutable except for lifecycle status. Legacy queue routes
reject agent strategies and research-linked strategy IDs.

SQLite `BEGIN IMMEDIATE` serializes approval consumption and workflow readiness
across API processes. One transaction creates the immutable version, workflow,
all job rows and evidence links. Files are staged before commit and cleaned on
rollback; no worker is dispatched before commit. A crash before commit may leave
an unreferenced job directory, never a runnable database row. A crash after commit
is recovered by the existing durable job recovery. If a crash follows analysis
reservation but precedes verdict persistence, recovery reconstructs the candidate
from its completed jobs before resuming; counters and events are applied once. Identical queue requests do
not dispatch twice. Worker input verification rejects changed approved strategy
artifacts or changed dataset/config inventories before launching Nautilus. Legacy
agent jobs without a proposal are rejected at this worker boundary too; an upgrade
does not silently grant approval to old queued work.

The existing worker dispatcher is designed for **one API/worker process**. The
approval transaction is cross-process safe; this does not turn the existing
in-process execution queue into a distributed worker lease system. Run one worker
owner. A dispatch failure after commit leaves a recoverable queued batch.

Normal startup runs Alembic to head. Stop old backend workers before upgrading;
this is a code/migration deliverable, not an instruction to deploy. Back up the
metadata DB before any downgrade. Downgrading removes the new audit tables and
loses their history; old strategies/jobs remain. No market-data migration occurs. Any pre-release proposal lacking pinned session
membership cannot provide verifiable research evidence and needs a new proposal.

## Selected-source ingestion and citations

`POST /api/agent/knowledge/text` accepts `{title, content, kind, sourceUrl?,
author?, license?}`. Kind is `user_note` or `selected_source`; selected sources
require an HTTP(S) attribution URL. It ingests supplied text only, never fetches
that URL, opens local paths, executes content, or purchases sources. Text is
limited to 2 MB. Short notes are kept. Each submission is a new immutable version
with a content SHA-256. Author/license claims are user assertions, not verified.
All retrieved text is explicitly untrusted reference material.

BM25 search returns stable passage IDs, content hashes, source revisions, citation
labels and snapshot URLs. `GET /api/agent/knowledge/passages/{id}` retrieves the
exact passage. Repository re-ingestion preserves older source versions/passages;
old versions remain searchable and carry their pinned revisions. Proposals embed
cited text/hash/source metadata so later source maintenance cannot rewrite the
approved rationale. This is provenance plus relational memory, not inferred
concept-to-concept knowledge graph construction.

## Quantitative evidence tools

- `get_trade_evidence(job_id, offset, limit)` pages all existing completed ES IS/WF
  trades (1–200 per page). The HTTP counterpart is
  `GET /api/agent/evidence/{jobId}/trades?offset=0&limit=50`. Missing legacy fields
  remain null. Every entry snapshot is explicitly unavailable; an `entryContextId`
  alone is not snapshot evidence. OOS/full jobs are refused. IS/WF labels alone
  are insufficient: actual requested dates, trade sessions/timestamps and daily
  return dates must fit permitted frozen ranges/session membership. Research
  validation reports and job-list metrics use the same gate. Unverifiable legacy
  dates fail closed; approved batches use their pinned session membership.
- `compare_trade_groups` selects by direction, exit reason, regime, session range
  or entry hour (ET) and reports sample sizes, overlap, PnL and R observations.
  Optional uncertainty requires an explicit policy with `method` equal to
  `session_cluster_bootstrap`, `confidence`, `resamples` (100–10,000), `seed`, and
  `minSessions` (at least 2). It resamples paired sessions to preserve intraday and
  group-overlap dependence. Missing evidence or too few sessions yields unavailable.
  Analysis is bounded to 32 MiB / 100,000 trades per artifact and 2,000,000 total
  bootstrap session draws; larger requests return explicit errors, not truncation.
  Sessions are assumed exchangeable; inter-session dependence and selection bias
  are not corrected. Intervals are descriptive, not proof of significance.
- `get_stability_analysis(job_ids, extra_costs_usd)` describes existing results by
  month and regime and applies explicitly supplied additive costs per trade to
  unchanged fills. At most 20 jobs/scenarios. It runs no backtests, parameter
  sweeps or winner selection. Jobs/windows may overlap and are not independent
  replications. Regime labels may use the full session: hindsight stratification
  must never become an entry-time feature claim.

Recorded experiment counts are a global lower bound. Legacy/external trials and
post-hoc descriptive comparisons are unknown. Existing validation reports remain
heuristics; no multiplicity-adjusted significance is claimed.

## Bounded coverage and verification

Dataset identity includes frozen in-sample session membership, split/manifest/front-month hashes, engine source hash and configuration
and a file-size/mtime inventory of ES derived partitions. It is **not an immutable
market-data snapshot** or full content hash of market files, does not verify every
requested session's quality, and cannot prevent writes during a running worker.
This limitation is included in the exact user-reviewed plan. Missing required
files block proposals; runtime gaps produce technical failures. Entry-context
snapshot capture/retrieval, full data immutability, L3 approval, overnight trading,
and elaborate GraphRAG are deferred explicitly.

Software checks use disposable databases, supplied-text fixtures and synthetic
trades/bars. No real-data research experiment or market backtest was run for this
change. See the PR for final focused/aggregate results and baseline failures.

## Post-opening review

Review fixes cover legacy-job approval bypass on recovery, the interval between
analysis reservation and recorded evidence, explicit worker-policy compatibility,
analysis/input resource bounds, retained-evidence delete errors (409), paginated
history, and removal of the obsolete non-atomic child-queue implementation.
Migration upgrade/downgrade/upgrade preserves legacy rows without synthesizing
approvals. The single-worker and metadata-only identity limitations above remain
explicit rather than being presented as distributed leases or data snapshots.

Independent review regressions additionally cover missing required WF/metric
evidence, interrupted analysis reconstruction, canonical risk aliases and nested
array diffs, mislabeled holdout artifacts across every research read path,
implicit exit fallbacks, primitive parameter domains, effective slippage, and
source-author/license assertions in passage retrieval and approval snapshots.
