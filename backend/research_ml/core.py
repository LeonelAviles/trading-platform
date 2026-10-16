"""Pure, bounded chronological discovery. No filesystem, agents or execution.

Labels are forward *outcomes*, not features. Every partition purges labels at
its end, at missing minutes and at the session boundary. Later partitions
cannot affect quantiles or selection. No final platform holdout is consumed.
"""
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from research_ml.schema import Plan
from research_ml.regimes import VolumeRegimes
from engine.mbo_features import FEATURES
from research_ml.multivariate import MBORidge, MBOKMeans

MAX_BARS = 50_000
COLUMNS = ["open", "high", "low", "close", "volume"]


def validate_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if not 1 <= len(bars) <= MAX_BARS:
        raise ValueError("dataset requires 1..50,000 bars")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("bar timestamps must be timezone-aware")
    if not bars.index.is_monotonic_increasing or not bars.index.is_unique:
        raise ValueError("timestamps must be unique and increasing")
    if any(c not in bars for c in COLUMNS):
        raise ValueError("OHLCV columns are required")
    extra = FEATURES if any(c in bars for c in FEATURES) else []
    if any(c not in bars for c in extra):
        raise ValueError("all ordered MBO features are required")
    frame = bars[COLUMNS + extra].astype(float).copy()
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("OHLCV must be finite; missing values are not imputed")
    if (frame.volume < 0).any() or (frame[COLUMNS[:4]] <= 0).any().any():
        raise ValueError("prices must be positive and volume nonnegative")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
            (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("inconsistent OHLC bars")
    if (frame.index != frame.index.floor("min")).any():
        raise ValueError("expected minute-open timestamps")
    return frame


def samples(bars: pd.DataFrame, plan: Plan, tick_size: float) -> dict[str, pd.DataFrame]:
    if not np.isfinite(tick_size) or tick_size <= 0:
        raise ValueError("positive finite tick size required")
    frame = validate_bars(bars)
    local = frame.index.tz_convert("America/New_York")
    # RTH-only baseline. Filter before shifting; gaps are still rejected below.
    frame = frame[(local.strftime("%H:%M") >= "09:30") & (local.strftime("%H:%M") < "16:00")]
    days = frame.index.tz_convert("America/New_York").date
    masks = {
        "train": (days >= plan.start) & (days <= plan.trainEnd),
        "validation": (days > plan.trainEnd) & (days <= plan.validationEnd),
        "test": (days > plan.validationEnd) & (days <= plan.testEnd),
    }
    out = {}
    h = plan.horizonBars
    for key, mask in masks.items():
        part = frame.loc[mask].copy()
        ts = pd.Series(part.index, index=part.index)
        # Difference of h minutes proves all intervening minute bars exist,
        # since inputs are unique minute-aligned timestamps.
        continuous = (ts.shift(-h) - ts) == pd.Timedelta(minutes=h)
        day = pd.Series(part.index.tz_convert("America/New_York").date, index=part.index)
        valid = continuous & (day.shift(-h) == day)
        sign = 1 if plan.direction == "long" else -1
        part["netTicks"] = sign * (part.close.shift(-h) - part.open.shift(-1)) / tick_size - plan.roundTripCostTicks
        part["labelEnd"] = ts.shift(-h) + pd.Timedelta(minutes=1)
        inputs = FEATURES if plan.method.startswith("mbo_") else ["volume"]
        if any(c not in part for c in inputs):
            raise ValueError("MBO methods require verified raw-event features; OHLCV cannot substitute")
        out[key] = part.loc[valid, [*inputs, "netTicks", "labelEnd"]]
        if not np.isfinite(out[key]["netTicks"]).all():
            raise ValueError("non-finite forward targets")
        if out[key].empty:
            raise ValueError(f"{key} has no complete, contiguous within-session targets")
    return out


@dataclass(frozen=True)
class Threshold:
    threshold: float
    op: str

    def predict(self, values):
        values = np.asarray(values)
        return values > self.threshold if self.op == "gt" else values <= self.threshold

    def expression(self) -> dict:
        return {"op": self.op, "args": [{"field": "volume"}, self.threshold]}

    def artifact(self):
        return {"threshold": self.threshold, "op": self.op,
                "preprocessing": "training_only_volume_quantiles", "trigger": self.expression()}


class FittedRule(Protocol):
    """Frozen inference and portable expression have identical signal semantics."""
    def predict(self, values): ...
    def expression(self) -> dict: ...
    def artifact(self) -> dict: ...


class Method(Protocol):
    """Plugins only receive training samples; export must preserve predictions."""
    requires_targets: bool
    def fit(self, train: pd.DataFrame, plan: Plan) -> tuple[FittedRule | None, list[dict]]: ...


class VolumeThreshold:
    requires_targets = True

    def fit(self, train, plan):
        # Quantile transformation is the only learned preprocessing. Its cut
        # points are fitted on training data and exported in original units.
        thresholds = np.quantile(train.volume, sorted(plan.quantiles), method="linear")
        trials, winner, best = [], None, -np.inf
        for threshold in sorted(set(float(t) for t in thresholds)):
            for op in ("gt", "lte"):
                model = Threshold(threshold, op)
                selected = model.predict(train.volume)
                count = int(selected.sum())
                score = float(train.netTicks[selected].sum() / len(train))
                trials.append({"threshold": threshold, "op": op, "signals": count, "netTicksPerBar": score})
                if count >= plan.minTrainSignals and score > best:
                    best, winner = score, model
        return winner, trials


METHODS: dict[str, Method] = {
    "volume_threshold_v1": VolumeThreshold(),
    "volume_regimes_v1": VolumeRegimes(),
    "mbo_ridge_v1": MBORidge(),
    "mbo_kmeans_v1": MBOKMeans(),
}


def evaluate(model, data):
    selected = model.predict(data[FEATURES] if "spread_ticks" in data else data.volume)
    values = data.netTicks[selected]
    return {"observations": len(data), "signals": len(values),
            "meanNetTicks": float(values.mean()) if len(values) else None,
            "netTicksPerBar": float(values.sum() / len(data)),
            "alwaysSignalNetTicksPerBar": float(data.netTicks.mean()),
            "noSignalNetTicksPerBar": 0.0}


def accepted(metrics, plan):
    # Compare on the same per-observation denominator, with the no-signal
    # baseline included. Strict improvement avoids a zero-edge tie passing.
    baseline = max(0.0, metrics["alwaysSignalNetTicksPerBar"])
    return (metrics["signals"] >= plan.minEvaluationSignals and
            metrics["meanNetTicks"] is not None and metrics["meanNetTicks"] >= plan.minMeanNetTicks and
            metrics["netTicksPerBar"] > baseline + plan.minImprovementOverBaselineTicks)


def discover(bars, plan: Plan, tick_size: float) -> dict:
    parts = samples(bars, plan, tick_size)
    method = METHODS[plan.method]
    inputs = FEATURES if plan.method.startswith("mbo_") else ["volume"]
    columns = inputs + (["netTicks"] if method.requires_targets else [])
    model, trials = method.fit(parts["train"][columns].copy(), plan)
    result = {"outcome": "no_edge", "reason": "insufficient training signals", "trials": trials,
              "learningKind": "supervised" if method.requires_targets else "unsupervised",
              "selectionCount": len(trials), "model": None, "metrics": {},
              "splitCounts": {k: len(v) for k, v in parts.items()},
              "evidenceKind": "overlapping_forward_labels_not_trade_pnl",
              "significance": "not established", "finalHoldoutUsed": False}
    if model is None:
        return result
    result["model"] = {"method": plan.method, **model.artifact()}
    result["metrics"] = {"train": evaluate(model, parts["train"]),
                         "validation": evaluate(model, parts["validation"])}
    if not accepted(result["metrics"]["validation"], plan):
        result["reason"] = "validation criteria failed; internal test not evaluated"
        return result
    result["metrics"]["test"] = evaluate(model, parts["test"])
    if not accepted(result["metrics"]["test"], plan):
        result["reason"] = "internal chronological test criteria failed"
        return result
    result.update(outcome="candidate", reason="descriptive candidate; requires separately approved Nautilus validation")
    return result
