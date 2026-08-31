import { useEffect, useRef, useState } from 'react';
import { fetchChatStatus, streamChat } from '../api';
import { useResizable } from '../hooks/useResizable';

const MIN_WIDTH = 300;

// The panel is docked on the right, so its width is the distance from the
// drag point to the right edge; cap it so the chart always keeps room.
function maxWidth() {
  return Math.max(MIN_WIDTH, Math.min(820, window.innerWidth - 420));
}

// Friendly labels for backend tool calls shown while the assistant works.
// Keyed by agent_tools tool names (backend/agent_tools.py). Anything not
// listed falls back to `Running <name>`, so this map only needs to cover the
// tools whose raw name reads badly in the UI.
const TOOL_LABELS = {
  get_spec_schema: 'Reading the strategy schema',
  search_knowledge: 'Searching the knowledge graph',
  start_agent_run: 'Starting a background run',
  propose_risk_profile: 'Proposing a risk profile',
  evaluate_candidate: 'Evaluating against the risk profile',
  get_regime_breakdown: 'Reading regime breakdown',
  get_monte_carlo: 'Running Monte Carlo',
  create_strategy: 'Building the strategy',
  get_strategy: 'Reading the strategy',
  list_strategies: 'Reading strategies',
  propose_strategy_revision: 'Drafting a revision',
  run_backtest: 'Running the backtest (this can take a few minutes)',
  get_backtest: 'Reading backtest results',
  get_backtest_analytics: 'Crunching analytics',
  get_win_rate: 'Computing win rate',
  compare_backtests: 'Comparing backtests',
  get_trade_features: 'Reading trade context',
  compare_winners_vs_losers: 'Comparing winners vs losers',
  find_near_miss_entries: 'Looking for near-miss entries',
  log_finding: 'Saving a finding',
  get_findings: 'Reading saved findings',
};

// Opening line for the run this page is reviewing. Sent as a real user turn
// (the model needs it in history) but rendered as a note, since the trader
// didn't type it.
function reviewPrompt(jobId, strategyName) {
  return `I'm reviewing backtest ${jobId}${strategyName ? ` on "${strategyName}"` : ''} and looking at its trades on the chart. `
    + 'Take a look at how it did. If anything you\'d need to decide the next move is unclear, '
    + 'ask me before revising the strategy or running another backtest.';
}

function storageKey(backtestId) {
  return `chat:${backtestId}`;
}

function loadMessages(backtestId) {
  try {
    return JSON.parse(localStorage.getItem(storageKey(backtestId))) || [];
  } catch {
    return [];
  }
}

