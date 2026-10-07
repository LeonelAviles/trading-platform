import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchAgentThread, fetchAgentWorkflow, streamAgentMessage } from '../api';
import { useResizable } from '../hooks/useResizable';
import { Message, Thinking } from '../pages/AgentPage';

function WorkflowTimeline({ workflow }) {
  const [expanded, setExpanded] = useState(false);
  if (!workflow?.events?.length) return null;
  const events = workflow.events.slice(-5);
  const latest = events[events.length - 1];
  return (
    <section className={`chart-agent-workflow ${expanded ? 'expanded' : 'collapsed'}`}>
      <button className="chart-agent-workflow-head" type="button" aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}>
        <span className="chart-agent-workflow-label">
          <span>Research loop</span>
          {!expanded && <small>{latest.title}</small>}
        </span>
        <span className="chart-agent-workflow-meta">
          <b>{workflow.changeCount}/{workflow.maxChanges}</b>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
        </span>
      </button>
      {expanded && (
        <div className="chart-agent-events">
          {events.map((event, index) => (
            <div className={`chart-agent-event ${event.type}`} key={`${event.at}-${index}`}>
              <i />
              <div><strong>{event.title}</strong>{event.detail && <span title={event.detail}>{event.detail}</span>}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export default function ChartAgentPanel({ threadId, backtestId, open, onClose }) {
  const [thread, setThread] = useState(null);
  const [workflow, setWorkflow] = useState(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [activity, setActivity] = useState('Starting…');
  const [error, setError] = useState('');
  const bottom = useRef(null);
  const panelRef = useRef(null);
  const navigate = useNavigate();
  const { size: width, resizing, bind } = useResizable({
    key: 'chartAgentWidth', defaultSize: 420, min: 300, max: () => Math.min(680, window.innerWidth * 0.6),
  });
  const resizeHandlers = bind((e) => panelRef.current.getBoundingClientRect().right - e.clientX);

  useEffect(() => {
    if (!threadId || !open) return undefined;
    let stopped = false;
    async function refresh() {
      try {
        const [nextThread, nextWorkflow] = await Promise.all([
          fetchAgentThread(threadId), fetchAgentWorkflow(threadId),
        ]);
        if (stopped) return;
        setThread(nextThread);
        setWorkflow(nextWorkflow?.id ? nextWorkflow : null);
        if (nextWorkflow?.chartJobId && nextWorkflow.chartJobId !== backtestId
            && ['running', 'analyzing'].includes(nextWorkflow.status)) {
          navigate(`/review/${nextWorkflow.chartJobId}?thread=${threadId}&run=${nextWorkflow.id}&chat=1`, { replace: true });
        }
      } catch (e) { if (!stopped) setError(e.message); }
    }
    refresh();
    const timer = setInterval(refresh, 2500);
    return () => { stopped = true; clearInterval(timer); };
  }, [threadId, backtestId, open, navigate]);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [thread?.messages, workflow?.events, sending]);

  async function send(event) {
    event.preventDefault();
    const message = input.trim();
    if (!message || sending || !threadId) return;
    setInput(''); setError(''); setSending(true); setActivity('Starting…');
    const nonce = Date.now();
    const draftId = `stream-${nonce}`;
    setThread((value) => ({ ...(value || { id: threadId }), messages: [...(value?.messages || []),
      { id: `local-${nonce}`, role: 'user', content: message, citations: [] }] }));
    try {
      await streamAgentMessage(threadId, message, (streamEvent) => {
        if (streamEvent.type === 'status') setActivity(streamEvent.message);
        if (streamEvent.type === 'workflow') setWorkflow(streamEvent.workflow);
        if (streamEvent.type === 'delta') {
          setActivity('Writing response…');
          setThread((value) => {
            const messages = [...(value?.messages || [])];
            const index = messages.findIndex((item) => item.id === draftId);
            if (index < 0) messages.push({ id: draftId, role: 'assistant', content: streamEvent.delta, citations: [] });
            else messages[index] = { ...messages[index], content: messages[index].content + streamEvent.delta };
            return { ...value, messages };
          });
        }
        if (streamEvent.type === 'done') {
          setThread((value) => {
            const messages = [...(value?.messages || [])];
            const index = messages.findIndex((item) => item.id === draftId);
            if (index < 0) messages.push(streamEvent.message);
            else messages[index] = streamEvent.message;
            return { ...value, messages };
          });
        }
      });
    } catch (e) { setError(e.message); }
    finally { setSending(false); }
  }

  if (!open) return null;
  return (
    <aside ref={panelRef} className="chart-agent-panel" style={{ width }} aria-label="Stratos research chat">
      <div className={`panel-resize left ${resizing ? 'active' : ''}`} {...resizeHandlers} />
      <button className="icon-btn chart-agent-close" onClick={onClose} aria-label="Close Stratos">×</button>
      <WorkflowTimeline workflow={workflow} />
      <div className="chart-agent-messages">
        {(thread?.messages || []).map((message) => <Message key={message.id} message={message} />)}
        {sending && <Thinking activity={activity} />}
        <div ref={bottom} />
      </div>
      <div className="chart-agent-composer">
        {error && <div className="agent-error">{error}</div>}
        <form className="agent-composer" onSubmit={send}>
          <textarea value={input} onChange={(e) => setInput(e.target.value)} rows="1"
            placeholder={workflow?.status === 'running' ? 'Ask about the running validation…' : 'Message Stratos…'} />
          <button className="agent-send" disabled={!input.trim() || sending} aria-label="Send">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 7-7 7 7M12 19V5" /></svg>
          </button>
        </form>
      </div>
    </aside>
  );
}
