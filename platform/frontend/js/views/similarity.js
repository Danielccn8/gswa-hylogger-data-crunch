import { $, api, esc, num, pct, stack, ranked, loading, failure } from '../util.js';
import { selection } from '../state.js';
import { clusterColor, mineralColor, categoryColor, scoreColor } from '../palette.js';
import { heatmap, scatter, stripLog } from '../charts.js';
import { groupColor } from '../palette.js';
import { go } from '../main.js';
import { kSelect, recut } from '../holeclusters.js';

let view = 'combined', colourBy = 'cluster', holeK = 'auto';
const VIEWS = { combined: 'SWIR + TIR (processed)', swir: 'SWIR (processed)', tir: 'TIR (processed)', raw_nir: 'Raw NIR/SWIR spectra', raw_tir: 'Raw TIR spectra' };

export async function render(root, [focus]) {
  root.innerHTML = `<div class="page-head"><div><h1>Similarity between holes</h1>
    <p class="muted">Whole-hole fingerprints compared across any number of holes. Use the selection, or start from one hole and take its nearest matches.</p></div>
    <div class="toolbar"><label class="field">Fingerprint<select id="fingerprint">${Object.entries(VIEWS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
    <label class="field">Start from hole<input id="focus" placeholder="Hole ID" value="${esc(focus || '')}"></label>
    <label class="field">Nearest<select id="k"><option>10</option><option selected>20</option><option>40</option></select></label>
    <button id="seed" type="button" class="secondary">Use hole + nearest</button></div></div>
    <div class="grid cols-2">
      <section class="panel"><h2>Distance matrix</h2><p class="muted small" id="matrix-note">Holes ordered by average-linkage clustering; darker = more similar.</p><div id="matrix"></div></section>
      <section class="panel"><div class="row gap" style="justify-content:space-between"><h2>Map of all holes by fingerprint</h2>
        <div class="row gap">${kSelect('sim-k', holeK)}<label class="field">Colour<select id="colour"><option value="cluster">Hole cluster</option><option value="commodity">Commodity</option><option value="region">Tectonic region</option><option value="anomaly">Unusualness</option></select></label></div></div>
        <p class="muted small">t-SNE embedding of the distance matrix: nearby points have similar fingerprints. Selected holes are outlined.</p><div id="embed"></div><div id="embed-card" class="small muted"></div></section>
    </div>
    <section class="panel" style="margin-top:14px"><h2>Selected holes</h2><div id="profiles"></div></section>
    <section class="panel" style="margin-top:14px"><div class="row gap" style="justify-content:space-between"><div><h2>Down-hole comparison</h2>
      <p class="muted small">Up to four holes side by side on one depth scale: SWIR and TIR mineral-group mix and interval cluster. Tick holes in the table above (the first four are used by default).</p></div>
      <button id="compare-strips" type="button" class="secondary">Compare strip logs</button></div><div id="strips" class="row gap" style="align-items:flex-start;overflow-x:auto"></div></section>`;
  $('#fingerprint').value = view; $('#colour').value = colourBy;
  $('#fingerprint').onchange = e => { view = e.target.value; refresh(); };
  $('#colour').onchange = e => { colourBy = e.target.value; drawEmbedding(); };
  $('#sim-k').onchange = async e => { holeK = e.target.value; await loadEmbedding(); };
  $('#seed').onclick = async () => {
    const id = $('#focus').value.trim(); if (!id) return;
    const d = await api(`/api/v2/holes/${encodeURIComponent(id)}/similar?view=${view}&limit=${$('#k').value}`);
    selection.set([id, ...d.items.map(i => i.hole_id)]); refresh();
  };
  $('#compare-strips').onclick = compareStrips;
  if (focus && selection.list().length < 2) { $('#seed').click(); return; }
  refresh();
}

let embedding = null;
async function refresh() {
  await Promise.all([drawMatrix(), loadEmbedding()]);
}

