import { $, api, esc, num, pct, stack, ranked, loading, failure, toast } from '../util.js';
import { selection } from '../state.js';
import { clusterColor, mineralColor, categoryColor, scoreColor, groupColor } from '../palette.js';
import { heatmap, scatter, stripLog, alignment, lineChart } from '../charts.js';
import { go } from '../main.js';
import { kSelect, recut } from '../holeclusters.js';
import { TERMS, NIR_MARKS, info } from '../glossary.js';

let view = 'combined', colourBy = 'cluster', holeK = 'auto';
const VIEWS = { combined: 'Mineral mix · SWIR + TIR', swir: 'Mineral mix · SWIR', tir: 'Mineral mix · TIR', seq_combined: 'Down-hole sequence · SWIR + TIR', seq_swir: 'Down-hole sequence · SWIR', seq_tir: 'Down-hole sequence · TIR', raw_nir: 'Raw spectra · NIR/SWIR', raw_tir: 'Raw spectra · TIR' };
const VIEW_HELP = 'Mineral mix: what each hole contains (share of its metres in each interval cluster). Down-hole sequence: also the order of zones from top to bottom, allowing zones to be thicker or thinner (dynamic time warping). Raw spectra: the measured spectra themselves, independent of the TSG mineral interpretation.';
let map, mapLayer, mapRows = [], mapNames = {}, embedding = null, lastMatrix = null, lastAlign = null, unsubscribe = null;
let stripPick = null, spectraPick = null, mapGroupingSupported = true;

