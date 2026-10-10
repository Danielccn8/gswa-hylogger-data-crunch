// Small canvas/SVG chart components (no external dependency, works offline).
import { esc, pct, num, tooltip } from './util.js';
import { groupColor, clusterColor, scoreColor, categoryColor } from './palette.js';

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function hiDpi(canvas, width, height) {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
  const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); return ctx;
}

/* --------------------------------------------------------------- strip log
 tracks: [{label, width, kind: 'stack'|'ribbon'|'line'|'text', rows: [...], ...}]
 rows share depth_from/depth_to (metres). */
export function stripLog(container, { tracks, depthMin, depthMax, pxPerMetre = 3, onPick, onRange, highlight, tvd = null }) {
  const depthAxis = 46;
  const width = depthAxis + tracks.reduce((s, t) => s + t.width + 6, 0);
  const span = Math.max(1, depthMax - depthMin);
  const height = Math.max(240, Math.min(30000, span * pxPerMetre)) + 10;
  container.innerHTML = `<div class="track-head" style="width:${width}px"><div style="width:${depthAxis}px">Depth m</div>${tracks.map(t => `<div style="width:${t.width + 6}px" title="${esc(t.title || t.label)}">${esc(t.label)}</div>`).join('')}</div>`;
  const canvas = document.createElement('canvas'); container.append(canvas);
  const ctx = hiDpi(canvas, width, height);
  const y = d => 5 + (d - depthMin) / span * (height - 10);
  ctx.fillStyle = css('--surface'); ctx.fillRect(0, 0, width, height);
  ctx.font = '11px Segoe UI, system-ui, sans-serif'; ctx.fillStyle = css('--muted'); ctx.strokeStyle = css('--line');
  const step = [1, 2, 5, 10, 20, 50, 100, 200].find(s => s * pxPerMetre >= 28) || 500;
  for (let d = Math.ceil(depthMin / step) * step; d <= depthMax; d += step) {
    ctx.fillText(String(d), 4, y(d) + 4);
    ctx.beginPath(); ctx.moveTo(depthAxis - 4, y(d)); ctx.lineTo(width, y(d)); ctx.globalAlpha = .35; ctx.stroke(); ctx.globalAlpha = 1;
  }
  let x = depthAxis;
  const layout = [];
  for (const track of tracks) {
    layout.push({ x, track });
    for (const row of track.rows) {
      const top = y(row.f), h = Math.max(1, y(row.t) - top);
      if (track.kind === 'stack') {
        const entries = Object.entries(row.parts || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
        const total = entries.reduce((s, [, v]) => s + v, 0);
        let left = x;
        if (!total) { ctx.fillStyle = css('--surface-2'); ctx.fillRect(x, top, track.width, h); continue; }
        for (const [name, v] of entries) { const w = track.width * v / total; ctx.fillStyle = (track.color || groupColor)(name); ctx.fillRect(left, top, w + .5, h); left += w; }
      } else if (track.kind === 'ribbon') {
        ctx.fillStyle = row.color || clusterColor(row.value); ctx.fillRect(x, top, track.width, h);
      } else if (track.kind === 'line') {
        const v = row.value; if (v == null) continue;
        const scaled = track.log ? Math.log10(Math.max(v, track.min || 1e-3)) : v;
        const lo = track.log ? Math.log10(track.min || 1e-3) : (track.min ?? 0), hi = track.log ? Math.log10(track.max) : track.max;
        const w = Math.max(1, track.width * Math.min(1, Math.max(0, (scaled - lo) / ((hi - lo) || 1))));
        ctx.fillStyle = row.color || track.fill || css('--accent'); ctx.fillRect(x, top, w, h);
      } else if (track.kind === 'text') {
        ctx.fillStyle = row.color || categoryColor(row.value); ctx.globalAlpha = .55; ctx.fillRect(x, top, track.width, h); ctx.globalAlpha = 1;
        if (h > 12) { ctx.fillStyle = css('--ink'); ctx.save(); ctx.beginPath(); ctx.rect(x, top, track.width, h); ctx.clip(); ctx.fillText(String(row.label || row.value || '').slice(0, 30), x + 3, top + 11); ctx.restore(); }
      }
    }
    ctx.strokeStyle = css('--line'); ctx.strokeRect(x - .5, 4.5, track.width + 1, height - 9);
    x += track.width + 6;
  }
  if (highlight) {
    ctx.strokeStyle = css('--ink'); ctx.lineWidth = 2;
    ctx.strokeRect(depthAxis - 2, y(highlight.f) - 1, width - depthAxis, Math.max(3, y(highlight.t) - y(highlight.f)) + 2); ctx.lineWidth = 1;
  }
  const pick = event => {
    const box = canvas.getBoundingClientRect();
    const px = event.clientX - box.left, py = event.clientY - box.top;
    const depth = depthMin + (py - 5) / (height - 10) * span;
    const hit = layout.find(l => px >= l.x && px <= l.x + l.track.width);
    if (!hit) return null;
    const row = hit.track.rows.find(r => depth >= r.f && depth < r.t);
    return row ? { track: hit.track, row, depth } : { track: hit.track, row: null, depth };
  };
  canvas.addEventListener('mousemove', event => {
    const hit = pick(event);
    if (!hit || !hit.row) { tooltip(null); return; }
    tooltip(`<b>${esc(hit.track.label)}</b> · ${num(hit.row.f, 1)}–${num(hit.row.t, 1)} m${tvd ? ` <span class="muted">(TVD ≈ ${num(tvd((hit.row.f + hit.row.t) / 2))} m)</span>` : ''}<br>${hit.track.describe ? hit.track.describe(hit.row) : esc(hit.row.label ?? hit.row.value ?? '')}`, event);
  });
  canvas.addEventListener('mouseleave', () => tooltip(null));
  // Drag vertically to choose a depth range (onRange); a plain click inspects one metre (onPick).
  let drag = null, dragged = false;
  const depthAt = event => depthMin + (event.clientY - canvas.getBoundingClientRect().top - 5) / (height - 10) * span;
  const box = document.createElement('div'); box.className = 'range-box'; box.hidden = true; container.append(box);
  canvas.addEventListener('mousedown', event => { if (onRange && event.button === 0) { drag = { start: depthAt(event), y0: event.clientY }; dragged = false; } });
  const move = event => {
    if (!drag) return;
    if (Math.abs(event.clientY - drag.y0) > 4) dragged = true;
    if (!dragged) return;
    const a = Math.max(depthMin, Math.min(drag.start, depthAt(event))), b = Math.min(depthMax, Math.max(drag.start, depthAt(event)));
    Object.assign(box.style, { top: `${canvas.offsetTop + y(a)}px`, left: `${depthAxis}px`, width: `${width - depthAxis}px`, height: `${Math.max(2, y(b) - y(a))}px` });
    box.hidden = false; tooltip(`${num(a, 1)}–${num(b, 1)} m`, event);
  };
  const up = event => {
    if (!drag) return;
    const start = drag.start; drag = null; box.hidden = true;
    if (dragged) { const end = depthAt(event); onRange(Math.max(depthMin, Math.min(start, end)), Math.min(depthMax, Math.max(start, end))); }
  };
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  canvas.addEventListener('click', event => { if (dragged) { dragged = false; return; } const hit = pick(event); if (hit && onPick) onPick(hit); });
  return {
    scrollTo: depth => { container.scrollTop = Math.max(0, y(depth) - 80); },
    destroy: () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); },
  };
}

