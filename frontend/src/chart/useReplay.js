import { useCallback, useEffect, useRef, useState } from 'react';
import { replaySocketUrl } from '../api';

const TRADE_RING = 6000;

function initialState() {
  return {
    status: 'idle', pct: 0, error: null, note: null,
    symbol: null, root: null, date: null, tickSize: 0.25, dayStart: null, dayEnd: null,
    clock: null, paused: true, speed: 1, bookMode: 'off', ended: false,
    bars: {}, book: null, trades: [], lastTrade: null,
    footprint: null, footprints: {}, position: null, fills: [], vap: new Map(), cvd: 0, marks: [],
    stats: { messages: 0 },
  };
}

// Client side of /ws/replay (PLATFORM-SPEC.md §4.11). High-rate state lives
// in a ref and React is bumped at most once per animation frame; subscribers
// get every raw message synchronously for chart-series updates.
export function useReplay() {
  const ref = useRef(initialState());
  const wsRef = useRef(null);
  const rafRef = useRef(null);
  const subsRef = useRef(new Set());
  const [tick, setTick] = useState(0);

  const bump = useCallback(() => {
    if (rafRef.current != null) return;
    rafRef.current = requestAnimationFrame(() => { rafRef.current = null; setTick((n) => n + 1); });
  }, []);

  const apply = useCallback((m) => {
    const s = ref.current;
    s.stats.messages += 1;
    switch (m.type) {
      case 'preparing':
        s.status = 'preparing'; s.pct = m.pct; if (m.note) s.note = m.note; break;
      case 'ready':
        Object.assign(s, {
          status: 'ready', error: null, symbol: m.symbol, root: m.root, date: m.date, tickSize: m.tickSize,
          dayStart: m.dayStart, dayEnd: m.dayEnd, clock: m.clock, paused: m.paused, speed: m.speed,
          bookMode: m.bookMode, ended: false, bars: m.bars, book: m.book ? { ...m.book, approx: m.bookMode !== 'l3' } : null,
          trades: m.lastTrades || [], lastTrade: m.lastTrades?.length ? m.lastTrades[m.lastTrades.length - 1] : null,
          footprint: m.footprint, position: m.position, fills: m.trades || [], cvd: m.cvd,
        });
        s.vap = new Map((m.volumeAtPrice || []).map(([p, v]) => [p, v]));
        s.footprints = {};
        break;
      case 'clock':
        s.clock = m.ts; s.paused = m.paused; s.speed = m.speed; break;
      case 'trades': {
        const arr = s.trades;
        for (const t of m.items) {
          arr.push(t);
          s.vap.set(t.price, (s.vap.get(t.price) || 0) + t.size);
          s.cvd += t.side === 'B' ? t.size : -t.size;
        }
        if (arr.length > TRADE_RING) arr.splice(0, arr.length - TRADE_RING);
        s.lastTrade = arr[arr.length - 1] || s.lastTrade;
        if (s.lastTrade) s.clock = Math.max(s.clock || 0, s.lastTrade.ts);
        break;
      }
      case 'book':
        s.book = { ts: m.ts, bids: m.bids, asks: m.asks, approx: !!m.approx }; break;
      case 'bar': {
        const list = s.bars[m.tf] || (s.bars[m.tf] = []);
        const last = list[list.length - 1];
        if (last && last.time === m.bar.time) list[list.length - 1] = m.bar;
        else if (!last || m.bar.time > last.time) list.push(m.bar);
        if (m.bar.cvd != null) s.cvd = m.bar.cvd;
        break;
      }
      case 'footprint':
        if (m.closed) s.footprints[m.time] = m.levels;
        else s.footprint = m;
        break;
      case 'position':
        s.position = m.position; break;
      case 'fill':
        if (m.trade) { s.fills = [...s.fills, m.trade]; s.position = null; } else s.position = m.position;
        break;
      case 'mode':
        s.bookMode = m.bookMode; s.speed = m.speed; break;
      case 'end':
        s.ended = true; s.paused = true; break;
      case 'marked':
        s.marks = [...s.marks, m]; break;
      case 'error':
        s.error = m.message; if (s.status !== 'ready') s.status = 'error'; break;
      default:
        break;
    }
    for (const fn of subsRef.current) fn(m, s);
    bump();
  }, [bump]);

  const stop = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState <= 1) ws.close();
    ref.current = initialState();
    bump();
  }, [bump]);

  const send = useCallback((msg) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
  }, []);

  // Open a socket and send `start`. Replaces any running session.
  const start = useCallback((params) => {
    stop();
    const s = ref.current;
    s.status = 'connecting';
    bump();
    const ws = new WebSocket(replaySocketUrl());
    wsRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: 'start', ...params }));
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (wsRef.current === ws) apply(m);
    };
    ws.onerror = () => { if (wsRef.current === ws) { ref.current.error = 'replay socket error'; ref.current.status = 'error'; bump(); } };
    ws.onclose = () => { if (wsRef.current === ws) { wsRef.current = null; if (ref.current.status !== 'error') ref.current.status = 'closed'; bump(); } };
  }, [apply, bump, stop]);

  const subscribe = useCallback((fn) => {
    subsRef.current.add(fn);
    return () => subsRef.current.delete(fn);
  }, []);

  useEffect(() => () => { if (rafRef.current != null) cancelAnimationFrame(rafRef.current); stop(); }, [stop]);

  return { replay: ref.current, tick, start, send, stop, subscribe };
}