export async function render(root, [focus]) {
  root.innerHTML = `<div class="page-head"><div><h1>Compare holes</h1>
    <p class="muted">Build a set of holes (on the map, in the fingerprint plot or by typing IDs) and compare them: overall similarity, side-by-side strip logs, a depth-by-depth alignment and their raw spectra.</p></div>
    <div class="toolbar"><label class="field"><span>Compare by ${info(VIEW_HELP)}</span><select id="fingerprint">${Object.entries(VIEWS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
    ${kSelect('sim-k', holeK)}</div></div>
    <div class="grid cols-2">
      <section class="panel"><div class="row gap" style="justify-content:space-between"><h2>1 · Choose holes on the map ${info('Holes are coloured by hole cluster for the comparison chosen above. Click a hole to add or remove it; drag a rectangle with “Select area” to add many. Holes in the set are outlined in black.')}</h2>
        <button id="sim-area" type="button" class="secondary">Select area</button></div>
        <div id="sim-map" class="map" style="height:430px;min-height:430px"></div><div id="sim-legend" class="legend"></div></section>
      <section class="panel"><div class="row gap" style="justify-content:space-between"><h2>…or by fingerprint ${info('t-SNE map of every hole: holes close together here have similar fingerprints, wherever they are geographically. Click a point to add or remove it.')}</h2>
        <label class="field"><select id="colour" aria-label="Colour points by"><option value="cluster">Hole cluster</option><option value="commodity">Target commodity</option><option value="region">Tectonic region</option><option value="anomaly">Unusualness</option></select></label></div>
        <div id="embed"></div><div id="embed-card" class="small muted"></div></section>
    </div>
    <section class="panel" style="margin-top:14px"><h2>2 · Comparison set ${info('Every comparison below uses these holes. It is the same list as the Selection button at the top right, so holes added on the Map tab appear here too.')}</h2>
      <div id="set-chips" class="chips" style="margin:6px 0 10px"></div>
      <div class="toolbar" style="margin:0">
        <label class="field" style="flex:1;min-width:220px">Type or paste hole IDs<input id="type-ids" placeholder="e.g. WTB-60, WTB-29 13TRDD012"></label><button id="add-ids" type="button" class="secondary">Add</button>
        <label class="field">Start from a hole<input id="focus" placeholder="Hole ID" value="${esc(focus || '')}" style="width:140px"></label>
        <label class="field">and its<select id="k"><option>5</option><option selected>10</option><option>20</option><option>40</option></select></label>
        <button id="seed" type="button" class="secondary">most similar holes</button>
        <button id="clear-set" type="button" class="ghost">Clear set</button></div></section>
    <div class="grid cols-2" style="margin-top:14px">
      <section class="panel"><h2>3 · How similar are they? ${info(TERMS.similarity + ' Holes are ordered so that similar holes sit next to each other; darker cells = more similar. Click a cell to open the hole.')}</h2><p class="muted small" id="matrix-note"></p><div id="matrix"></div></section>
      <section class="panel"><h2>What each hole contains</h2><div id="profiles"></div></section>
    </div>
    <section class="panel" style="margin-top:14px"><h2>4 · Strip logs side by side ${info('Mineral-group mix by depth for up to four holes on one depth scale, with the interval cluster ribbon. Pick the holes below.')}</h2>
      <div class="row gap" style="justify-content:space-between"><div id="strip-pick" class="chips"></div><button id="compare-strips" type="button">Show strip logs</button></div>
      <div id="strips" class="row gap" style="align-items:flex-start;overflow-x:auto;margin-top:10px"></div></section>
    <section class="panel" style="margin-top:14px"><h2>5 · Align two holes depth by depth</h2>
      <div class="grid cols-2" style="align-items:start"><div class="notice" style="margin:0"><b>How to read it.</b> Each hole is cut into 10 m steps and the two sequences are matched from top to bottom (dynamic time warping).
        <ul style="margin:6px 0 0;padding-left:18px"><li>A line joins two depths that the method considers equivalent.</li><li>A fan of lines from one step to many means that zone is thicker in the other hole.</li>
        <li>Colours are interval clusters; matching colours on both sides mean the same mineral assemblage.</li><li>The distance is 0 for identical sequences; below ~0.15 the holes share most of their zones in the same order.</li></ul></div>
        <div class="toolbar" style="margin:0"><label class="field">Hole A<select id="align-a"></select></label><label class="field">Hole B<select id="align-b"></select></label>
          <label class="field">Sensor<select id="align-sensor"><option value="swir">SWIR</option><option value="tir">TIR</option></select></label><button id="align" type="button">Align</button>
          <p class="muted small" style="flex-basis:100%">Default: the most similar pair in the set.</p></div></div>
      <div class="grid cols-2" style="margin-top:10px"><div id="align-chart"></div><div id="align-zones"></div></div></section>
    <section class="panel" style="margin-top:14px"><h2>6 · Compare raw spectra ${info('Average measured spectrum of each hole (or of matched zones), with the range covered by 80% of its metres. Independent of the TSG mineral interpretation. Spectral angle compares the shape only: below ~3° nearly identical, above ~10° clearly different.')}</h2>
      <div class="toolbar" style="margin:0"><div id="spectra-pick" class="chips" style="flex:1"></div>
        <label class="field">Sensor<select id="spectra-sensor"><option value="nir">NIR/SWIR</option><option value="tir">TIR</option></select></label>
        <label class="field">Part of the hole<select id="spectra-range"><option value="">Whole hole</option></select></label>
        <button id="spectra" type="button">Compare spectra</button></div>
      <div class="grid cols-main-side" style="margin-top:10px"><div id="spectra-chart"></div><div id="spectra-table"></div></div></section>`;
  $('#fingerprint').value = view; $('#colour').value = colourBy;
  $('#fingerprint').onchange = e => { view = e.target.value; refresh(); };
  $('#colour').onchange = e => { colourBy = e.target.value; drawEmbedding(); };
  $('#sim-k').onchange = async e => { holeK = e.target.value; await Promise.all([loadEmbedding(), loadMap(), drawMatrix()]); };
  $('#seed').onclick = seed;
  $('#add-ids').onclick = addTyped;
  $('#type-ids').onkeydown = e => { if (e.key === 'Enter') addTyped(); };
  $('#clear-set').onclick = () => selection.clear();
  $('#compare-strips').onclick = compareStrips;
  $('#align').onclick = alignTwo;
  $('#spectra').onclick = compareSpectra;
  $('#set-chips').onclick = e => { const id = e.target.closest('[data-remove]')?.dataset.remove; if (id) selection.remove(id); };
  unsubscribe = selection.on(() => { drawSet(); drawMatrix(); drawEmbedding(); styleMap(); });
  setupMap();
  drawSet();
  if (focus && selection.list().length < 2) { await seed(); return; }
  refresh();
}

