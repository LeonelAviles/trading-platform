"""Every research choice is explicit; there are no trading-policy defaults."""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CommonPlan(StrictModel):
    hypothesis: str = Field(min_length=1, max_length=4000)
    symbol: Literal["ES1!"]
    interval: Literal["1min"]
    direction: Literal["long", "short"]
    start: date
    trainEnd: date
    validationEnd: date
    testEnd: date
    target: Literal["next_open_to_horizon_close_ticks"]
    horizonBars: int = Field(strict=True, ge=1, le=60)
    roundTripCostTicks: float = Field(strict=True, ge=0)
    minEvaluationSignals: int = Field(strict=True, ge=1)
    minMeanNetTicks: float = Field(strict=True)
    minImprovementOverBaselineTicks: float = Field(strict=True, ge=0)
    multipleTestingPolicy: Literal["descriptive_only_no_significance_claim"]

    @model_validator(mode="after")
    def choices(self):
        if not self.hypothesis.strip():
            raise ValueError("hypothesis is required")
        if not self.start <= self.trainEnd < self.validationEnd < self.testEnd:
            raise ValueError("require start <= trainEnd < validationEnd < testEnd")
        if (self.testEnd - self.start).days > 366:
            raise ValueError("one experiment is bounded to 366 days and 50,000 bars")
        return self


class ThresholdPlan(CommonPlan):
    method: Literal["volume_threshold_v1"]
    quantiles: list[Annotated[float, Field(strict=True)]] = Field(min_length=1, max_length=50)
    maxCandidates: int = Field(strict=True, ge=2, le=100)
    minTrainSignals: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def search_budget(self):
        if any(not 0 < q < 1 for q in self.quantiles) or len(set(self.quantiles)) != len(self.quantiles):
            raise ValueError("quantiles must be unique finite numbers strictly between 0 and 1")
        if len(self.quantiles) * 2 > self.maxCandidates:
            raise ValueError("two threshold directions per quantile exceed maxCandidates")
        return self


class RegimePlan(CommonPlan):
    method: Literal["volume_regimes_v1"]
    clusterCount: int = Field(strict=True, ge=2, le=8)
    maxIterations: int = Field(strict=True, ge=1, le=100)
    minClusterSamples: int = Field(strict=True, ge=1)
    activeClusters: list[Annotated[int, Field(strict=True, ge=0)]] = Field(min_length=1, max_length=8)
    interpretation: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def cluster_rule(self):
        if len(set(self.activeClusters)) != len(self.activeClusters) or any(k >= self.clusterCount for k in self.activeClusters):
            raise ValueError("activeClusters must be unique zero-based ranks below clusterCount")
        if not self.interpretation.strip():
            raise ValueError("explicit interpretation of the selected volume regimes is required")
        return self


class MBOSource(StrictModel):
    day: date
    instrumentId: int = Field(strict=True, gt=0, le=2**32-1)
    publisherId: int = Field(strict=True, gt=0, le=2**16-1)
    channelId: int = Field(strict=True, ge=0, le=255)


class MBOFeatureRequest(StrictModel):
    """Data preparation only: no outcomes, fitting or trading choices."""
    contract: str = Field(pattern=r"^ES[HMUZ][0-9]{1,2}$")
    sources: list[MBOSource] = Field(min_length=1, max_length=366)
    depthLevels: int = Field(strict=True, ge=1, le=50)
    featureWindowSeconds: Literal[60]
    maxBookAgeMs: int = Field(strict=True, ge=1, le=3_600_000)
    maxEventLagMs: int = Field(strict=True, ge=1, le=3_600_000)
    maxUnknownTradeFraction: float = Field(strict=True, ge=0, le=1)

    @model_validator(mode="after")
    def source_days(self):
        days = [s.day for s in self.sources]
        if days != sorted(set(days)):
            raise ValueError("sources must be unique, increasing days")
        return self


class MBOPlan(CommonPlan, MBOFeatureRequest):
    contractSelection: Literal["fixed_contract_before_experiment"]
    sources: list[MBOSource] = Field(min_length=3, max_length=366)
    dataSource: Literal["raw_DBN_MBO", "prepared_MBO_features"] = "raw_DBN_MBO"

    @model_validator(mode="after")
    def experiment_days(self):
        if any(not self.start <= s.day <= self.testEnd for s in self.sources):
            raise ValueError("sources must be inside the experiment")
        return self


class MBORidgePlan(MBOPlan):
    method: Literal["mbo_ridge_v1"]
    ridgeAlpha: float = Field(strict=True, gt=0)
    predictionThresholdTicks: float = Field(strict=True)
    minTrainSignals: int = Field(strict=True, ge=1)


