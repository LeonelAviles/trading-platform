import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { ResearchSelectionContext } from "../researchSelection";

const KEY = "stratos.research.selection";
function normalize(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) value = {};
  return Object.fromEntries(
    ["strategyId", "runId", "name"].map((key) => [
      key,
      typeof value?.[key] === "string" &&
      value[key].length <= (key === "name" ? 1000 : 200)
        ? value[key]
        : null,
    ]),
  );
}
function readSelection() {
  try {
    return normalize(JSON.parse(sessionStorage.getItem(KEY)));
  } catch {
    return {};
  }
}

export default function ResearchSelection({ children }) {
  const [selection, setSelection] = useState(readSelection);
  const { pathname, search } = useLocation();
  const select = useCallback(
    (next) =>
      setSelection((prev) => {
        const value = normalize(next);
        return Object.keys(value).every((key) => value[key] === prev[key])
          ? prev
          : value;
      }),
    [],
  );
  useEffect(() => {
    const params = new URLSearchParams(search);
    const encodedId = /^\/strategies\/([^/]+)$/.exec(pathname)?.[1];
    let strategyId = params.get("strategy");
    if (encodedId) {
      try {
        strategyId = decodeURIComponent(encodedId);
      } catch {
        return;
      }
    }
    if (!strategyId) return;
    setSelection((prev) => ({
      strategyId,
      runId:
        params.get("run") ||
        (prev.strategyId === strategyId ? prev.runId : null),
      name: prev.strategyId === strategyId ? prev.name : null,
    }));
  }, [pathname, search]);
  useEffect(() => {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(selection));
    } catch {
      /* storage is optional */
    }
  }, [selection]);
  const value = useMemo(() => ({ selection, select }), [selection, select]);
  return (
    <ResearchSelectionContext.Provider value={value}>
      {children}
    </ResearchSelectionContext.Provider>
  );
}
