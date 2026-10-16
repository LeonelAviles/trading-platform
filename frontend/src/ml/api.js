export async function request(path, body, signal) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const value = await response.json();
  if (!response.ok) {
    const detail = typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail || value);
    throw new Error(detail);
  }
  return value;
}

export const discoveriesPath = (thread) => `/api/agent/threads/${encodeURIComponent(thread)}/ml-discoveries`;
export const experimentsPath = (thread) => `/api/agent/threads/${encodeURIComponent(thread)}/ml-experiments`;
