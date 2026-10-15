"""Every research choice is explicit; there are no trading-policy defaults."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Plan(StrictModel):
    hypothesis: str = Field(min_length=1, max_length=4000)
    method: Literal["volume_threshold_v1"]
    symbol: Literal["ES1!"]
    interval: Literal["1min"]
    direction: Literal["long", "short"]
    start: date
    trainEnd: date
    validationEnd: date
    testEnd: date
    target: Literal["next_open_to_horizon_close_ticks"]
    horizonBars: int = Field(strict=True, ge=1, le=60)
    roundTripCostTicks: float = Field(ge=0)
    quantiles: list[float] = Field(min_length=1, max_length=50)
    maxCandidates: int = Field(strict=True, ge=2, le=100)
    minTrainSignals: int = Field(strict=True, ge=1)
    minEvaluationSignals: int = Field(strict=True, ge=1)
    minMeanNetTicks: float
    minImprovementOverBaselineTicks: float = Field(ge=0)
    multipleTestingPolicy: Literal["descriptive_only_no_significance_claim"]

    @model_validator(mode="after")
    def choices(self):
        if not self.hypothesis.strip():
            raise ValueError("hypothesis is required")
        if not self.start <= self.trainEnd < self.validationEnd < self.testEnd:
            raise ValueError("require start <= trainEnd < validationEnd < testEnd")
        if (self.testEnd - self.start).days > 366:
            raise ValueError("one experiment is bounded to 366 days and 50,000 bars")
        if any(not 0 < q < 1 for q in self.quantiles) or len(set(self.quantiles)) != len(self.quantiles):
            raise ValueError("quantiles must be unique finite numbers strictly between 0 and 1")
        if len(self.quantiles) * 2 > self.maxCandidates:
            raise ValueError("two threshold directions per quantile exceed maxCandidates")
        return self


class AccountPolicy(StrictModel):
    """Explicit subset of observed metrics, never a certification of firm rules."""
    name: str = Field(min_length=1, max_length=200)
    startingEquity: float = Field(gt=0)
    minNetPnlUsd: float
    maxClosedTradeDrawdownPct: float = Field(ge=0, le=100)
    maxDailyNetLossUsd: float = Field(ge=0)
    minTrades: int = Field(strict=True, ge=1)