/* ----------------------------------------------------------------- heatmap */
export function heatmap(container, { labels, matrix, onPick, max = 1 }) {
  const n = labels.length;
  const label = Math.min(130, 8 + Math.max(...labels.map(l => l.length)) * 6.5);
  const cell = Math.max(6, Math.min(28, Math.floor((Math.min(container.clientWidth || 800, 900) - label) / n)));
  const size = label + cell * n + 4;
  const canvas = document.createElement('canvas'); container.replaceChildren(canvas);
  const ctx = hiDpi(canvas, size, size);
  ctx.fillStyle = css('--surface'); ctx.fillRect(0, 0, size, size);
  ctx.font = `${Math.min(11, Math.max(8, cell - 2))}px Segoe UI, system-ui, sans-serif`; ctx.fillStyle = css('--ink');
  for (let i = 0; i < n; i += 1) {
    for (let j = 0; j < n; j += 1) {
      // Similar (distance 0) is deep teal; dissimilar fades to near-white.
      const t = Math.min(1, matrix[i][j] / max);
      ctx.fillStyle = `rgb(${Math.round(15 + 230 * t)},${Math.round(110 + 135 * t)},${Math.round(104 + 141 * t)})`;
      ctx.fillRect(label + j * cell, label + i * cell, cell - (cell > 8 ? 1 : 0), cell - (cell > 8 ? 1 : 0));
    }
    if (cell >= 8) {
      ctx.fillStyle = css('--ink'); ctx.textAlign = 'right'; ctx.fillText(labels[i], label - 4, label + i * cell + cell * .75);
      ctx.save(); ctx.translate(label + i * cell + cell * .75, label - 4); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'left'; ctx.fillText(labels[i], 0, 0); ctx.restore();
    }
  }
  const cellAt = event => {
    const box = canvas.getBoundingClientRect();
    const j = Math.floor((event.clientX - box.left - label) / cell), i = Math.floor((event.clientY - box.top - label) / cell);
    return i >= 0 && j >= 0 && i < n && j < n ? [i, j] : null;
  };
  canvas.addEventListener('mousemove', event => {
    const hit = cellAt(event);
    tooltip(hit ? `<b>${esc(labels[hit[0]])}</b> vs <b>${esc(labels[hit[1]])}</b><br>distance ${matrix[hit[0]][hit[1]].toFixed(3)} · similarity ${pct(1 - matrix[hit[0]][hit[1]])}` : null, event);
  });
  canvas.addEventListener('mouseleave', () => tooltip(null));
  canvas.addEventListener('click', event => { const hit = cellAt(event); if (hit && onPick) onPick(labels[hit[0]], labels[hit[1]]); });
}

