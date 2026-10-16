import { useEffect, useState } from "react";
import { discoveriesPath, request } from "./api";

export default function TrainingIteration({ thread, row }) {
  const [context, setContext] = useState(null);
  const [history, setHistory] = useState([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const path = `${discoveriesPath(thread)}/${row.id}`;
  useEffect(() => {
    const controller = new AbortController();
    request(`${path}/next-ideas`, undefined, controller.signal).then((r) => setHistory(r.ideas))
      .catch((e) => { if (e.name !== "AbortError") setError(e.message); });
    return () => controller.abort();
  }, [path]);
  async function exportContext() {
    setBusy(true); setError("");
    try { setContext(await request(`${path}/next-idea-context`)); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function record(e) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const idea = JSON.parse(text);
      const result = await request(`${path}/next-ideas`, { ...idea, discoveryDigest: row.digest,
        contextDigest: context.digest, previousIdeaDigest: history.at(-1)?.digest || null });
      setHistory((old) => [...old, result]); setText("");
    } catch (e) { setError(`${e.message}. Reload state before retrying.`); }
    finally { setBusy(false); }
  }
  return <section className="ml-panel"><h2>Next training-design iteration</h2>
    <p>Record a falsifiable next idea or revise its design. These records have zero fit, evaluation and execution budget. Previously consumed heldout dates stay consumed.</p>
    {history.map((item) => <article key={item.digest}><h3>Idea {item.document.iteration} · not evaluated</h3>
      <p>{item.document.hypothesis}</p><p>{item.document.rationale}</p><p>{item.document.notEvaluatedReason}</p>
      <details><summary>Training context, design changes and immutable chain</summary><pre>{JSON.stringify(item, null, 2)}</pre></details></article>)}
    <details><summary>Record or revise a training-only next idea (maximum five)</summary>
      <button disabled={busy || history.length >= 5} onClick={exportContext}>Export next-idea training context</button>
      {context && <><p>{context.document.blinding}</p><pre>{JSON.stringify(context, null, 2)}</pre><form onSubmit={record}>
        <label className="ml-field">Next idea JSON<textarea required rows="10" value={text} onChange={(e) => setText(e.target.value)} placeholder={'{"hypothesis":"...","rationale":"...","trainingOnlyChanges":["..."],"falsification":"...","notEvaluatedReason":"Budget exhausted","author":"..."}'} /></label>
        <button disabled={busy || history.length >= 5}>Record unevaluated idea</button></form></>}
    </details>{error && <p role="alert">{error}</p>}
  </section>;
}