async function seed() {
  const id = $('#focus').value.trim(); if (!id) { toast('Type a hole ID first'); return; }
  try {
    const d = await api(`/api/v2/holes/${encodeURIComponent(id)}/similar?view=${view}&limit=${$('#k').value}`);
    selection.set([id, ...d.items.map(i => i.hole_id)]);
  } catch (error) { toast(error.message); }
  refresh();
}

function addTyped() {
  const ids = $('#type-ids').value.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean);
  if (!ids.length) return;
  const known = new Set(mapRows.map(r => r.hole_id));
  const found = ids.filter(id => !known.size || known.has(id)), unknown = ids.filter(id => known.size && !known.has(id));
  if (found.length) selection.add(found);
  $('#type-ids').value = unknown.join(' ');
  toast(`${found.length} added${unknown.length ? ` · not found: ${unknown.slice(0, 5).join(', ')}` : ''}`);
}

function drawSet() {
  const list = selection.list();
  $('#set-chips').innerHTML = list.length
    ? list.map(id => `<span class="chip">${esc(id)}<button type="button" data-remove="${esc(id)}" aria-label="Remove ${esc(id)}">×</button></span>`).join('') + `<span class="muted small">${list.length} holes</span>`
    : '<span class="muted">No holes yet — click holes on the map or in the fingerprint plot, type IDs, or start from a hole and its most similar holes.</span>';
  stripPick = pickList('#strip-pick', list, stripPick, 4);
  spectraPick = pickList('#spectra-pick', list, spectraPick, 6);
  const options = list.map(id => `<option>${esc(id)}</option>`).join('');
  const [a, b] = [$('#align-a').value, $('#align-b').value];
  $('#align-a').innerHTML = options; $('#align-b').innerHTML = options;
  const pair = closestPair();
  $('#align-a').value = list.includes(a) ? a : pair[0] || list[0] || '';
  $('#align-b').value = list.includes(b) ? b : pair[1] || list[1] || '';
}

// Toggle chips choosing up to `max` holes of the set; remembers the choice while holes stay in the set.
function pickList(selector, list, chosen, max) {
  const keep = (chosen || []).filter(id => list.includes(id));
  const picked = keep.length ? keep : list.slice(0, max);
  const node = $(selector);
  node.innerHTML = list.length ? list.map(id => `<label class="chip"><input type="checkbox" value="${esc(id)}" ${picked.includes(id) ? 'checked' : ''}> ${esc(id)}</label>`).join('') + `<span class="muted small">up to ${max}</span>` : '<span class="muted small">Add holes to the set first.</span>';
  node.onchange = event => {
    const boxes = [...node.querySelectorAll('input:checked')];
    if (boxes.length > max) { event.target.checked = false; toast(`Choose at most ${max} holes`); }
    const ids = [...node.querySelectorAll('input:checked')].map(i => i.value);
    if (selector === '#strip-pick') stripPick = ids; else spectraPick = ids;
  };
  return picked;
}

async function refresh() {
  await Promise.all([drawMatrix(), loadEmbedding(), loadMap()]);
}

