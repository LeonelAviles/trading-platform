import { describe, expect, it } from "vitest";
import { datasetGroups, discoveryPlan, hypothesisBody, verifyRecord } from "./model";

const sources = [13, 14, 15].map((day) => ({ day: `2026-07-${day}`, instrumentId: 10, publisherId: 1, channelId: 0 }));
const settings = { contract: "ESU6", depthLevels: 2, featureWindowSeconds: 60, maxBookAgeMs: 120000, maxEventLagMs: 120000, maxUnknownTradeFraction: 0 };
const partitions = sources.map((source) => ({ document: { identity: { settings, source, extractor: { code: "v1" }, tickSize: 0.25 } } }));

describe("discovery workflow boundaries", () => {
  it("keeps different contracts, policies and source mappings separate", () => {
    const other = structuredClone(partitions);
    other[0].document.identity.settings.contract = "ESZ6";
    expect(datasetGroups([...partitions, ...other])).toHaveLength(2);
    const ambiguous = datasetGroups([...partitions, partitions[0]]);
    expect(ambiguous[0].duplicate).toBe(true);
    expect(() => discoveryPlan(ambiguous[0], {})).toThrow("unambiguous");
  });

  it("does not add active clusters or trading choices before discovery", () => {
    const plan = discoveryPlan(datasetGroups(partitions)[0], { researchQuestion: "What patterns recur?", start: sources[0].day,
      trainEnd: sources[0].day, validationEnd: sources[1].day, testEnd: sources[2].day,
      clusterCount: "2", maxIterations: "50", minClusterSamples: "10" });
    expect(plan.sources).toEqual(sources);
    expect(plan).not.toHaveProperty("activeClusters");
    expect(plan).not.toHaveProperty("direction");
    expect(plan.clusterCount).toBe(2);
  });

  it("rejects stale or incomplete recorded plans before actions", () => {
    expect(() => verifyRecord({ id: "aaaaaaaaaaaa", threadId: "different" }, "thread")).toThrow();
    expect(() => verifyRecord({ id: "../escape", threadId: "thread", digest: "a".repeat(64), document: { plan: {} }, status: "proposed" }, "thread")).toThrow();
  });

  it("requires explicit economics and records revisions to AI suggestions", () => {
    const suggestion = { hypothesis: "Test group one", activeClusters: [1], direction: "long", horizonBars: 5,
      interpretation: "Group one", rationale: "Feature behavior", falsification: "No later improvement" };
    const row = { digest: "a".repeat(64), review: { digest: "b".repeat(64), document: { review: { suggestion } } } };
    const fields = { ...suggestion, reviewReason: "Reviewed", roundTripCostTicks: "1.5", minEvaluationSignals: "100",
      minMeanNetTicks: "0.2", minImprovementOverBaselineTicks: "0.1" };
    expect(hypothesisBody(row, fields).reviewDisposition).toBe("accept");
    expect(hypothesisBody(row, { ...fields, direction: "short" }).reviewDisposition).toBe("revise");
    expect(() => hypothesisBody(row, { ...fields, roundTripCostTicks: "" })).toThrow("explicitly");
  });
});