// Docked right of the chart — every conversation here is grounded in the
// backtest under review (see CandlestickPage, which is itself always a
// review of one run). History is kept per backtestId so switching reviews
// doesn't bleed one run's conversation into another's.
export default function ChatPanel({ symbol, interval, backtestId, strategyName, backtestStatus }) {
  const [messages, setMessages] = useState(() => loadMessages(backtestId));
  const [input, setInput] = useState('');
  const [pending, setPending] = useState(false);
  const [toolNote, setToolNote] = useState(null);
  const [status, setStatus] = useState(null);
  const panelRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const { size: width, resizing, bind } = useResizable({
    key: 'chatPanelWidth', defaultSize: 340, min: MIN_WIDTH, max: maxWidth,
  });
  const resizeHandlers = bind((e) => panelRef.current.getBoundingClientRect().right - e.clientX);

  useEffect(() => {
    setMessages(loadMessages(backtestId));
  }, [backtestId]);

  useEffect(() => {
    fetchChatStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    localStorage.setItem(storageKey(backtestId), JSON.stringify(messages));
    // Autoscroll to the latest message.
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [backtestId, messages, pending]);

  async function sendText(text, { seed = false } = {}) {
    if (!text || pending) return;
    const userMsg = { id: crypto.randomUUID(), role: 'user', content: text, seed };
    const history = [...messages, userMsg];
    setMessages(history);
    setPending(true);
    setToolNote(null);

    // Placeholder assistant bubble that fills in as the stream arrives.
    const replyId = crypto.randomUUID();
    const patchReply = (patch) => setMessages((m) => m.map(
      (msg) => (msg.id === replyId ? { ...msg, ...patch(msg) } : msg),
    ));

    try {
      // Only role/content go to the model; context carries app state so the
      // assistant is chart-aware.
      const wire = history.map((m) => ({ role: m.role, content: m.content }));
      setMessages((m) => [...m, { id: replyId, role: 'assistant', content: '' }]);
      await streamChat(wire, { symbol, interval, backtestId, strategyName, backtestStatus }, {
        onDelta: (delta) => {
          setToolNote(null);
          patchReply((msg) => ({ content: msg.content + delta }));
        },
        onTool: (name) => setToolNote(TOOL_LABELS[name] || `Running ${name}`),
        onError: (message) => patchReply((msg) => ({
          content: msg.content ? `${msg.content}\n\n${message}` : message,
          error: true,
        })),
      });
      // Drop the bubble entirely if nothing ever arrived.
      setMessages((m) => m.filter((msg) => msg.id !== replyId || msg.content));
    } catch (e) {
      setMessages((m) => m.filter((msg) => msg.id !== replyId || msg.content));
      setMessages((m) => [...m, { id: crypto.randomUUID(), role: 'assistant', content: `Error: ${e.message}`, error: true }]);
    } finally {
      setPending(false);
      setToolNote(null);
      inputRef.current?.focus();
    }
  }

  function send() {
    const text = input.trim();
    if (!text || pending) return;
    setInput('');
    sendText(text);
  }

  // The run this page reviews just finished (or was already done on arrival):
  // open the conversation instead of waiting to be asked. One auto-run per
  // backtest, ever — gated on a seed message already sitting in this job's
  // persisted history, not an in-memory flag, so reopening the panel or
  // reloading the page doesn't re-trigger it. Held until the status check
  // says there's an assistant to answer — greeting with an "ANTHROPIC_API_KEY
  // is not set" bubble is worse than staying quiet.
  useEffect(() => {
    if (!backtestId || backtestStatus !== 'done' || pending || !status?.connected) return;
    if (messages.some((m) => m.seed)) return;
    sendText(reviewPrompt(backtestId, strategyName), { seed: true });
    // sendText closes over the history it should send; re-running on every
    // message change would re-fire the greeting.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backtestId, backtestStatus, pending, status, strategyName]);

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function onInput(e) {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }

  return (
    <div ref={panelRef} className="chat-panel" style={{ width }}>
      <div className={`panel-resize left ${resizing ? 'active' : ''}`} {...resizeHandlers} />
      <div className="chat-messages" ref={listRef}>
        {messages.length === 0 && !pending && (
          <div className="chat-empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
            <p>Ask about the chart, this strategy, or its backtest.</p>
            <span>
              {status?.connected
                ? 'The assistant can read your market data, strategies, and backtest results.'
                : 'The assistant is currently offline.'}
            </span>
          </div>
        )}
        {messages.map((m) => (
          (m.content || m.role !== 'assistant') && (
            <div key={m.id} className={`chat-msg ${m.seed ? 'seed' : m.role}${m.error ? ' error' : ''}`}>{m.content}</div>
          )
        ))}
        {pending && toolNote && <div className="chat-tool-note">{toolNote}…</div>}
        {pending && !toolNote && <div className="chat-typing"><span /><span /><span /></div>}
      </div>

      <div className="chat-input-row">
        <div className="chat-input-box">
          <svg className="chat-input-spark" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M9 2.5l1.4 3.7 3.7 1.4-3.7 1.4L9 12.7 7.6 9 3.9 7.6l3.7-1.4z" />
            <path d="M17.5 12l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z" />
          </svg>
          <textarea
            ref={inputRef}
            className="chat-input" rows={1}
            placeholder="Ask Stratos"
            value={input}
            onChange={onInput}
            onKeyDown={onKeyDown}
          />
          <button className="chat-send" title="Send" onClick={send} disabled={!input.trim() || pending}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5M6 11l6-6 6 6" /></svg>
          </button>
        </div>
      </div>
    </div>
  );
}