/* ----------------------------------------------------------------- scatter */
export function scatter(container, { points, colorOf, highlight = new Set(), onPick, describe, height = 420 }) {
  const width = Math.max(320, container.clientWidth || 600);
  const canvas = document.createElement('canvas'); container.replaceChildren(canvas);
  const ctx = hiDpi(canvas, width, height);
  ctx.fillStyle = css('--surface'); ctx.fillRect(0, 0, width, height);
  if (!points.length) return;
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const px = v => 12 + (v - x0) / ((x1 - x0) || 1) * (width - 24), py = v => 12 + (v - y0) / ((y1 - y0) || 1) * (height - 24);
  const drawn = points.map(p => ({ p, x: px(p.x), y: py(p.y) }));
  for (const d of drawn) {
    const on = highlight.has(d.p.hole_id);
    ctx.globalAlpha = highlight.size && !on ? .35 : .9;
    ctx.fillStyle = colorOf(d.p); ctx.beginPath(); ctx.arc(d.x, d.y, on ? 6 : 3.5, 0, Math.PI * 2); ctx.fill();
    if (on) { ctx.globalAlpha = 1; ctx.strokeStyle = css('--ink'); ctx.lineWidth = 1.5; ctx.stroke(); ctx.lineWidth = 1; }
  }
  ctx.globalAlpha = 1;
  const nearest = event => {
    const box = canvas.getBoundingClientRect(); const mx = event.clientX - box.left, my = event.clientY - box.top;
    let best = null, bd = 64;
    for (const d of drawn) { const dd = (d.x - mx) ** 2 + (d.y - my) ** 2; if (dd < bd) { bd = dd; best = d.p; } }
    return best;
  };
  canvas.addEventListener('mousemove', event => { const p = nearest(event); tooltip(p ? describe(p) : null, event); });
  canvas.addEventListener('mouseleave', () => tooltip(null));
  canvas.addEventListener('click', event => { const p = nearest(event); if (p && onPick) onPick(p); });
}