/* ------------------------------------------------------------------ map */
function setupMap() {
  if (!globalThis.L) { $('#sim-map').innerHTML = '<p class="empty">Map library unavailable.</p>'; return; }
  map = L.map('sim-map', { preferCanvas: true, boxZoom: false }).setView([-25.5, 121.5], 4);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors', maxZoom: 17 }).addTo(map);
  mapLayer = L.layerGroup().addTo(map);
  let start = null, box = null;
  $('#sim-area').onclick = () => { map.dragging.disable(); $('#sim-area').textContent = 'Drag on the map…'; map._selecting = true; };
  map.on('mousedown', e => { if (!map._selecting) return; start = e.containerPoint; box = document.createElement('div'); box.className = 'select-box'; $('#sim-map').append(box); });
  map.on('mousemove', e => {
    if (!start || !box) return; const p = e.containerPoint;
    Object.assign(box.style, { left: `${Math.min(p.x, start.x)}px`, top: `${Math.min(p.y, start.y)}px`, width: `${Math.abs(p.x - start.x)}px`, height: `${Math.abs(p.y - start.y)}px` });
  });
  map.on('mouseup', e => {
    if (!start) return;
    const bounds = L.latLngBounds(map.containerPointToLatLng(start), map.containerPointToLatLng(e.containerPoint));
    const picked = mapRows.filter(r => r.cluster_id != null && bounds.contains([r.lat, r.lon])).map(r => r.hole_id);
    box?.remove(); start = null; box = null; map._selecting = false; map.dragging.enable(); $('#sim-area').textContent = 'Select area';
    if (picked.length) { selection.add(picked); toast(`${picked.length} analysed holes added to the set`); }
  });
}

async function loadMap() {
  if (!map) return;
  const data = await api(`/api/v2/map?view=${view}`).catch(() => null);
  if (!data) return;
  mapRows = data.rows.map(r => Object.fromEntries(data.columns.map((c, i) => [c, r[i]])));
  mapNames = data.cluster_names || {};
  mapGroupingSupported = data.grouping?.supported !== false;
  const cut = await recut(view, holeK).catch(() => null);
  if (cut) { mapRows.forEach(r => { r.cluster_id = cut.assignments[r.hole_id] ?? null; }); mapNames = cut.names; mapGroupingSupported = cut.supported !== false; }
  styleMap();
}

function styleMap() {
  if (!map) return;
  mapLayer.clearLayers();
  const inSet = new Set(selection.list());
  const ordered = [...mapRows].sort((a, b) => Number(inSet.has(a.hole_id)) - Number(inSet.has(b.hole_id)));
  for (const r of ordered) {
    const analysed = r.cluster_id != null, picked = inSet.has(r.hole_id);
    L.circleMarker([r.lat, r.lon], { radius: picked ? 6.5 : analysed ? 4 : 2, weight: picked ? 2.5 : .5, color: picked ? '#111' : '#fff',
      fillColor: analysed ? (mapGroupingSupported ? clusterColor(r.cluster_id) : '#9ba6ad') : '#c9d1d5', fillOpacity: analysed ? .9 : .3, opacity: analysed || picked ? 1 : .3 })
      .bindTooltip(() => `<b>${esc(r.hole_id)}</b>${analysed ? `<br>${mapGroupingSupported ? `Hole cluster ${r.cluster_id}: ${esc(mapNames[r.cluster_id] || '')}` : 'Unassigned: no supported grouping'}` : '<br><span class="tip-minerals">not in this comparison</span>'}
        ${r.swir_top ? `<div class="tip-minerals"><b>SWIR</b> ${r.swir_top.map(([m, v]) => `${esc(m)} ${pct(v)}`).join(' · ')}</div>` : ''}
        <div class="tip-minerals">${picked ? 'In the set · click to remove' : analysed ? 'Click to add to the set' : ''}</div>`, { direction: 'top', className: 'wrap-tip' })
      .on('click', () => { if (analysed || picked) selection.toggle(r.hole_id); })
      .addTo(mapLayer);
  }
  const counts = {}; mapRows.forEach(r => { if (r.cluster_id != null) counts[r.cluster_id] = (counts[r.cluster_id] || 0) + 1; });
  $('#sim-legend').innerHTML = mapGroupingSupported ? Object.keys(counts).sort((a, b) => a - b).map(id => `<span><i style="background:${clusterColor(id)}"></i>${id} · ${esc(mapNames[id] || 'Hole cluster')} <span class="muted">${num(counts[id])}</span></span>`).join('')
    + `<span class="muted">${esc(VIEWS[view])} · ${num(Object.values(counts).reduce((s, n) => s + n, 0))} holes analysed</span>` : `<span class="muted">Unassigned: no supported grouping. ${num(Object.values(counts).reduce((s, n) => s + n, 0))} holes retain distances and selection.</span>`;
}

