"""Deterministic, feature-only one-dimensional volume regime discovery.

Cluster ranks mean ascending training volume, never expected profitability.
The user chooses active ranks and their trading interpretation before fitting.
"""
from dataclasses import dataclass

import numpy as np


def boundaries(centers):
    # Stable midpoint formula for nonnegative volumes. Equality belongs to
    # the lower cluster, matching the exported <= / > expression exactly.
    centers = np.asarray(centers)
    return centers[:-1] + (centers[1:] - centers[:-1]) / 2


@dataclass(frozen=True)
class RegimeRule:
    centers: tuple[float, ...]
    active_clusters: tuple[int, ...]

    def assign(self, values):
        return np.searchsorted(boundaries(self.centers), np.asarray(values), side="left")

    def predict(self, values):
        return np.isin(self.assign(values), self.active_clusters)

    def expression(self):
        edges = boundaries(self.centers)
        regions = []
        for cluster in self.active_clusters:
            terms = []
            if cluster > 0:
                terms.append({"op": "gt", "args": [{"field": "volume"}, float(edges[cluster - 1])]})
            if cluster < len(self.centers) - 1:
                terms.append({"op": "lte", "args": [{"field": "volume"}, float(edges[cluster])]})
            regions.append(terms[0] if len(terms) == 1 else {"op": "and", "args": terms})
        return regions[0] if len(regions) == 1 else {"op": "or", "args": regions}

    def artifact(self):
        return {"centers": list(self.centers), "boundaries": boundaries(self.centers).tolist(),
                "activeClusters": list(self.active_clusters), "clusterRanking": "ascending_training_volume",
                "preprocessing": "raw_volume_no_scaler", "trigger": self.expression()}


class VolumeRegimes:
    requires_targets = False

    def fit(self, train, plan):
        # Fail if the orchestrator accidentally exposes outcomes to this method.
        if list(train.columns) != ["volume"]:
            raise ValueError("unsupervised fitting accepts only training volume features")
        values = train.volume.to_numpy(dtype=float)
        unique = np.unique(values)
        k = plan.clusterCount
        if len(unique) < k:
            raise ValueError("fewer distinct training volumes than clusterCount")
        # One deterministic initialization, evenly spaced ranks of unique
        # training values. No labels, random seed, restarts or model selection.
        centers = unique[np.linspace(0, len(unique) - 1, k).round().astype(int)]
        for iteration in range(1, plan.maxIterations + 1):
            assigned = np.searchsorted(boundaries(centers), values, side="left")
            counts = np.bincount(assigned, minlength=k)
            if (counts == 0).any():
                raise ValueError("empty training cluster; no automatic change of clusterCount")
            updated = np.array([values[assigned == i].mean() for i in range(k)])
            if not np.isfinite(updated).all() or (np.diff(updated) <= 0).any():
                raise ValueError("non-finite or collapsed training cluster centers")
            new_assigned = np.searchsorted(boundaries(updated), values, side="left")
            centers = updated
            if np.array_equal(assigned, new_assigned):
                break
        else:
            raise ValueError("clustering did not converge within the approved maxIterations")
        if (counts < plan.minClusterSamples).any():
            raise ValueError("training clusters do not meet the explicit minClusterSamples")
        distortion = float(np.mean((values - centers[assigned]) ** 2))
        if not np.isfinite(distortion):
            raise ValueError("non-finite training cluster distortion")
        rule = RegimeRule(tuple(float(c) for c in centers), tuple(sorted(plan.activeClusters)))
        diagnostics = {"kind": "feature_only_clustering", "iterations": iteration,
                       "trainingMeanSquaredDistance": distortion,
                       "clusters": [{"rank": i, "centerVolume": float(centers[i]), "samples": int(counts[i])}
                                    for i in range(k)],
                       "interpretation": plan.interpretation,
                       "outcomeRanking": "none; active cluster ranks supplied by user before fitting"}
        return rule, [diagnostics]
