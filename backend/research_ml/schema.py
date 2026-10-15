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


Plan = Annotated[ThresholdPlan | RegimePlan, Field(discriminator="method")]
_PLAN_ADAPTER = TypeAdapter(Plan)


def parse_plan(value) -> ThresholdPlan | RegimePlan:
    return _PLAN_ADAPTER.validate_python(value)


class AccountPolicy(StrictModel):
    """Explicit subset of observed metrics, never a certification of firm rules."""
    name: str = Field(min_length=1, max_length=200)
    startingEquity: float = Field(gt=0)
    minNetPnlUsd: float
    maxClosedTradeDrawdownPct: float = Field(ge=0, le=100)
    maxDailyNetLossUsd: float = Field(ge=0)
    minTrades: int = Field(strict=True, ge=1)