/* --------------------------------------------------------------- matrix */
async function drawMatrix() {
  const node = $('#matrix');
  const all = selection.list();
  const holes = all.slice(0, 150);
  if (holes.length < 2) { node.innerHTML = '<p class="empty">Add at least two holes to the set.</p>'; $('#profiles').innerHTML = ''; $('#matrix-note').textContent = ''; lastMatrix = null; return; }
  loading(node);
  let d;
  try { d = await api(`/api/v2/similarity?view=${view}&holes=${encodeURIComponent(holes.join(','))}`); } catch (error) { failure(node, error); return; }
  const cut = await recut(view, holeK).catch(() => null);
  if (cut) {
    d.grouping = { supported: cut.supported !== false, status: cut.status, reason: cut.meaning };
    Object.entries(d.profiles).forEach(([id, profile]) => { profile.cluster_id = cut.assignments[id] ?? null; });
  }
  $('#matrix-note').textContent = `${VIEWS[view]}. ${d.missing.length ? `Not in this comparison: ${d.missing.slice(0, 20).join(', ')}${d.missing.length > 20 ? '…' : ''}. ` : ''}${all.length > 150 ? `Showing the first 150 of ${all.length} holes. ` : ''}Darker = more similar.`;
  lastMatrix = d;
  heatmap(node, { labels: d.holes, matrix: d.matrix, onPick: a => go(`hole/${encodeURIComponent(a)}`) });
  const rows = d.holes.map(h => {
    const p = d.profiles[h]; const s = p?.summary || {};
    const minerals = s.top_minerals || (s.swir || s.tir || {}).top_minerals || {};
    const clusters = s.top_clusters || (s.swir || {}).top_clusters || {};
    return `<tr><td><button class="link" data-hole="${esc(h)}">${esc(h)}</button></td><td>${d.grouping?.supported === false ? 'Unassigned' : p ? `<span class="swatch" style="background:${clusterColor(p.cluster_id)}"></span>${p.cluster_id}` : '–'}</td>
      <td style="min-width:200px">${stack(minerals, mineralColor)}<span class="muted small">${ranked(minerals, 3).map(([m, v]) => `${esc(m)} ${pct(v)}`).join(' · ')}</span></td>
      <td class="small">${ranked(clusters, 3).map(([n, v]) => `${esc(n)} ${pct(v)}`).join('<br>')}</td></tr>`;
  }).join('');
  $('#profiles').innerHTML = `<div class="table-wrap" style="max-height:520px"><table><thead><tr><th>Hole</th><th>Hole cluster</th><th>Main minerals</th><th>Main interval clusters</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  $('#profiles').onclick = event => { const open = event.target.closest('[data-hole]')?.dataset.hole; if (open) go(`hole/${encodeURIComponent(open)}`); };
  if (!$('#align-chart').innerHTML) drawSet();
}

/* ------------------------------------------------------------ embedding */
async function loadEmbedding() {
  embedding = await api(`/api/v2/embedding?view=${view}`).catch(() => null);
  const cut = embedding ? await recut(view, holeK).catch(() => null) : null;
  if (cut) {
    embedding.points.forEach(p => { p.cluster_id = cut.assignments[p.hole_id] ?? null; });
    embedding.card = { ...embedding.card, hole_clustering: { ...(embedding.card.hole_clustering || {}), k: cut.k, names: cut.names, sizes: cut.sizes, descriptive_container: cut.supported === false, status: cut.status, reason: cut.meaning } };
  }
  drawEmbedding();
}

function drawEmbedding() {
  const node = $('#embed');
  if (!node) return;
  if (!embedding || !embedding.points.length) { node.innerHTML = '<p class="empty">This comparison has not been analysed yet.</p>'; return; }
  const supported = !embedding.card?.hole_clustering?.descriptive_container;
  const colour = p => colourBy === 'cluster' ? (supported ? clusterColor(p.cluster_id) : '#9ba6ad') : colourBy === 'anomaly' ? scoreColor(p.anomaly_score)
    : categoryColor(colourBy === 'commodity' ? (p.commodity || '').split(',')[0] : p.tectonic_region);
  scatter(node, { points: embedding.points, colorOf: colour, highlight: new Set(selection.list()), height: 430,
    describe: p => `<b>${esc(p.hole_id)}</b><br>${supported ? `hole cluster ${p.cluster_id}` : 'Unassigned: no supported grouping'} · ${esc(p.commodity || '')}<br>${esc(p.tectonic_region || '')}<br><span class="muted">click to add/remove</span>`,
    onPick: p => selection.toggle(p.hole_id) });
  const card = embedding.card || {};
  $('#embed-card').innerHTML = `${num(card.holes)} holes · ${supported ? `${card.hole_clustering?.k ?? '–'} hole clusters` : 'Unassigned: no supported grouping (distances and neighbours retained)'}${card.agreement_with_processed ? ` · agrees with the processed SWIR comparison at ρ=${card.agreement_with_processed.spearman_rho}` : ''}`;
}

/* ---------------------------------------------------------- strip logs */
async function compareStrips() {
  const holes = (stripPick || []).slice(0, 4);
  const node = $('#strips');
  if (!holes.length) { node.innerHTML = '<p class="empty">Tick up to four holes above.</p>'; return; }
  node.innerHTML = '<p class="empty">Loading strip logs…</p>';
  const logs = await Promise.all(holes.map(h => api(`/api/v2/holes/${encodeURIComponent(h)}/striplog`).catch(() => null)));
  const depths = logs.flatMap(l => Object.values(l?.tracks || {}).flatMap(t => t.intervals.map(i => i.depth_to_m)));
  const depthMax = Math.max(10, ...depths);
  node.innerHTML = holes.map((h, i) => `<div><h3><button class="link" data-hole="${esc(h)}">${esc(h)}</button></h3><div class="strip-wrap" id="strip-${i}" style="max-height:70vh"></div></div>`).join('');
  node.onclick = event => { const open = event.target.closest('[data-hole]')?.dataset.hole; if (open) go(`hole/${encodeURIComponent(open)}`); };
  const groups = new Set();
  logs.forEach((log, i) => {
    if (!log) return;
    const tracks = [];
    for (const sensor of ['swir', 'tir']) {
      const t = log.tracks[sensor]; if (!t) continue;
      t.intervals.forEach(r => Object.keys(r.groups || {}).forEach(g => groups.add(g)));
      tracks.push({ label: sensor.toUpperCase(), width: 90, kind: 'stack', color: groupColor,
        rows: t.intervals.map(r => ({ f: r.depth_from_m, t: r.depth_to_m, parts: r.groups, row: r })),
        describe: r => ranked(r.row.top_minerals).map(([m, v]) => `${esc(m)} ${pct(v)}`).join('<br>') });
      tracks.push({ label: 'Cl.', title: 'Interval cluster', width: 16, kind: 'ribbon', rows: t.intervals.filter(r => r.cluster_id != null).map(r => ({ f: r.depth_from_m, t: r.depth_to_m, value: r.cluster_id })),
        describe: r => `Cluster ${r.value}: ${esc(t.cluster_names[`fine:${r.value}`] || '')}` });
    }
    if (tracks.length) stripLog($(`#strip-${i}`), { tracks, depthMin: 0, depthMax, pxPerMetre: Math.max(0.5, Math.min(2, 900 / depthMax)) });
    else $(`#strip-${i}`).innerHTML = '<p class="empty">No prepared data.</p>';
  });
  node.insertAdjacentHTML('beforeend', `<div class="legend" style="flex-basis:100%">${[...groups].sort().map(g => `<span><i style="background:${groupColor(g)}"></i>${esc(g)}</span>`).join('')}</div>`);
}

