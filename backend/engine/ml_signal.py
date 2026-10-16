"""Versioned, bounded numeric inference shared by discovery and Nautilus."""
import json

import numpy as np
from pydantic import Field, model_validator
from typing import Literal
from research_ml.schema import StrictModel
from engine.mbo_features import FEATURES, FEATURE_VERSION


class FrozenModel(StrictModel):
    version: Literal["frozen_mbo_v1"] = "frozen_mbo_v1"
    featureVersion: Literal["mbo_receive_minute_v1"] = FEATURE_VERSION
    kind: Literal["ridge", "kmeans"]
    features: list[str]
    mean: list[float]
    scale: list[float]
    coefficients: list[float] = Field(default_factory=list)
    intercept: float = 0.0
    threshold: float = 0.0
    centers: list[list[float]] = Field(default_factory=list)
    active: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def dimensions(self):
        n = len(FEATURES)
        if self.features != FEATURES or len(self.mean) != n or len(self.scale) != n or any(x <= 0 for x in self.scale):
            raise ValueError("frozen feature order/scaler mismatch")
        if self.kind == "ridge":
            if len(self.coefficients) != n or self.centers or self.active:
                raise ValueError("invalid frozen ridge dimensions")
        elif (self.coefficients or not 2 <= len(self.centers) <= 8
              or any(len(c) != n for c in self.centers) or not self.active
              or len(set(self.active)) != len(self.active)
              or any(type(k) is not int or not 0 <= k < len(self.centers) for k in self.active)):
            raise ValueError("invalid frozen cluster dimensions")
        return self

    def predict(self, values):
        x = np.asarray(values, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(FEATURES) or not np.isfinite(x).all():
            raise ValueError("complete finite ordered MBO feature matrix required")
        z = (x - np.asarray(self.mean)) / np.asarray(self.scale)
        if not np.isfinite(z).all():
            raise ValueError("non-finite normalized features")
        if self.kind == "ridge":
            # Identical per-row arithmetic for batch and single-row inference.
            score = (z * np.asarray(self.coefficients)).sum(axis=1) + self.intercept
            if not np.isfinite(score).all():
                raise ValueError("non-finite model score")
            return score > self.threshold
        distance = ((z[:, None, :] - np.asarray(self.centers)[None, :, :]) ** 2).sum(axis=2)
        if not np.isfinite(distance).all():
            raise ValueError("non-finite cluster distance")
        return np.isin(distance.argmin(axis=1), self.active)

    def expression(self):
        return {"op": "eq", "args": [{"ind": "frozen_mbo_signal", "params": {"model": self.model_dump_json()}}, True]}

    def artifact(self):
        return {"frozen": self.model_dump(), "preprocessing": "training_only_population_standardization",
                "trigger": self.expression()}


def parse_frozen(text):
    if not isinstance(text, str) or len(text.encode()) > 16_000:
        raise ValueError("bounded JSON frozen model required")
    return FrozenModel.model_validate(json.loads(text))
