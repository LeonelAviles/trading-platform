import { useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { createAgentThread, fetchAgentThread, streamAgentMessage } from '../api';
import { HeaderSlotContext } from '../headerSlot';
import AgentMessageBody from '../components/AgentMessageBody';

export const THREAD_KEY = 'stratos.agent.thread';

export function StratosMark() {
  return <span className="agent-avatar" aria-hidden="true">S</span>;
}

export function Message({ message }) {
  const isUser = message.role === 'user';
  return (
    <article className={`agent-message ${message.role}`}>
      {!isUser && <StratosMark />}
      <div className="agent-message-content">
        {!isUser && <div className="agent-message-role">Stratos <span>Research agent</span></div>}
        <AgentMessageBody text={message.content} />
        {message.citations?.length > 0 && (
          <div className="agent-citations">
            <div className="agent-citations-title">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5v-16Z" /><path d="M4 5.5v16M8 7h8M8 11h6" /></svg>
              Sources <span>{message.citations.length}</span>
            </div>
            <div className="agent-citation-list">
              {message.citations.map((c) => (
                <a key={c.url} href={c.url} target="_blank" rel="noreferrer">
                  <span className="agent-citation-heading">{c.heading || c.path}</span>
                  <span className="agent-citation-path">{c.source} / {c.path}</span>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17 17 7M8 7h9v9" /></svg>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

export function Thinking({ activity }) {
  return (
    <div className="agent-thinking">
      <StratosMark />
      <div>
        <div className="agent-message-role">Stratos <span>Research agent</span></div>
        <div className="agent-thinking-state">
          <span className="agent-thinking-dots"><i /><i /><i /></span>
          {activity}
        </div>
      </div>
    </div>
  );
}

export default function AgentPage() {
  const { leading: leadingSlot, trailing: trailingSlot } = useContext(HeaderSlotContext);
  const [thread, setThread] = useState(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [activity, setActivity] = useState('Starting…');
  const [error, setError] = useState('');
  const bottom = useRef(null);
  const composer = useRef(null);
  const handoff = useRef('');
  const navigate = useNavigate();

  async function newThread() {
    const created = await createAgentThread();
    localStorage.setItem(THREAD_KEY, created.id);
    setThread(created);
    setError('');
  }

  useEffect(() => {
    const id = localStorage.getItem(THREAD_KEY);
    (id ? fetchAgentThread(id) : createAgentThread())
      .then((value) => { localStorage.setItem(THREAD_KEY, value.id); setThread(value); })
      .catch(() => newThread().catch((e) => setError(e.message)));
  }, []);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [thread?.messages, sending]);

  useEffect(() => {
    if (!composer.current) return;
    composer.current.style.height = 'auto';
    composer.current.style.height = `${Math.min(composer.current.scrollHeight, 180)}px`;
  }, [input]);

  async function send(event) {
    event?.preventDefault();
    const message = input.trim();
    if (!message || !thread || sending) return;
    setInput(''); setError(''); setSending(true); setActivity('Starting…');
    const nonce = Date.now();
    const optimistic = { id: `local-${nonce}`, role: 'user', content: message, citations: [] };
    const draftId = `stream-${nonce}`;
    setThread((t) => ({ ...t, messages: [...(t.messages || []), optimistic] }));
    try {
      await streamAgentMessage(thread.id, message, (event) => {
        if (event.type === 'status') setActivity(event.message);
        if (event.type === 'delta') {
          setActivity('Writing response…');
          setThread((t) => {
            const messages = [...(t.messages || [])];
            const index = messages.findIndex((item) => item.id === draftId);
            if (index === -1) {
              messages.push({ id: draftId, role: 'assistant', content: event.delta, citations: [] });
            } else {
              messages[index] = { ...messages[index], content: messages[index].content + event.delta };
            }
            return { ...t, messages };
          });
        }
        if (event.type === 'done') {
          setThread((t) => {
            const messages = [...(t.messages || [])];
            const index = messages.findIndex((item) => item.id === draftId);
            if (index === -1) messages.push(event.message);
            else messages[index] = event.message;
            return { ...t, messages };
          });
        }
        if (event.type === 'workflow' && event.navigateTo) handoff.current = event.navigateTo;
      });
      if (handoff.current) {
        const target = handoff.current;
        handoff.current = '';
        navigate(target);
      }
    } catch (e) {
      setThread((t) => ({ ...t, messages: (t.messages || []).filter((item) => item.id !== draftId) }));
      setError(e.message);
    } finally {
      setSending(false);
    }
  }

  function onKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); }
  }

  return (
    <div className="page agent-page">
      {leadingSlot && createPortal(<div className="hdr-title">Stratos Research</div>, leadingSlot)}
      {trailingSlot && createPortal(<button className="btn" onClick={newThread}>New chat</button>, trailingSlot)}
      <main className="agent-chat">
        <div className="agent-messages">
          {thread?.messages?.length ? thread.messages.map((m) => <Message key={m.id} message={m} />) : (
            <div className="agent-welcome">
              <div className="agent-mark">S</div>
              <div className="agent-welcome-kicker">Stratos Research</div>
              <h1>What are we testing?</h1>
              <p>Describe an idea in plain English. I’ll research it, turn it into a valid strategy, and keep theory separate from backtest evidence.</p>
              <div className="agent-starters">
                <button className="agent-starter" onClick={() => setInput('Find a defensible ES intraday strategy, create it, and validate it.')}>Find and test an ES strategy <span>→</span></button>
                <button className="agent-starter" onClick={() => setInput('Explain how I should validate an ES opening-range breakout strategy.')}>Review a strategy idea <span>→</span></button>
              </div>
            </div>
          )}
          {sending && <Thinking activity={activity} />}
          <div ref={bottom} />
        </div>
        <div className="agent-composer-area">
          {error && <div className="agent-error">{error}</div>}
          <form className="agent-composer" onSubmit={send}>
            <textarea ref={composer} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown} placeholder="Ask Stratos to research, create, or validate…" rows="1" aria-label="Message Stratos" />
            <button className="agent-send" disabled={!input.trim() || sending} aria-label="Send message" title="Send message">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 7-7 7 7M12 19V5" /></svg>
            </button>
          </form>
          <div className="agent-composer-hint">Enter to send · Shift + Enter for a new line</div>
        </div>
      </main>
    </div>
  );
}