/* ------------------------------------------------------------ alignment */
async function alignTwo() {
  const a = $('#align-a').value, b = $('#align-b').value;
  const chart = $('#align-chart'), zones = $('#align-zones');
  if (!a || !b || a === b) { chart.innerHTML = '<p class="empty">Choose two different holes.</p>'; zones.innerHTML = ''; return; }
  chart.innerHTML = '<p class="empty">Aligning…</p>'; zones.innerHTML = '';
  let d;
  try { d = await api(`/api/v2/align?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&sensor=${$('#align-sensor').value}`); }
  catch (error) { chart.innerHTML = `<div class="notice">${esc(error.message)}</div>`; lastAlign = null; return; }
  lastAlign = d;
  alignment(chart, { a: d.a, b: d.b, stepsA: d.steps_a, stepsB: d.steps_b, path: d.path, colorOf: clusterColor });
  const shown = d.zones.filter(z => z.a_to - z.a_from >= 5);
  zones.innerHTML = `<p><b>Sequence distance ${d.distance.toFixed(3)}</b> ${verdict(d.distance)}</p>
    ${d.summary?.sentence ? `<p class="small">${esc(d.summary.sentence)}.</p>` : ''}
    <div class="table-wrap" style="max-height:520px"><table><thead><tr><th>${esc(d.a)}</th><th>matches ${esc(d.b)}</th><th>Zone in ${esc(d.a)}</th><th class="num">Same cluster ${info(`Share of the matched metres in ${d.b} that fall in the same interval cluster.`)}</th></tr></thead><tbody>
    ${shown.map(z => `<tr><td class="num">${num(z.a_from)}–${num(z.a_to)} m</td><td class="num">${num(z.b_from)}–${num(z.b_to)} m</td>
      <td><span class="swatch" style="background:${clusterColor(z.cluster_id)}"></span>${esc(z.cluster_name || `cluster ${z.cluster_id}`)}</td><td class="num">${pct(z.same_cluster_share)}</td></tr>`).join('')}</tbody></table></div>
    <p class="muted small">Zones shorter than 5 m are hidden. Compare the raw spectra of any matched zone in section 6.</p>`;
  // Matched zones become depth ranges for the spectra comparison.
  $('#spectra-range').innerHTML = '<option value="">Whole hole</option>' + shown.map((z, i) => `<option value="${i}">Zone ${i + 1}: ${esc(d.a)} ${num(z.a_from)}–${num(z.a_to)} m ↔ ${esc(d.b)} ${num(z.b_from)}–${num(z.b_to)} m</option>`).join('');
  lastAlign.shown = shown;
}