class MBOClusterPlan(MBOPlan):
    method: Literal["mbo_kmeans_v1"]
    clusterCount: int = Field(strict=True, ge=2, le=8)
    maxIterations: int = Field(strict=True, ge=1, le=100)
    minClusterSamples: int = Field(strict=True, ge=1)
    activeClusters: list[Annotated[int, Field(strict=True, ge=0)]] = Field(min_length=1, max_length=8)
    interpretation: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def cluster_rule(self):
        if len(set(self.activeClusters)) != len(self.activeClusters) or any(k >= self.clusterCount for k in self.activeClusters):
            raise ValueError("activeClusters must be unique zero-based IDs below clusterCount")
        if not self.interpretation.strip():
            raise ValueError("explicit cluster interpretation required")
        return self


class ClusterDiscoveryPlan(MBOFeatureRequest):
    """Feature-only discovery. Trading direction and active IDs come later."""
    method: Literal["mbo_cluster_discovery_v1"]
    researchQuestion: str = Field(min_length=1, max_length=4000)
    symbol: Literal["ES1!"]
    interval: Literal["1min"]
    start: date
    trainEnd: date
    validationEnd: date
    testEnd: date
    contractSelection: Literal["fixed_contract_before_experiment"]
    sources: list[MBOSource] = Field(min_length=3, max_length=366)
    dataSource: Literal["raw_DBN_MBO", "prepared_MBO_features"] = "prepared_MBO_features"
    clusterCount: int = Field(strict=True, ge=2, le=8)
    maxIterations: int = Field(strict=True, ge=1, le=100)
    minClusterSamples: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def discovery_dates(self):
        if (not self.researchQuestion.strip() or
                not self.start <= self.trainEnd < self.validationEnd < self.testEnd):
            raise ValueError("research question and ordered discovery/validation/test dates required")
        if (self.testEnd - self.start).days > 366 or any(
                not self.start <= s.day <= self.testEnd for s in self.sources):
            raise ValueError("sources must be within a discovery range of at most 366 days")
        return self


class ClusterInterpretation(StrictModel):
    clusterId: int = Field(strict=True, ge=0, le=7)
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    concerns: list[str] = Field(max_length=8)


class ClusterHypothesis(StrictModel):
    hypothesis: str = Field(min_length=1, max_length=4000)
    activeClusters: list[Annotated[int, Field(strict=True, ge=0, le=7)]] = Field(min_length=1, max_length=8)
    direction: Literal["long", "short"]
    horizonBars: int = Field(strict=True, ge=1, le=60)
    interpretation: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    falsification: str = Field(min_length=1, max_length=2000)


class ClusterReview(StrictModel):
    assessment: str = Field(min_length=1, max_length=4000)
    clusters: list[ClusterInterpretation] = Field(min_length=2, max_length=8)
    recommendation: Literal["propose", "stop"]
    suggestion: ClusterHypothesis | None


class ClusterHypothesisRequest(ClusterHypothesis):
    discoveryDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewDisposition: Literal["accept", "revise"]
    reviewReason: str = Field(min_length=1, max_length=4000)
    target: Literal["next_open_to_horizon_close_ticks"]
    roundTripCostTicks: float = Field(strict=True, ge=0)
    minEvaluationSignals: int = Field(strict=True, ge=1)
    minMeanNetTicks: float = Field(strict=True)
    minImprovementOverBaselineTicks: float = Field(strict=True, ge=0)
    multipleTestingPolicy: Literal["descriptive_only_no_significance_claim"]

    @model_validator(mode="after")
    def explicit_text(self):
        for field in ("hypothesis", "interpretation", "rationale", "falsification", "reviewReason"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} requires explicit text")
        if len(set(self.activeClusters)) != len(self.activeClusters):
            raise ValueError("activeClusters must be unique")
        return self


Plan = Annotated[ThresholdPlan | RegimePlan | MBORidgePlan | MBOClusterPlan, Field(discriminator="method")]
_PLAN_ADAPTER = TypeAdapter(Plan)


def parse_plan(value) -> ThresholdPlan | RegimePlan | MBORidgePlan | MBOClusterPlan:
    return _PLAN_ADAPTER.validate_python(value)


class AccountPolicy(StrictModel):
    """Explicit subset of observed metrics, never a certification of firm rules."""
    name: str = Field(min_length=1, max_length=200)
    startingEquity: float = Field(gt=0)
    minNetPnlUsd: float
    maxClosedTradeDrawdownPct: float = Field(ge=0, le=100)
    maxDailyNetLossUsd: float = Field(ge=0)
    minTrades: int = Field(strict=True, ge=1)
