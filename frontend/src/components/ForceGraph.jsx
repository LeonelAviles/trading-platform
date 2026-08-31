import { useEffect, useRef } from 'react';
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force';
import { clusterColor, matchesQuery, neighbourhood, adjacency, radius, showLabel } from '../knowledge/graphMath';

// Interactive force-directed graph on a canvas: pan (drag background), zoom
// (wheel), drag nodes, hover to highlight the neighbourhood, click to select,
// double-click to expand. Simulation via d3-force; drawing is ours so a few
// thousand nodes stay smooth.
export default function ForceGraph({ nodes, edges, selected, focus, query, onSelect, onExpand, onHover, height = 640 }) {
  const canvasRef = useRef(null);
  const state = useRef({ sim: null, nodes: [], edges: [], transform: { x: 0, y: 0, k: 1 }, hovered: null, drag: null, adj: new Map(),
    pointer: null, raf: 0, dirty: true, props: {} });

  // Keep latest props reachable from the draw loop without re-creating the simulation.
  state.current.props = { selected, focus, query, onSelect, onExpand, onHover };

  useEffect(() => {
    const st = state.current;
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const prev = new Map(st.nodes.map((n) => [n.id, n]));
    // Reuse positions of nodes that survive a filter change so the picture does not jump.
    st.nodes = nodes.map((n) => {
      const p = prev.get(n.id);
      return p ? Object.assign(p, n, { x: p.x, y: p.y, vx: p.vx, vy: p.vy }) : { ...n };
    });
    const byId = new Map(st.nodes.map((n) => [n.id, n]));
    st.edges = edges.filter((e) => byId.has(e.source) && byId.has(e.target)).map((e) => ({ ...e, source: e.source, target: e.target }));
    st.adj = adjacency(st.edges);
    if (st.sim) st.sim.stop();
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || height;
    st.sim = forceSimulation(st.nodes)
      .force('link', forceLink(st.edges).id((d) => d.id).distance((e) => 40 + 60 / Math.sqrt(e.weight || 1)).strength((e) => Math.min(1, 0.2 + e.weight * 0.08)))
      .force('charge', forceManyBody().strength((d) => -30 - radius(d.facts, d.type) * 8))
      .force('collide', forceCollide().radius((d) => radius(d.facts, d.type) + 2))
      .force('center', forceCenter(w / 2, h / 2))
      .alpha(1).alphaDecay(0.03)
      .on('tick', () => { st.dirty = true; });
    st.dirty = true;
    return () => { st.sim.stop(); };
  }, [nodes, edges, height]);

  useEffect(() => {
    const st = state.current;
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      st.dirty = true;
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    function draw() {
      st.raf = requestAnimationFrame(draw);
      if (!st.dirty) return;
      st.dirty = false;
      const { x: tx, y: ty, k } = st.transform;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      const { selected: sel, focus: foc, query: q } = st.props;
      const hov = st.hovered;
      const emphasis = hov ? neighbourhood(st.adj, hov) : sel ? neighbourhood(st.adj, sel) : null;
      ctx.clearRect(0, 0, w, h);
      ctx.save();
      ctx.translate(tx, ty);
      ctx.scale(k, k);
      for (const e of st.edges) {
        const a = e.source;
        const b = e.target;
        if (a.x == null || b.x == null) continue;
        let alpha = Math.min(0.55, 0.08 + e.weight * 0.05);
        if (emphasis) alpha = emphasis.has(a.id) && emphasis.has(b.id) && (a.id === (hov || sel) || b.id === (hov || sel)) ? 0.8 : 0.04;
        else if (foc) alpha = foc.has(a.id) && foc.has(b.id) ? 0.6 : 0.03;
        ctx.strokeStyle = `rgba(160,170,190,${alpha})`;
        ctx.lineWidth = Math.min(3, 0.4 + e.weight * 0.25) / k;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      ctx.font = `${11 / k}px system-ui, sans-serif`;
      ctx.textBaseline = 'middle';
      for (const n of st.nodes) {
        if (n.x == null) continue;
        const r = radius(n.facts, n.type);
        let alpha = 1;
        if (emphasis) alpha = emphasis.has(n.id) ? 1 : 0.12;
        else if (foc) alpha = foc.has(n.id) ? 1 : 0.1;
        const hit = matchesQuery(n, q);
        ctx.globalAlpha = alpha;
        ctx.beginPath();
        if (n.type === 'source') ctx.rect(n.x - r, n.y - r, 2 * r, 2 * r);
        else if (n.type === 'strategy' || n.type === 'teaching') {
          ctx.moveTo(n.x, n.y - r); ctx.lineTo(n.x + r, n.y); ctx.lineTo(n.x, n.y + r); ctx.lineTo(n.x - r, n.y); ctx.closePath();
        } else ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = n.type === 'topic' ? '#e0e0e0' : n.type === 'instrument' ? '#ffffff' : clusterColor(n.cluster);
        ctx.fill();
        if (n.id === sel || hit) {
          ctx.lineWidth = 2.5 / k;
          ctx.strokeStyle = hit && n.id !== sel ? '#ffca28' : '#ffffff';
          ctx.stroke();
        } else if (n.type === 'source' && n.providedBy === 'user') {
          ctx.lineWidth = 1.5 / k;
          ctx.strokeStyle = '#ffca28';
          ctx.stroke();
        }
        if (showLabel(n, { scale: k, hovered: hov, selected: sel, focus: emphasis || foc }) || hit) {
          const label = n.type === 'topic' ? n.label.split(':')[0].slice(0, 36) : n.label.slice(0, 40);
          ctx.fillStyle = 'rgba(0,0,0,0.55)';
          const tw = ctx.measureText(label).width;
          ctx.fillRect(n.x + r + 2 / k, n.y - 7 / k, tw + 4 / k, 14 / k);
          ctx.fillStyle = '#f2f2f2';
          ctx.fillText(label, n.x + r + 4 / k, n.y);
        }
      }
      ctx.globalAlpha = 1;
      ctx.restore();
    }
    st.raf = requestAnimationFrame(draw);

    function toGraph(ev) {
      const rect = canvas.getBoundingClientRect();
      const { x, y, k } = st.transform;
      return { x: (ev.clientX - rect.left - x) / k, y: (ev.clientY - rect.top - y) / k };
    }
    function nodeAt(p) {
      const k = st.transform.k;
      let best = null;
      let bd = Infinity;
      for (const n of st.nodes) {
        if (n.x == null) continue;
        const r = radius(n.facts, n.type) + 4 / k;
        const d = (n.x - p.x) ** 2 + (n.y - p.y) ** 2;
        if (d <= r * r && d < bd) { best = n; bd = d; }
      }
      return best;
    }
    function onDown(ev) {
      const p = toGraph(ev);
      const n = nodeAt(p);
      st.drag = n ? { node: n, moved: false } : { pan: true, sx: ev.clientX, sy: ev.clientY, ox: st.transform.x, oy: st.transform.y, moved: false };
      if (n) { n.fx = n.x; n.fy = n.y; st.sim.alphaTarget(0.3).restart(); }
      canvas.setPointerCapture?.(ev.pointerId);
    }
    function onMove(ev) {
      const p = toGraph(ev);
      if (st.drag?.node) {
        st.drag.node.fx = p.x; st.drag.node.fy = p.y; st.drag.moved = true; st.dirty = true; return;
      }
      if (st.drag?.pan) {
        st.transform.x = st.drag.ox + (ev.clientX - st.drag.sx);
        st.transform.y = st.drag.oy + (ev.clientY - st.drag.sy);
        st.drag.moved = true; st.dirty = true; return;
      }
      const n = nodeAt(p);
      const id = n ? n.id : null;
      if (id !== st.hovered) { st.hovered = id; st.dirty = true; st.props.onHover?.(n || null); canvas.style.cursor = n ? 'pointer' : 'grab'; }
    }
    function onUp(ev) {
      const d = st.drag;
      st.drag = null;
      if (d?.node) {
        d.node.fx = null; d.node.fy = null; st.sim.alphaTarget(0);
        if (!d.moved) st.props.onSelect?.(d.node);
      } else if (d?.pan && !d.moved) {
        st.props.onSelect?.(null);
      }
      canvas.releasePointerCapture?.(ev.pointerId);
    }
    function onDbl(ev) {
      const n = nodeAt(toGraph(ev));
      if (n) st.props.onExpand?.(n);
    }
    function onWheel(ev) {
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;
      const factor = Math.exp(-ev.deltaY * 0.0015);
      const k = Math.max(0.15, Math.min(8, st.transform.k * factor));
      const ratio = k / st.transform.k;
      st.transform = { k, x: mx - (mx - st.transform.x) * ratio, y: my - (my - st.transform.y) * ratio };
      st.dirty = true;
    }
    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('pointerleave', () => { if (st.hovered) { st.hovered = null; st.dirty = true; st.props.onHover?.(null); } });
    canvas.addEventListener('dblclick', onDbl);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      cancelAnimationFrame(st.raf);
      ro.disconnect();
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('dblclick', onDbl);
      canvas.removeEventListener('wheel', onWheel);
    };
  }, []);

  // Selection / focus / query changes only need a redraw.
  useEffect(() => { state.current.dirty = true; }, [selected, focus, query]);

  return <canvas ref={canvasRef} className="kg-canvas" style={{ height }} />;
}
