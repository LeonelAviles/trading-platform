import { useEffect, useRef, useState } from 'react';
import { fetchChatStatus, sendChat } from '../api';

const STORAGE_KEY = 'chatMessages';
const WIDTH_KEY = 'chatWidth';
const MIN_WIDTH = 300;
const DEFAULT_WIDTH = 340;

function loadMessages() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
  } catch {
    return [];
  }
}

function loadWidth() {
  const w = Number(localStorage.getItem(WIDTH_KEY));
  return w >= MIN_WIDTH ? w : DEFAULT_WIDTH;
}

// The panel is docked on the right, so its width is the distance from the
// drag point to the right edge; cap it so the chart always keeps room.
function maxWidth() {
  return Math.max(MIN_WIDTH, Math.min(820, window.innerWidth - 420));
}

export default function ChatPanel({ symbol, interval, onClose }) {
  const [messages, setMessages] = useState(loadMessages);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState(null);
  const [width, setWidth] = useState(loadWidth);
  const [resizing, setResizing] = useState(false);
  const listRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    localStorage.setItem(WIDTH_KEY, String(width));
  }, [width]);

  // Suppress text selection / cursor flicker while dragging the resize handle.
  useEffect(() => {
    if (!resizing) return;
    const prevSelect = document.body.style.userSelect;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    return () => {
      document.body.style.userSelect = prevSelect;
      document.body.style.cursor = '';
    };
  }, [resizing]);

  function onResizeDown(e) {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setResizing(true);
  }
  function onResizeMove(e) {
    if (!resizing) return;
    setWidth(Math.min(Math.max(window.innerWidth - e.clientX, MIN_WIDTH), maxWidth()));
  }
  function onResizeUp(e) {
    setResizing(false);
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
  }

  useEffect(() => {
    fetchChatStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    // Autoscroll to the latest message.
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, pending]);

  async function send() {
    const text = input.trim();
    if (!text || pending) return;
    const userMsg = { id: crypto.randomUUID(), role: 'user', content: text };
    const history = [...messages, userMsg];
    setMessages(history);
    setInput('');
    setPending(true);
    try {
      // Only role/content go to the model; context carries app state so a
      // future LLM can be chart-aware.
      const wire = history.map((m) => ({ role: m.role, content: m.content }));
      const reply = await sendChat(wire, { symbol, interval });
      setMessages((m) => [...m, { id: crypto.randomUUID(), role: 'assistant', content: reply.content }]);
    } catch (e) {
      setMessages((m) => [...m, { id: crypto.randomUUID(), role: 'assistant', content: `Error: ${e.message}`, error: true }]);
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  }

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

  function clearChat() {
    setMessages([]);
  }

  return (
    <div className="chat-panel" style={{ width }}>
      <div
        className={`chat-resize ${resizing ? 'active' : ''}`}
        onPointerDown={onResizeDown}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeUp}
        title="Drag to resize"
      />
      <div className="chat-head">
        <h2>
          Assistant
          <span className={`chat-badge ${status?.connected ? 'on' : 'off'}`}>
            {status?.connected ? (status.model || 'connected') : 'no LLM yet'}
          </span>
        </h2>
        <div className="chat-head-actions">
          <button className="icon-btn" title="Clear chat" onClick={clearChat} disabled={!messages.length}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
          </button>
          <button className="icon-btn" title="Close" onClick={onClose}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
      </div>

      <div className="chat-messages" ref={listRef}>
        {messages.length === 0 && !pending && (
          <div className="chat-empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
            <p>Ask about the chart, a strategy, or a backtest.</p>
            <span>No model is connected yet — replies are placeholders.</span>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`chat-msg ${m.role}${m.error ? ' error' : ''}`}>{m.content}</div>
        ))}
        {pending && <div className="chat-typing"><span /><span /><span /></div>}
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
            placeholder="Ask the assistant"
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
