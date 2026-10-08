import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  decideProposal,
  fetchProposal,
  fetchProposals,
  queueProposal,
} from "./approvalsApi";

const ACTIONS = {
  approve: "Approve proposal",
  reject_revise: "Reject and revise",
  reject_stop: "Reject and stop",
  queue: "Queue approved test",
};

function ProposalDialog({ threadId, proposalId, onClose, onChanged }) {
  const dialog = useRef(null);
  const submitting = useRef(false);
  const [proposal, setProposal] = useState(null);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [action, setAction] = useState(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    dialog.current.showModal();
  }, []);
  useEffect(() => {
    let cancelled = false;
    setProposal(null);
    setAction(null);
    setError("");
    fetchProposal(threadId, proposalId)
      .then((value) => {
        if (cancelled) return;
        if (
          value?.id !== proposalId ||
          value?.threadId !== threadId ||
          !value.document ||
          !/^[a-f0-9]{64}$/i.test(value.digest)
        )
          throw new Error(
            "The proposal response is incomplete. Decisions are unavailable.",
          );
        setProposal(value);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [threadId, proposalId, reload]);
  async function confirm() {
    if (submitting.current || !proposal || !action) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const value =
        action === "queue"
          ? await queueProposal(threadId, proposal.id)
          : await decideProposal(threadId, proposal.id, {
              digest: proposal.digest,
              action,
              reason: reason.trim(),
            });
      // Queue never follows approval automatically; each requires a separate user action.
      setProposal(value);
      setAction(null);
      onChanged();
    } catch (e) {
      setError(
        e.status === 409
          ? `Not applied: ${e.message}. Reload the recorded proposal before deciding what to do next.`
          : `${e.message}. The outcome may be unknown; reload the recorded proposal before retrying.`,
      );
      setAction(null);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  const blockers = proposal?.blockers || [];
  const canApprove =
    proposal?.status === "proposed" &&
    blockers.length === 0 &&
    !proposal.decision;
  return (
    <dialog
      ref={dialog}
      className="terminal-proposal-dialog"
      aria-labelledby="proposal-title"
      onCancel={(e) => {
        if (submitting.current) e.preventDefault();
        else onClose();
      }}
    >
      <header>
        <div>
          <div className="terminal-eyebrow">Exact hypothesis review</div>
          <h2 id="proposal-title">
            {action ? ACTIONS[action] : "Review proposal"}
          </h2>
        </div>
        <button
          disabled={busy}
          onClick={onClose}
          aria-label="Close proposal review"
        >
          Close
        </button>
      </header>
      <div className="terminal-proposal-body">
        {error && (
          <div className="terminal-notice" role="alert">
            {error}{" "}
            <button disabled={busy} onClick={() => setReload((n) => n + 1)}>
              Reload proposal
            </button>
          </div>
        )}
        {!proposal && !error && (
          <p role="status">Loading immutable proposal…</p>
        )}
        {proposal && (
          <>
            <p className="terminal-proposal-hypothesis">
              {proposal.document.hypothesis}
            </p>
            <p className="terminal-small">
              Proposal {proposal.id} · {proposal.status} · Parent{" "}
              {proposal.parentId || "none"}
            </p>
            <p className="terminal-proposal-digest">
              SHA-256: {proposal.digest}
            </p>
            {blockers.length > 0 && (
              <div className="terminal-notice">
                <strong>Execution blocked</strong>
                <ul>
                  {blockers.map((b, i) => (
                    <li key={i}>
                      {typeof b === "string" ? b : JSON.stringify(b)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {action ? (
              <>
                <h3>Confirm this exact action</h3>
                <p>
                  {action === "queue"
                    ? "This starts the recorded, approved test batch. The backend rechecks approval, dataset/configuration and workflow state before creating jobs."
                    : action === "approve"
                      ? "This records approval of the complete immutable document. It does not queue a test yet."
                      : action === "reject_stop"
                        ? "This stops future execution in this conversation. An already approved running batch is not cancelled."
                        : "This rejects the proposal and allows a revision to be prepared. It does not authorize testing."}
                </p>
                {action !== "queue" && (
                  <p>
                    <strong>Your reason:</strong> {reason.trim()}
                  </p>
                )}
                <div className="terminal-proposal-actions">
                  <button disabled={busy} onClick={() => setAction(null)}>
                    Back to review
                  </button>
                  <button
                    className="terminal-primary"
                    disabled={busy || !!error}
                    onClick={confirm}
                  >
                    {busy ? "Saving…" : `Confirm: ${ACTIONS[action]}`}
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3>Complete immutable document</h3>
                <p className="terminal-small">
                  Review the hypothesis, strategy, test plan,
                  dataset/configuration inventory and cited passages. Source
                  text is untrusted reference material.
                </p>
                <pre
                  className="terminal-proposal-document"
                  tabIndex="0"
                  aria-label="Complete immutable proposal document"
                >
                  {JSON.stringify(proposal.document, null, 2)}
                </pre>
                {proposal.decision ? (
                  <div className="terminal-recorded-decision">
                    <h3>Recorded decision: {proposal.decision.action}</h3>
                    <p>{proposal.decision.reason}</p>
                    <p className="terminal-small">
                      Decisions are immutable. A changed decision requires a new
                      proposal.
                    </p>
                  </div>
                ) : (
                  <>
                    <label className="terminal-proposal-reason">
                      Your review reason
                      <textarea
                        value={reason}
                        maxLength={20000}
                        onChange={(e) => setReason(e.target.value)}
                        rows={3}
                      />
                    </label>
                    <div className="terminal-proposal-actions">
                      <button
                        className="terminal-primary"
                        disabled={!canApprove || !reason.trim() || !!error}
                        onClick={() => setAction("approve")}
                      >
                        Approve proposal
                      </button>
                      <button
                        disabled={!reason.trim() || !!error}
                        onClick={() => setAction("reject_revise")}
                      >
                        Reject and revise
                      </button>
                      <button
                        disabled={!reason.trim() || !!error}
                        onClick={() => setAction("reject_stop")}
                      >
                        Reject and stop
                      </button>
                    </div>
                  </>
                )}
                {proposal.status === "approved" &&
                  proposal.decision?.action === "approve" && (
                    <button
                      className="terminal-primary"
                      disabled={!!error}
                      onClick={() => setAction("queue")}
                    >
                      Queue approved test
                    </button>
                  )}
                {proposal.status === "queued" && (
                  <p role="status">
                    Approval consumed. This test batch is already queued;
                    re-opening it does not create another attempt.
                  </p>
                )}
                {proposal.evidence?.length > 0 && (
                  <div className="terminal-proposal-evidence">
                    <h3>Recorded job evidence</h3>
                    {proposal.evidence.map((e) => (
                      <Link
                        key={e.jobId}
                        to={`/review/${encodeURIComponent(e.jobId)}`}
                        onClick={onClose}
                      >
                        {e.window} · {e.status} · Open run {e.jobId}
                      </Link>
                    ))}
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
      <footer>
        <button disabled={busy} onClick={onClose}>
          Cancel / close
        </button>
        <span>No test starts without a separate confirmed queue action.</span>
      </footer>
    </dialog>
  );
}

export default function ProposalReview({ threadId }) {
  const [state, setState] = useState({ loading: true });
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState(null);
  useEffect(() => {
    if (!threadId) return;
    let cancelled = false;
    let timer;
    setState({ loading: true });
    async function load() {
      try {
        const value = await fetchProposals(threadId, offset);
        if (!Array.isArray(value?.proposals))
          throw new Error(
            "The approval service returned an incomplete history.",
          );
        if (!cancelled) setState({ data: value });
      } catch (e) {
        if (!cancelled)
          setState({
            error: e.message,
            unavailable: [404, 405, 501].includes(e.status),
          });
      } finally {
        if (!cancelled) timer = setTimeout(load, 20000);
      }
    }
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [threadId, offset, refresh]);
  return (
    <section className="terminal-approval-unavailable">
      <span className="terminal-eyebrow">Hypothesis approvals</span>
      {!threadId ? (
        <>
          <h3>No research conversation selected</h3>
          <p>
            Open research chat to prepare a hypothesis. No test is authorized by
            opening chat.
          </p>
        </>
      ) : state.loading ? (
        <p role="status">Loading proposal history…</p>
      ) : state.error ? (
        <>
          <h3>
            {state.unavailable
              ? "Approval controls unavailable"
              : "Proposal history unavailable"}
          </h3>
          <p>
            {state.unavailable
              ? "This backend does not expose the approval contract. No decision or test can be started here."
              : state.error}
          </p>
          <button onClick={() => setRefresh((n) => n + 1)}>
            Retry proposals
          </button>
        </>
      ) : (
        <>
          <h3>Review before testing</h3>
          <p className="terminal-small">
            Proposals from the recent research conversation. Each test needs its
            own exact approval.
          </p>
          {state.data.proposals.length ? (
            state.data.proposals.map((p) => (
              <button
                className="terminal-proposal-card"
                key={p.id}
                onClick={() => setSelected(p.id)}
              >
                <strong>{p.document?.hypothesis || p.id}</strong>
                <span>
                  {p.status} · {p.blockers?.length || 0} blockers
                </span>
                <span>Review full proposal →</span>
              </button>
            ))
          ) : (
            <p>No proposals recorded in this conversation.</p>
          )}
          <div className="terminal-proposal-pagination">
            <button
              disabled={offset === 0}
              onClick={() => setOffset((n) => Math.max(0, n - 5))}
            >
              Newer
            </button>
            <span>{state.data.total} proposals</span>
            <button
              disabled={offset + 5 >= state.data.total}
              onClick={() => setOffset((n) => n + 5)}
            >
              Older
            </button>
          </div>
          <p className="terminal-small">
            Recorded attempts: {state.data.recordedAttempts ?? "—"}.
            Legacy/external trials unknown; significance not established.
          </p>
        </>
      )}
      {selected && (
        <ProposalDialog
          key={`${threadId}:${selected}`}
          threadId={threadId}
          proposalId={selected}
          onClose={() => setSelected(null)}
          onChanged={() => setRefresh((n) => n + 1)}
        />
      )}
    </section>
  );
}