/* ------------------------------------------------------------ line (SVG) */
// bands: [{ low, high, color, name }] are shaded ranges (for example the 10th–90th percentile).
// marks: [{ x, label }] are vertical guide lines (for example absorption features).
export function lineChart({ x, series, bands = [], marks = [], xLabel = '', yLabel = '', width = 560, height = 220 }) {
  const all = [...series.flatMap(s => s.values), ...bands.flatMap(b => [...b.low, ...b.high])].filter(Number.isFinite);
  if (!all.length || x.length < 2) return '<p class="empty">No data.</p>';
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const pxv = v => 48 + (v - x[0]) / (x[x.length - 1] - x[0]) * (width - 60);
  const px = i => pxv(x[i]);
  const py = v => height - 30 - (v - lo) / span * (height - 42);
  const path = values => values.map((v, i) => Number.isFinite(v) ? `${px(i).toFixed(1)},${py(v).toFixed(1)}` : null).filter(Boolean).join(' ');
  const area = b => {
    const top = b.high.map((v, i) => Number.isFinite(v) ? `${px(i).toFixed(1)},${py(v).toFixed(1)}` : null).filter(Boolean);
    const bottom = b.low.map((v, i) => Number.isFinite(v) ? `${px(i).toFixed(1)},${py(v).toFixed(1)}` : null).filter(Boolean).reverse();
    return `<polygon points="${[...top, ...bottom].join(' ')}" fill="${b.color}" fill-opacity="${b.opacity ?? 0.18}" stroke="none"/>`;
  };
  const named = [...bands.filter(b => b.name).map(b => ({ ...b, band: true })), ...series.filter(s => s.name)];
  return `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(yLabel || 'Values')} against ${esc(xLabel)}" style="max-width:${width}px">
    <path d="M48 8V${height - 30}H${width - 12}" fill="none" stroke="var(--line)"/>
    ${marks.map(m => `<line x1="${pxv(m.x).toFixed(1)}" x2="${pxv(m.x).toFixed(1)}" y1="8" y2="${height - 30}" stroke="var(--line)" stroke-dasharray="2 3"/><text x="${pxv(m.x).toFixed(1)}" y="16" text-anchor="middle" font-size="9" fill="var(--muted)">${esc(m.label)}</text>`).join('')}
    ${bands.map(area).join('')}
    ${series.map(s => `<polyline points="${path(s.values)}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.6}" ${s.dash ? 'stroke-dasharray="4 3"' : ''}/>`).join('')}
    <text x="44" y="14" text-anchor="end" font-size="10" fill="var(--muted)">${hi.toPrecision(3)}</text>
    <text x="44" y="${height - 30}" text-anchor="end" font-size="10" fill="var(--muted)">${lo.toPrecision(3)}</text>
    <text x="48" y="${height - 16}" font-size="10" fill="var(--muted)">${num(x[0])}</text>
    <text x="${width - 12}" y="${height - 16}" text-anchor="end" font-size="10" fill="var(--muted)">${num(x[x.length - 1])}</text>
    <text x="${(width + 36) / 2}" y="${height - 3}" text-anchor="middle" font-size="11" fill="var(--muted)">${esc(xLabel)}</text>
  </svg>${named.length ? `<div class="legend small">${named.map(s => `<span><i style="background:${s.color};${s.band ? 'opacity:.35;border-radius:2px' : 'border-radius:1px;height:3px;vertical-align:3px'}"></i>${esc(s.name)}</span>`).join('')}</div>` : ''}`;
}

export { scoreColor };

/* ------------------------------------------------- DTW alignment of two holes */
export function alignment(container, { a, b, stepsA, stepsB, path, colorOf, height = 640 }) {
  const width = Math.max(420, Math.min(container.clientWidth || 560, 640));
  const canvas = document.createElement('canvas'); container.replaceChildren(canvas);
  const ctx = hiDpi(canvas, width, height);
  ctx.fillStyle = css('--surface'); ctx.fillRect(0, 0, width, height);
  const colW = 54, ax = 70, bx = width - 70 - colW, top = 34, bottom = height - 16;
  const scale = steps => {
    const lo = Math.min(...steps.map(s => s.f)), hi = Math.max(...steps.map(s => s.t));
    return { lo, hi, y: d => top + (d - lo) / ((hi - lo) || 1) * (bottom - top) };
  };
  const sa = scale(stepsA), sb = scale(stepsB);
  ctx.font = '12px Segoe UI, system-ui, sans-serif'; ctx.fillStyle = css('--ink'); ctx.textAlign = 'center';
  ctx.fillText(a, ax + colW / 2, 18); ctx.fillText(b, bx + colW / 2, 18);
  ctx.globalAlpha = 0.28; ctx.lineWidth = 1;
  for (const [i, j] of path) {
    const s1 = stepsA[i], s2 = stepsB[j];
    ctx.strokeStyle = colorOf(s1.cluster_id);
    ctx.beginPath();
    ctx.moveTo(ax + colW, sa.y((s1.f + s1.t) / 2));
    ctx.bezierCurveTo((ax + bx) / 2, sa.y((s1.f + s1.t) / 2), (ax + bx) / 2, sb.y((s2.f + s2.t) / 2), bx, sb.y((s2.f + s2.t) / 2));
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  const column = (steps, sc, x) => {
    for (const s of steps) { ctx.fillStyle = colorOf(s.cluster_id); ctx.fillRect(x, sc.y(s.f), colW, Math.max(1, sc.y(s.t) - sc.y(s.f))); }
    ctx.strokeStyle = css('--line'); ctx.strokeRect(x - .5, top - .5, colW + 1, bottom - top + 1);
    ctx.fillStyle = css('--muted'); ctx.font = '10px Segoe UI, system-ui, sans-serif'; ctx.textAlign = x < width / 2 ? 'right' : 'left';
    const step = [10, 20, 50, 100, 200, 500].find(v => (sc.hi - sc.lo) / v <= 12) || 1000;
    for (let d = Math.ceil(sc.lo / step) * step; d <= sc.hi; d += step) {
      ctx.fillText(`${d} m`, x < width / 2 ? x - 5 : x + colW + 5, sc.y(d) + 3);
    }
  };
  column(stepsA, sa, ax); column(stepsB, sb, bx);
}
