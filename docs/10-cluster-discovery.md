# Discover patterns, review a hypothesis, then evaluate

Open **ML discovery** in the app sidebar or visit `/ml`. The staged path is
separate from the earlier one-shot ML plans, which remain supported.

## Workflow

1. Prepare selected ES contract/day MBO features using the
   [feature preparation workflow](08-ml-research.md#prepare-once-and-reuse-local-feature-rows).
2. In ML discovery, choose the prepared dataset, research question, disjoint
   discovery/validation/internal-test dates and clustering settings. No trading
   direction, active cluster IDs, horizon or economic criteria are required yet.
3. Prepare the immutable discovery document, inspect its complete settings and
   provenance, record an approval reason, and separately click **Fit discovery
   clusters**. One fit is reserved durably before any learning occurs.
4. Inspect cluster centers in original feature units, counts, shares, within-group
   feature variability and per-session occupancy bounds. All are calculated from
   discovery features only. No forward outcomes or returns are calculated here.
5. Click **Request AI review**. The configured OpenAI model receives a fresh
   request containing only the question, contract, discovery dates and feature
   summary. It has no tools, conversation history, future-period observations or
   outcome metrics. It describes every cluster and either proposes one falsifiable
   hypothesis or advises stopping. Its structured output cannot approve or run a
   test. The review and its input/model/response/digest provenance are retained.
6. Accept or revise the proposed cluster selection, direction, horizon,
   interpretation, rationale and falsification condition. Supply round-trip costs
   and minimum evaluation criteria explicitly. A user may override a stop
   recommendation by recording a revision reason. This creates one immutable,
   **unapproved** hypothesis linked to the exact fit and AI review.
7. Inspect and approve that complete hypothesis document with a reason. Then
   separately choose **Evaluate on later data**. The fitted scaler and centers
   are reused; there is no refitting, parameter search or automatic revision.
8. Validation must pass the signal-count and mean-net-tick criteria and improve
   strictly upon the better of always signaling or never signaling, on the same
   per-observation denominator. Only passing validation opens internal-test
   metrics. Failure is `no_edge`; a technical failure is `blocked`. Passing both
   produces a descriptive `candidate`, not a profitability/significance claim.
9. Export candidates through the existing
   `POST /api/agent/threads/{thread}/ml-experiments/{experiment}/candidate-proposal`
   API with an explicit complete strategy and test plan. The existing exact
   approval and separate queue action still apply to Nautilus backtesting. Its
   frozen replay uses the original selected feature/price snapshot. No live or
   paper adapter is added.

Discovery and hypothesis approval dialogs show the complete recorded document
and its digest. Cancel and Escape do not write. Network/conflict errors require
reloading recorded state before another mutation; fitting, reviewing and
evaluation never resume automatically after an interruption. A reserved attempt
cannot be silently retried. Missing OpenAI configuration leaves a fitted
discovery available for review after configuration is supplied; an API attempt
that fails is recorded as `review_failed` and needs a new discovery for another
review attempt. The one-fit, one-review and one-hypothesis budgets are visible
in the discovery document.

**Stop discovery** appends a reasoned cancellation to the decision envelope,
retains original approval fields and fitted/reviewed evidence, and blocks further
work in the thread. An already running computation can finish and retain its
result, but cannot advance a stopped thread to the next stage. Evaluation that
has already reserved its later sessions may complete; stopping is not a rollback
of outcomes that were already exposed.

## Data boundary and exposure accounting

The complete price/feature snapshot is hash-pinned before discovery approval.
Only rows through `trainEnd`, and only the six feature columns, are passed to
clustering. Later partitions cannot change scaling, centers, descriptive cluster
summaries or the AI request. Labels are constructed only when preparing the
chosen hypothesis's feasibility checks and evaluated only after its approval.

Before evaluation, both validation and internal-test sessions are reserved in
`ml_heldout_uses`, keyed by fixed contract and ET date. The reservation is atomic
with consumption of the hypothesis approval. Another staged discovery cannot
evaluate those same contract/sessions, even from another thread or with different
extraction settings. Reservations conservatively consume both partitions even
when validation fails or a later technical problem prevents scoring. They are
not deleted to permit another search.

This ledger tracks **this staged workflow's reservations**. It cannot prove that
the user, legacy analysis/backtest endpoints or external tools have never viewed
those dates. External/legacy exposure remains unknown. The platform's final
holdout remains excluded; the validation and internal test are later partitions
inside the existing platform IS allocation. Overlapping horizon labels are not
independent trades. Nautilus validation reuses already-used partitions and is
an execution check, not fresh unseen evidence.

## API

The new prefix is `/api/agent/threads/{thread_id}/ml-discoveries`:

| Method and suffix | Action |
|---|---|
| `GET /` | Recorded discovery, decisions, fit/review and global fit count |
| `POST /` | Freeze a `ClusterDiscoveryPlan`; no fit or AI request |
| `POST /{id}/decision` | `{digest, action: approve or reject_stop, reason}` |
| `POST /{id}/fit` | Consume the approved one-fit reservation |
| `POST /{id}/review` | `{digest}`; consume one structured AI review request |
| `POST /{id}/hypothesis` | Freeze a `ClusterHypothesisRequest`; no evaluation |
| `POST /{id}/stop` | `{digest, reason}`; retain evidence and stop the thread |

Hypotheses use the existing `/ml-experiments/{id}/decision`, `/run` and
`/candidate-proposal` endpoints. Full schemas are available in `/docs`.

Discovery plans contain all prepared-feature extraction fields plus `method:
"mbo_cluster_discovery_v1"`, `researchQuestion`, `symbol: "ES1!"`, `interval:
"1min"`, `start`, `trainEnd`, `validationEnd`, `testEnd`,
`contractSelection: "fixed_contract_before_experiment"`, `clusterCount`,
`maxIterations` and `minClusterSamples`. `dataSource` defaults to
`prepared_MBO_features`; raw extraction remains an explicit API option.

Hypothesis requests bind `discoveryDigest` and `reviewDigest`, supply all
`ClusterHypothesis` fields, `reviewDisposition: "accept" or "revise"`,
`reviewReason`, the forward target, costs and economic criteria. Accepting must
preserve the recorded AI suggestion; changed choices require a recorded revision.
Exactly one hypothesis may be attached to a discovery. The assistant cannot
change cluster geometry, partition dates, costs or acceptance criteria through
the review response.

## Verification

Synthetic tests cover future-data invariance, feature-only fitting and reviewer
inputs, separate approvals, immutable hypothesis/review digests, AI stop advice
and explicit user overrides, invalid responses, missing configuration, concurrent
fit reservations, cross-thread session reuse, frozen-model evaluation and
candidate export. Both PostgreSQL and SQLite suites exercise the new schema.
Browser checks in `npm run test:ml-ui` intercept every API request and cover the
desktop/mobile flow, cancellation, Escape, no automatic testing after AI review
or approval, source-text escaping and page reload. No real-data fit, backtest or
OpenAI call is part of these checks.

AI review uses the SDK's structured Responses output as described in the
[official OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
