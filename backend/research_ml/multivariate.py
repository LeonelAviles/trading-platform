"""Single approved fit, all six causal features jointly; no hidden search."""
import numpy as np
from engine.mbo_features import FEATURES
from engine.ml_signal import FrozenModel


def standardize(train, targets):
    expected = FEATURES + (["netTicks"] if targets else [])
    if list(train.columns) != expected:
        raise ValueError("training matrix must contain exactly the ordered features and permitted target")
    x = train[FEATURES].to_numpy(dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("non-finite training features")
    mean, scale = x.mean(axis=0), x.std(axis=0, ddof=0)
    scale[scale == 0] = 1.0  # Explicit constant-column policy; no held-out statistics.
    return (x - mean) / scale, mean, scale


class MBORidge:
    requires_targets = True

    def fit(self, train, plan):
        x, mean, scale = standardize(train, True)
        y = train.netTicks.to_numpy(dtype=float)
        intercept = float(y.mean())
        coef = np.linalg.solve(x.T @ x + plan.ridgeAlpha * np.eye(len(FEATURES)), x.T @ (y-intercept))
        model = FrozenModel(kind="ridge", features=FEATURES, mean=mean.tolist(), scale=scale.tolist(),
                            coefficients=coef.tolist(), intercept=intercept, threshold=plan.predictionThresholdTicks)
        count = int(model.predict(train[FEATURES]).sum())
        trial = {"ridgeAlpha": plan.ridgeAlpha, "predictionThresholdTicks": plan.predictionThresholdTicks,
                 "signals": count, "jointFeatureCount": len(FEATURES)}
        return (model if count >= plan.minTrainSignals else None), [trial]


class MBOKMeans:
    requires_targets = False

    def fit(self, train, plan):
        x, mean, scale = standardize(train, False)
        unique = np.unique(x, axis=0)  # lexicographic; independent of input ordering
        k = plan.clusterCount
        if len(unique) < k:
            raise ValueError("fewer distinct feature vectors than clusters")
        # Deterministic farthest-first, lexicographic tie break. No random restarts.
        centers = [unique[0]]
        for _ in range(1, k):
            dist = ((unique[:, None, :] - np.asarray(centers)[None, :, :])**2).sum(axis=2).min(axis=1)
            centers.append(unique[int(dist.argmax())])
        centers = np.asarray(centers)
        previous = None
        for iteration in range(plan.maxIterations):
            labels = ((x[:, None, :] - centers[None, :, :])**2).sum(axis=2).argmin(axis=1)
            counts = np.bincount(labels, minlength=k)
            if (counts < plan.minClusterSamples).any():
                raise ValueError("undersized or empty cluster")
            if previous is not None and np.array_equal(previous, labels):
                break
            centers = np.asarray([x[labels == i].mean(axis=0) for i in range(k)])
            previous = labels
        else:
            raise ValueError("cluster iteration budget exhausted; no converged model")
        # IDs are lexicographic standardized-center order, never outcome-ranked.
        centers = np.asarray(sorted(centers.tolist()))
        model = FrozenModel(kind="kmeans", features=FEATURES, mean=mean.tolist(), scale=scale.tolist(),
                            centers=centers.tolist(), active=plan.activeClusters)
        labels = ((x[:, None, :] - centers[None, :, :])**2).sum(axis=2).argmin(axis=1)
        return model, [{"iterations": iteration + 1, "counts": np.bincount(labels, minlength=k).tolist(),
                        "jointFeatureCount": len(FEATURES), "clusterIds": "lexicographic_standardized_centers",
                        "interpretation": plan.interpretation}]
