export const FEATURE_LABELS = {
  spread_ticks: "Spread (ticks)",
  depth_imbalance: "Depth imbalance",
  aggressive_buy_rate: "Aggressive buying (contracts/s)",
  aggressive_sell_rate: "Aggressive selling (contracts/s)",
  add_rate: "Order additions/s",
  cancel_reduction_rate: "Order reductions (contracts/s)",
};

export function datasetGroups(partitions = []) {
  const groups = new Map();
  for (const partition of partitions) {
    const identity = partition?.document?.identity;
    if (!identity?.settings?.contract || !identity.source?.day || !identity.extractor) continue;
    const { instrumentId, publisherId, channelId } = identity.source;
    const key = JSON.stringify([identity.settings, identity.extractor, identity.tickSize, instrumentId, publisherId, channelId]);
    if (!groups.has(key)) groups.set(key, { key, settings: identity.settings, sources: [], duplicate: false });
    const group = groups.get(key);
    if (group.sources.some((s) => s.day === identity.source.day)) group.duplicate = true;
    group.sources.push(identity.source);
  }
  return [...groups.values()].map((group) => ({ ...group, sources: group.sources.sort((a, b) => a.day.localeCompare(b.day)) }));
}

export function discoveryPlan(group, fields) {
  if (!group || group.duplicate) throw new Error("Select an unambiguous prepared feature dataset.");
  if (!(fields.start <= fields.trainEnd && fields.trainEnd < fields.validationEnd && fields.validationEnd < fields.testEnd))
    throw new Error("Choose ordered discovery, validation and test periods.");
  const sources = group.sources.filter((s) => s.day >= fields.start && s.day <= fields.testEnd);
  if (sources.length < 3 || [fields.start, fields.trainEnd, fields.validationEnd, fields.testEnd].some((d) => !sources.some((s) => s.day === d)))
    throw new Error("Every partition boundary must be an available session in this dataset.");
  return { ...group.settings, ...fields, sources, method: "mbo_cluster_discovery_v1", symbol: "ES1!", interval: "1min",
    dataSource: "prepared_MBO_features", contractSelection: "fixed_contract_before_experiment",
    clusterCount: Number(fields.clusterCount), maxIterations: Number(fields.maxIterations), minClusterSamples: Number(fields.minClusterSamples) };
}

export function verifyRecord(row, thread) {
  if (!row || row.threadId !== thread || !/^[a-f0-9]{12}$/.test(row.id) || !row.document?.plan ||
      typeof row.status !== "string" || !/^[a-f0-9]{64}$/.test(row.digest))
    throw new Error("Recorded plan is incomplete. Reload before taking an action.");
  return row;
}

export function hypothesisBody(row, fields) {
  const suggestion = row.review?.document?.review?.suggestion;
  const hypothesis = {
    hypothesis: fields.hypothesis.trim(), activeClusters: fields.activeClusters.map(Number).sort((a, b) => a - b),
    direction: fields.direction, horizonBars: Number(fields.horizonBars), interpretation: fields.interpretation.trim(),
    rationale: fields.rationale.trim(), falsification: fields.falsification.trim(),
  };
  const same = suggestion && Object.keys(hypothesis).every((key) =>
    JSON.stringify(hypothesis[key]) === JSON.stringify(key === "activeClusters" ? [...suggestion[key]].sort((a, b) => a - b) : suggestion[key]));
  for (const key of ["roundTripCostTicks", "minEvaluationSignals", "minMeanNetTicks", "minImprovementOverBaselineTicks"])
    if (fields[key] === "" || !Number.isFinite(Number(fields[key]))) throw new Error("Enter all costs and evaluation criteria explicitly.");
  return { ...hypothesis, discoveryDigest: row.digest, reviewDigest: row.review.digest,
    reviewDisposition: same ? "accept" : "revise", reviewReason: fields.reviewReason.trim(),
    target: "next_open_to_horizon_close_ticks", roundTripCostTicks: Number(fields.roundTripCostTicks),
    minEvaluationSignals: Number(fields.minEvaluationSignals), minMeanNetTicks: Number(fields.minMeanNetTicks),
    minImprovementOverBaselineTicks: Number(fields.minImprovementOverBaselineTicks),
    multipleTestingPolicy: "descriptive_only_no_significance_claim" };
}
