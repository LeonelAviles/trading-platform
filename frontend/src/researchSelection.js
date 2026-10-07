import { createContext, useContext } from "react";

export const ResearchSelectionContext = createContext({
  selection: {},
  select: () => {},
});
export const useResearchSelection = () => useContext(ResearchSelectionContext);

export function researchHref(path, selection) {
  const params = new URLSearchParams();
  if (selection?.strategyId) params.set("strategy", selection.strategyId);
  if (selection?.runId) params.set("run", selection.runId);
  return `${path}${params.size ? `?${params}` : ""}`;
}