async function drawMatrix() {
  const node = $('#matrix');
  const all = selection.list();
  const holes = all.slice(0, 150);
  if (holes.length < 2) { node.innerHTML = '<p class="empty">Select at least two holes (map area, tables, or “Use hole + nearest”).</p>'; $('#profiles').innerHTML = ''; return; }
  loading(node);
  let d;
  try { d = await api(`/api/v2/similarity?view=${view}&holes=${encodeURIComponent(holes.join(','))}`); } catch (error) { failure(node, error); return; }
  $('#matrix-note').textContent = `${d.distance || ''}. ${d.missing.length ? `Not in this analysis: ${d.missing.slice(0, 20).join(', ')}${d.missing.length > 20 ? '…' : ''}.` : ''}${all.length > 150 ? ` Showing the first 150 of ${all.length} selected holes.` : ''} Darker = more similar.`;
  heatmap(node, { labels: d.holes, matrix: d.matrix, onPick: (a) => go(`hole/${encodeURIComponent(a)}`) });
  const rows = d.holes.map(h => {
    const p = d.profiles[h]; const s = p?.summary || {};
    const minerals = s.top_minerals || (s.swir || s.tir || {}).top_minerals || {};
    const clusters = s.top_clusters || (s.swir || {}).top_clusters || {};
    return `<tr><td><input type="checkbox" data-strip="${esc(h)}" aria-label="Compare ${esc(h)} down-hole"> <button class="link" data-hole="${esc(h)}">${esc(h)}</button></td><td>${p ? `<span class="swatch" style="background:${clusterColor(p.cluster_id)}"></span>${p.cluster_id}` : '–'}</td>
      <td style="min-width:220px">${stack(minerals, mineralColor)}<span class="muted small">${ranked(minerals, 3).map(([m, v]) => `${esc(m)} ${pct(v)}`).join(' · ')}</span></td>
      <td class="small">${ranked(clusters, 3).map(([n, v]) => `${esc(n)} ${pct(v)}`).join('<br>')}</td>
      <td><button class="ghost" data-remove="${esc(h)}">Remove</button></td></tr>`;
  }).join('');
  $('#profiles').innerHTML = `<div class="table-wrap"><table><thead><tr><th>Hole</th><th>Cluster</th><th>Mineral mix</th><th>Main interval clusters</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  $('#profiles').onclick = event => {
    const open = event.target.closest('[data-hole]')?.dataset.hole; if (open) go(`hole/${encodeURIComponent(open)}`);
    const remove = event.target.closest('[data-remove]')?.dataset.remove; if (remove) { selection.remove(remove); refresh(); }
  };
}

async function loadEmbedding() {
  embedding = await api(`/api/v2/embedding?view=${view}`).catch(() => null);
  const cut = embedding ? await recut(view, holeK).catch(() => null) : null;
  if (cut) {
    embedding.points.forEach(p => { p.cluster_id = cut.assignments[p.hole_id] ?? null; });
    embedding.card = { ...embedding.card, hole_clustering: { ...(embedding.card.hole_clustering || {}), k: cut.k, names: cut.names, sizes: cut.sizes } };
  }
  drawEmbedding();
}

function drawEmbedding() {
  const node = $('#embed');
  if (!embedding || !embedding.points.length) { node.innerHTML = '<p class="empty">This fingerprint has not been analysed yet.</p>'; return; }
  const colour = p => colourBy === 'cluster' ? clusterColor(p.cluster_id) : colourBy === 'anomaly' ? scoreColor(p.anomaly_score)
    : categoryColor(colourBy === 'commodity' ? (p.commodity || '').split(',')[0] : p.tectonic_region);
  scatter(node, { points: embedding.points, colorOf: colour, highlight: new Set(selection.list()),
    describe: p => `<b>${esc(p.hole_id)}</b><br>cluster ${p.cluster_id} · ${esc(p.commodity || '')}<br>${esc(p.tectonic_region || '')}<br><span class="muted">click to add/remove</span>`,
    onPick: p => { selection.toggle(p.hole_id); drawEmbedding(); drawMatrix(); } });
  const card = embedding.card || {};
  const names = card.hole_clustering?.names || {};
  const legend = colourBy === 'cluster' ? `<div class="legend">${Object.entries(names).map(([id, name]) => `<span><i style="background:${clusterColor(id)}"></i>${id} · ${esc(name)} <span class="muted">${num(card.hole_clustering.sizes?.[id])}</span></span>`).join('')}</div>` : '';
  $('#embed-card').innerHTML = `${legend}${num(card.holes)} holes · ${card.hole_clustering?.k ?? '–'} hole clusters (average linkage, silhouette rule)${card.agreement_with_processed ? ` · agreement with processed SWIR: ρ=${card.agreement_with_processed.spearman_rho} on ${card.agreement_with_processed.holes} holes` : ''}`;
}

async function compareStrips() {
  const ticked = [...document.querySelectorAll('[data-strip]:checked')].map(i => i.dataset.strip);
  const holes = (ticked.length ? ticked : [...document.querySelectorAll('[data-strip]')].map(i => i.dataset.strip)).slice(0, 4);
  const node = $('#strips');
  if (!holes.length) { node.innerHTML = '<p class="empty">Select holes first.</p>'; return; }
  node.innerHTML = '<p class="empty">Loading strip logs…</p>';
  const logs = await Promise.all(holes.map(h => api(`/api/v2/holes/${encodeURIComponent(h)}/striplog`).catch(() => null)));
  const depths = logs.flatMap(l => Object.values(l?.tracks || {}).flatMap(t => t.intervals.map(i => i.depth_to_m)));
  const depthMax = Math.max(10, ...depths);
  node.innerHTML = holes.map((h, i) => `<div><h3>${esc(h)}</h3><div class="strip-wrap" id="strip-${i}" style="max-height:70vh"></div></div>`).join('');
  logs.forEach((log, i) => {
    if (!log) return;
    const tracks = [];
    for (const sensor of ['swir', 'tir']) {
      const t = log.tracks[sensor]; if (!t) continue;
      tracks.push({ label: sensor.toUpperCase(), width: 90, kind: 'stack', color: groupColor,
        rows: t.intervals.map(r => ({ f: r.depth_from_m, t: r.depth_to_m, parts: r.groups, row: r })),
        describe: r => ranked(r.row.top_minerals).map(([m, v]) => `${esc(m)} ${pct(v)}`).join('<br>') });
      tracks.push({ label: 'Cl.', width: 16, kind: 'ribbon', rows: t.intervals.filter(r => r.cluster_id != null).map(r => ({ f: r.depth_from_m, t: r.depth_to_m, value: r.cluster_id })),
        describe: r => `Cluster ${r.value}: ${esc(t.cluster_names[`fine:${r.value}`] || '')}` });
    }
    if (tracks.length) stripLog($(`#strip-${i}`), { tracks, depthMin: 0, depthMax, pxPerMetre: Math.max(0.5, Math.min(2, 900 / depthMax)) });
    else $(`#strip-${i}`).innerHTML = '<p class="empty">No prepared data.</p>';
  });
}