function verdict(distance) {
  if (distance < 0.15) return '<span class="chip accent">very similar sequence</span>';
  if (distance < 0.25) return '<span class="chip">partly similar sequence</span>';
  return '<span class="chip warn">different sequence</span>';
}

// Most similar pair in the current matrix: the natural default to align.
function closestPair() {
  const d = lastMatrix; if (!d || d.holes.length < 2) return [];
  let best = [0, 1], low = Infinity;
  d.matrix.forEach((row, i) => row.forEach((v, j) => { if (j > i && v != null && v < low) { low = v; best = [i, j]; } }));
  return best.map(i => d.holes[i]);
}

/* -------------------------------------------------------------- spectra */
const FEATURE_ROWS = [['aloh_2200_pos', 'Al-OH position (nm)', 1], ['feoh_2250_pos', 'Fe-OH position (nm)', 1], ['mgoh_2330_pos', 'Mg-OH position (nm)', 1],
  ['aloh_2200_depth', 'Al-OH depth', 3], ['h2o_1900_depth', 'Water (1900 nm) depth', 3], ['fe3_900_depth', 'Ferric iron depth', 3], ['albedo', 'Brightness', 3]];

async function compareSpectra() {
  const sensor = $('#spectra-sensor').value, range = $('#spectra-range').value;
  const chart = $('#spectra-chart'), table = $('#spectra-table');
  let items;
  if (range !== '' && lastAlign?.shown?.[range]) {
    const z = lastAlign.shown[range];
    items = [`${lastAlign.a}@${z.a_from}-${z.a_to}`, `${lastAlign.b}@${z.b_from}-${z.b_to}`];
  } else items = (spectraPick || []).slice(0, 6);
  if (!items.length) { chart.innerHTML = '<p class="empty">Tick holes above, or align two holes and pick a matched zone.</p>'; table.innerHTML = ''; return; }
  loading(chart, 'Loading spectra…'); table.innerHTML = '';
  let d;
  try { d = await api(`/api/v2/spectra?holes=${encodeURIComponent(items.join(','))}&sensor=${sensor}`); } catch (error) { failure(chart, error); return; }
  const ok = d.items.filter(i => i.mean);
  const colour = i => clusterColor(d.items.indexOf(i));
  const label = i => `${i.hole_id}${i.from_m != null ? ` ${num(i.from_m)}–${num(i.to_m)} m` : ''}`;
  chart.innerHTML = ok.length ? lineChart({ x: d.wavelengths, bands: ok.map(i => ({ low: i.p10, high: i.p90, color: colour(i), opacity: 0.1 })),
    series: ok.map(i => ({ values: i.mean, color: colour(i), name: `${label(i)} · ${num(i.metres)} m`, width: 1.8 })),
    marks: sensor === 'nir' ? NIR_MARKS : [], xLabel: 'Wavelength (nm)', width: 760, height: 320 }) : '<p class="empty">None of these holes has raw spectra for this sensor.</p>';
  const missing = d.items.filter(i => !i.mean);
  const angles = ok.length > 1 ? `<h3>Spectral angle (°) ${info(d.method)}</h3><table><thead><tr><th></th>${ok.map(i => `<th class="num"><span class="swatch" style="background:${colour(i)}"></span></th>`).join('')}</tr></thead><tbody>
    ${ok.map(a => `<tr><td><span class="swatch" style="background:${colour(a)}"></span>${esc(label(a))}</td>${ok.map(b => { const v = d.spectral_angle_deg[d.items.indexOf(a)][d.items.indexOf(b)]; return `<td class="num" style="${a !== b && v != null ? `background:${v < 3 ? 'var(--accent-soft)' : v > 10 ? 'var(--warn-soft)' : 'transparent'}` : ''}">${a === b ? '–' : num(v, 1)}</td>`; }).join('')}</tr>`).join('')}</tbody></table>` : '';
  const features = sensor === 'nir' && ok.length ? `<h3 style="margin-top:12px">Absorption features</h3><table><thead><tr><th>Feature</th>${ok.map(i => `<th class="num"><span class="swatch" style="background:${colour(i)}"></span></th>`).join('')}</tr></thead><tbody>
    ${FEATURE_ROWS.map(([key, name, digits]) => `<tr><td data-tip="${esc(d.features_meaning?.[key] || name)}">${esc(name)}</td>${ok.map(i => `<td class="num">${num(i.features?.[key], digits)}</td>`).join('')}</tr>`).join('')}</tbody></table>
    <p class="muted small">Al-OH below ~2198 nm = sodic/muscovitic white mica; above ~2206 nm = phengitic. Deeper absorptions = more of that mineral.</p>` : '';
  table.innerHTML = angles + features + (missing.length ? `<p class="muted small">${missing.map(i => `${esc(i.hole_id)}: ${esc(i.missing)}`).join('<br>')}</p>` : '');
}

export function leave() {
  if (unsubscribe) { unsubscribe(); unsubscribe = null; }
  if (map) { map.remove(); map = null; }
}
