import { useState } from "react";
import { discoveriesPath, request } from "./api";

export default function AssistantReview({ thread, row, disabled, onRecorded }) {
  const [context, setContext] = useState(null);
  const [text, setText] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function exportContext() {
    setBusy(true); setError("");
    try { setContext(await request(`${discoveriesPath(thread)}/${row.id}/review-context?digest=${row.digest}`)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function record(e) {
    e.preventDefault(); setError("");
    let parsed;
    try { parsed = JSON.parse(text); } catch { setError("Enter a valid ClusterReview JSON document."); return; }
    setBusy(true);
    try {
      await request(`${discoveriesPath(thread)}/${row.id}/assistant-review`, {
        digest: row.digest, inputDigest: context.inputDigest, reviewer, review: parsed,
      });
      onRecorded();
    } catch (e) { setError(`${e.message}. Reload recorded state before retrying.`); }
    finally { setBusy(false); }
  }
  return <details><summary>Use this conversation's assistant for review</summary>
    <p>Export training-feature context to the assistant, then record its structured review here. No API key is needed. Attribution is supplied by you; this does not approve or run a hypothesis.</p>
    <button disabled={disabled || busy} onClick={exportContext}>Export training-only review context</button>
    {context && <><pre>{JSON.stringify(context, null, 2)}</pre><form onSubmit={record}>
      <label className="ml-field">Reviewer attribution<input required maxLength="200" value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label>
      <label className="ml-field">Assistant ClusterReview JSON<textarea required rows="12" value={text} onChange={(e) => setText(e.target.value)} /></label>
      <button disabled={disabled || busy}>Record one assistant review</button></form></>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
