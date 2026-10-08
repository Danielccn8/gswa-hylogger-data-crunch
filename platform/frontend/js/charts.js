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
export function stripLog(container, { tracks, depthMin, depthMax, pxPerMetre = 3, onPick, highlight }) {
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
    tooltip(`<b>${esc(hit.track.label)}</b> · ${num(hit.row.f, 1)}–${num(hit.row.t, 1)} m<br>${hit.track.describe ? hit.track.describe(hit.row) : esc(hit.row.label ?? hit.row.value ?? '')}`, event);
  });
  canvas.addEventListener('mouseleave', () => tooltip(null));
  canvas.addEventListener('click', event => { const hit = pick(event); if (hit && onPick) onPick(hit); });
  return { scrollTo: depth => { container.scrollTop = Math.max(0, y(depth) - 80); } };
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
export function lineChart({ x, series, xLabel = '', yLabel = '', width = 560, height = 220 }) {
  const all = series.flatMap(s => s.values).filter(Number.isFinite);
  if (!all.length || x.length < 2) return '<p class="empty">No data.</p>';
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const px = i => 48 + (x[i] - x[0]) / (x[x.length - 1] - x[0]) * (width - 60);
  const py = v => height - 34 - (v - lo) / span * (height - 50);
  const path = values => values.map((v, i) => Number.isFinite(v) ? `${px(i).toFixed(1)},${py(v).toFixed(1)}` : null).filter(Boolean).join(' ');
  return `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(yLabel)} against ${esc(xLabel)}" style="max-width:${width}px">
    <path d="M48 12V${height - 34}H${width - 12}" fill="none" stroke="var(--line)"/>
    ${series.map(s => `<polyline points="${path(s.values)}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.6}" ${s.dash ? 'stroke-dasharray="4 3"' : ''}/>`).join('')}
    <text x="44" y="18" text-anchor="end" font-size="10" fill="var(--muted)">${hi.toPrecision(3)}</text>
    <text x="44" y="${height - 34}" text-anchor="end" font-size="10" fill="var(--muted)">${lo.toPrecision(3)}</text>
    <text x="48" y="${height - 18}" font-size="10" fill="var(--muted)">${num(x[0])}</text>
    <text x="${width - 12}" y="${height - 18}" text-anchor="end" font-size="10" fill="var(--muted)">${num(x[x.length - 1])}</text>
    <text x="${(width + 36) / 2}" y="${height - 4}" text-anchor="middle" font-size="11" fill="var(--muted)">${esc(xLabel)}</text>
    ${series.length > 1 ? '' : ''}${series.map((s, i) => `<g transform="translate(${60 + (i % 4) * 150},${16 + Math.floor(i / 4) * 12})"><rect width="10" height="3" y="-3" fill="${s.color}"/><text x="14" font-size="10" fill="var(--muted)">${esc(s.name || '')}</text></g>`).join('')}
  </svg>`;
}

export { scoreColor };
