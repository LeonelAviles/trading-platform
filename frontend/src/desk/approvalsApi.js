// Optional contract supplied by backend PR #1; no legacy execution fallback.
const root = (threadId) =>
  `/api/agent/threads/${encodeURIComponent(threadId)}/proposals`;
async function request(url, options) {
  const response = await fetch(url, options);
  let value;
  try {
    value = await response.json();
  } catch {
    /* report HTTP failure below */
  }
  if (!response.ok) {
    const error = new Error(
      typeof value?.detail === "string"
        ? value.detail
        : `Research service returned HTTP ${response.status}`,
    );
    error.status = response.status;
    throw error;
  }
  return value;
}
export const fetchProposals = (threadId, offset = 0) =>
  request(`${root(threadId)}?limit=5&offset=${offset}`);
export const fetchProposal = (threadId, id) =>
  request(`${root(threadId)}/${encodeURIComponent(id)}`);
export const decideProposal = (threadId, id, decision) =>
  request(`${root(threadId)}/${encodeURIComponent(id)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });
export const queueProposal = (threadId, id) =>
  request(`${root(threadId)}/${encodeURIComponent(id)}/queue`, {
    method: "POST",
  });
export const fetchTradeEvidence = (jobId, offset = 0) =>
  request(
    `/api/agent/evidence/${encodeURIComponent(jobId)}/trades?offset=${offset}&limit=50`,
  );
