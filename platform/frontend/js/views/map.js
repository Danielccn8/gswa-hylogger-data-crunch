import { $, api, esc, num, pct, bars, ranked, toast, loading } from '../util.js';
import { selection } from '../state.js';
import { clusterColor, categoryColor, scoreColor, mineralColor } from '../palette.js';
import { lineChart } from '../charts.js';
import { go } from '../main.js';
import { kSelect, recut } from '../holeclusters.js';
import { PROCESSED, RAW, statusText, TERMS, NIR_MARKS, info } from '../glossary.js';

const STATUS = {
  prepared: ['Mineral data ready', '#0f6e68'],
  raw: ['Mineral data + raw spectra', '#7b4173'],
  no_logs: ['Scanned, no public mineral logs', '#e6a23c'],
  not_fetched: ['Not downloaded yet', '#9aa5ab'],
  index_only: ['In the package index only', '#c9d1d5'],
};
const ANALYSES = {
  combined: 'Mineral mix · SWIR + TIR', swir: 'Mineral mix · SWIR', tir: 'Mineral mix · TIR',
  seq_combined: 'Down-hole sequence · SWIR + TIR', seq_swir: 'Down-hole sequence · SWIR', seq_tir: 'Down-hole sequence · TIR',
  raw_nir: 'Raw spectra · NIR/SWIR', raw_tir: 'Raw spectra · TIR',
};
const ANALYTES = ['Au', 'Cu', 'Zn', 'Pb', 'Ni', 'U', 'Th', 'As', 'Cr', 'S', 'K2O', 'MgO', 'CaO', 'Na2O', 'Fe2O3T', 'SiO2', 'Al2O3'];
let map, layer, focusLayer, rows = [], colourBy = 'status', view = 'combined', holeK = 'auto', clusterNames = {};
let chem = null, analyte = 'U', threshold = 100;
let focus = null, simView = 'combined', groupingSupported = true;
const markers = new Map();

function statusOf(r) {
  if (String(r.raw_status).startsWith('summarised') && r.processed_status === 'prepared') return 'raw';
  if (r.processed_status === 'prepared') return 'prepared';
  if (r.processed_status === 'no_logs') return 'no_logs';
  if (!r.nvcl_available) return 'index_only';
  return 'not_fetched';
}
async function loadChem() {
  if (colourBy !== 'chem') return;
  const d = await api(`/api/v2/chemistry?analyte=${analyte}&threshold=${threshold}`);
  chem = { ...d, byHole: Object.fromEntries(d.holes.map(h => [h.hole_id, h])) };
}
function chemColour(r) {
  const h = chem?.byHole[r.hole_id];
  if (!h) return '#d5dcdf';
  if (!h.above) return '#9fb7c2';
  const top = Math.log10(Math.max(...chem.holes.map(x => x.max || 0), threshold * 10));
  const t = Math.max(0, Math.min(1, (Math.log10(h.max) - Math.log10(Math.max(threshold, 1e-6))) / Math.max(top - Math.log10(Math.max(threshold, 1e-6)), 1e-6)));
  return scoreColor(0.5 + t / 2);
}
function colourOf(r) {
  if (colourBy === 'chem') return chemColour(r);
  if (colourBy === 'status') return STATUS[statusOf(r)][1];
  if (colourBy === 'cluster') return r.cluster_id == null || !groupingSupported ? '#d5dcdf' : clusterColor(r.cluster_id);
  if (colourBy === 'anomaly') return r.anomaly_score == null ? '#d5dcdf' : scoreColor(r.anomaly_score);
  if (colourBy === 'assays') return r.n_assay > 0 ? '#0f6e68' : '#d5dcdf';
  if (colourBy === 'tsa') return tsaColour(r.tsa_agreement);
  return categoryColor(colourBy === 'commodity' ? (r.commodity || '').split(',')[0] : r.tectonic_region);
}
const TSA_BANDS = [[0.97, '#2f8f4e', '≥ 97% agree'], [0.9, '#9cc96b', '90–97%'], [0.7, '#e6a23c', '70–90%'], [0, '#c0392b', 'below 70%']];
function tsaColour(value) { return value == null ? '#d5dcdf' : TSA_BANDS.find(([low]) => value >= low)[1]; }
function syncControls() {
  $('#chem-controls').hidden = colourBy !== 'chem';
  $('#analysis-controls').hidden = !['cluster', 'anomaly'].includes(colourBy);
  $('#k-wrap').hidden = colourBy !== 'cluster';
}

export async function render(root, [focusId] = []) {
  root.innerHTML = `
  <div class="page-head"><div><h1>Drill holes across Western Australia</h1>
    <p class="muted">Every HyLogger-scanned hole in the catalogue and the GSWA package. Hover a hole for its main minerals; click it to see everything it contains and its most similar holes.</p></div></div>
  <div id="metrics" class="metrics"></div>
  <div class="grid cols-main-side">
    <section>
      <div class="toolbar">
        <label class="field"><span>Colour by ${info('What the marker colour means. The legend under the map explains each colour.')}</span><select id="colour">
          <option value="status">Data available</option><option value="cluster">Hole cluster (similar holes)</option>
          <option value="anomaly">Unusualness</option><option value="commodity">Target commodity (company)</option>
          <option value="region">Tectonic region</option><option value="assays">Has assays</option><option value="chem">Assay above a threshold</option>
          <option value="tsa">Machine vs analyst (SWIR)</option></select></label>
        <span id="analysis-controls" class="row gap" hidden><label class="field"><span>Based on ${info(TERMS.analysis)}</span><select id="analysis">
          ${Object.entries(ANALYSES).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
          <span id="k-wrap">${kSelect('hole-k', holeK)}</span></span>
        <span id="chem-controls" class="row gap" hidden><label class="field"><span>Analyte ${info('Holes are coloured by their highest assay of this element or oxide; grey = no assays for it.')}</span><select id="analyte">${ANALYTES.map(a => `<option ${a === analyte ? 'selected' : ''}>${a}</option>`).join('')}</select></label>
          <label class="field">Threshold<input id="threshold" type="number" min="0" step="any" value="${threshold}" style="width:100px"></label></span>
        <label class="field" style="flex:1;min-width:160px">Filter<input id="filter" type="search" placeholder="Hole, project, operator or commodity"></label>
        <button id="area" class="secondary" type="button" title="Drag a rectangle on the map to add holes to the selection">Select area</button>
      </div>
      <div id="map" aria-label="Map of drill holes"></div>
      <div id="legend" class="legend"></div>
    </section>
    <aside class="panel side-panel" id="side"><h2>Hole</h2><p class="muted">Hover a marker for its main minerals. Click it to see its SWIR, TIR and raw-spectra summary, and to light up its most similar holes on the map.</p></aside>
  </div>`;
  const summary = await api('/api/v2/summary');
  const c = summary.counts;
  const intervals = Object.fromEntries(summary.intervals.map(r => [r.sensor, r]));
  const metres = (sensor, label) => [`Metres of ${label} data`, num(intervals[sensor]?.intervals), `in ${num(intervals[sensor]?.holes)} holes · ${num(intervals[sensor]?.readings)} readings`,
    `${TERMS.interval} ${TERMS[sensor]}`];
  $('#metrics').innerHTML = [
    ['Holes in the catalogue', num(c.holes), `${num(c.nvcl)} scanned by a HyLogger`, 'Every drill hole with coordinates in the NVCL catalogue or the GSWA package. Not all of them have published HyLogger data.'],
    ['Holes with mineral data', num(c.prepared), `downloaded & processed · ${num(c.no_logs)} scanned without public logs`, PROCESSED.prepared[1]],
    ['Holes with raw spectra', num(c.raw_summarised), `of ${num(c.raw)} raw packages published`, 'Raw spectra downloaded and reduced to one median spectrum per metre (Final Mask applied), with absorption features. Independent of the TSG mineral interpretation.'],
    metres('swir', 'SWIR'), metres('tir', 'TIR'),
    ['Holes with assays', num(c.with_assays), `${num(c.with_geology)} with logged geology`, TERMS.assays],
  ].map(([a, b, s, tip]) => `<div class="metric" tabindex="0" data-tip="${esc(tip)}"><span>${a} <span class="info" aria-hidden="true">i</span></span><strong>${b}</strong><small>${s}</small></div>`).join('');
  $('#colour').value = colourBy; $('#analysis').value = view; syncControls();
  $('#colour').onchange = async e => { colourBy = e.target.value; syncControls(); await loadChem(); draw(); };
  $('#analyte').onchange = async e => { analyte = e.target.value; await loadChem(); draw(); };
  $('#threshold').onchange = async e => { threshold = Math.max(0, Number(e.target.value) || 0); await loadChem(); draw(); };
  $('#analysis').onchange = async e => { view = e.target.value; await load(); draw(); };
  $('#filter').oninput = () => draw();
  $('#hole-k').onchange = async e => { holeK = e.target.value; await load(); draw(); };
  document.addEventListener('keydown', onKey);
  setupMap();
  await load();
  draw();
  // #/map/<hole>: open that hole and frame it with its most similar holes.
  const target = focusId && rows.find(r => r.hole_id === focusId);
  if (target) { await side(target); frame(); }
}

function frame() {
  if (!map || !focus) return;
  const points = [[focus.hole.lat, focus.hole.lon], ...focus.items.filter(i => i.lat != null).map(i => [i.lat, i.lon])];
  map.fitBounds(L.latLngBounds(points).pad(0.25), { maxZoom: 9, animate: false });
}

function onKey(event) { if (event.key === 'Escape' && focus) clearFocus(); }

async function load() {
  const data = await api(`/api/v2/map?view=${view}`);
  rows = data.rows.map(r => Object.fromEntries(data.columns.map((c, i) => [c, r[i]])));
  clusterNames = data.cluster_names || {};
  groupingSupported = data.grouping?.supported !== false;
  const cut = await recut(view, holeK).catch(() => null);
  if (cut) { rows.forEach(r => { r.cluster_id = cut.assignments[r.hole_id] ?? null; }); clusterNames = cut.names; groupingSupported = cut.supported !== false; }
}

function setupMap() {
  if (!globalThis.L) { $('#map').innerHTML = '<p class="empty">Map library unavailable.</p>'; return; }
  map = L.map('map', { preferCanvas: true, boxZoom: false }).setView([-25.5, 121.5], 5);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors', maxZoom: 17 })
    .on('tileerror', () => { $('#legend').dataset.offline = '1'; }).addTo(map);
  layer = L.layerGroup().addTo(map);
  focusLayer = L.layerGroup().addTo(map);
  let start = null, box = null;
  $('#area').onclick = () => { map.dragging.disable(); $('#area').textContent = 'Drag on the map…'; map._selecting = true; };
  map.on('mousedown', e => { if (!map._selecting) return; start = e.containerPoint; box = document.createElement('div'); box.className = 'select-box'; $('#map').append(box); });
  map.on('mousemove', e => {
    if (!start || !box) return; const p = e.containerPoint;
    Object.assign(box.style, { left: `${Math.min(p.x, start.x)}px`, top: `${Math.min(p.y, start.y)}px`, width: `${Math.abs(p.x - start.x)}px`, height: `${Math.abs(p.y - start.y)}px` });
  });
  map.on('mouseup', e => {
    if (!start) return;
    const bounds = L.latLngBounds(map.containerPointToLatLng(start), map.containerPointToLatLng(e.containerPoint));
    const picked = visible().filter(r => bounds.contains([r.lat, r.lon])).map(r => r.hole_id);
    box?.remove(); start = null; box = null; map._selecting = false; map.dragging.enable(); $('#area').textContent = 'Select area';
    if (picked.length) { selection.add(picked); toast(`${picked.length} holes added to the selection`); }
  });
}

function visible() {
  const q = $('#filter')?.value.trim().toLowerCase();
  return rows.filter(r => !q || `${r.hole_id} ${r.name} ${r.project} ${r.operator} ${r.commodity}`.toLowerCase().includes(q));
}

const mineralLine = (label, top) => top?.length ? `<div class="tip-minerals"><b>${label}</b> ${top.map(([m, v]) => `${esc(m)} ${pct(v)}`).join(' · ')}</div>` : '';
function hoverText(r) {
  const h = chem?.byHole[r.hole_id];
  return `<b>${esc(r.hole_id)}</b>${r.name && r.name !== r.hole_id ? ` <span class="tip-minerals">${esc(r.name)}</span>` : ''}
    ${mineralLine('SWIR', r.swir_top)}${mineralLine('TIR', r.tir_top)}
    ${!r.swir_top && !r.tir_top ? `<div class="tip-minerals">${esc(statusText(PROCESSED, r.processed_status)[0])}</div>` : ''}
    ${colourBy === 'cluster' && r.cluster_id != null ? `<div>${groupingSupported ? `Hole cluster ${r.cluster_id}: ${esc(clusterNames[r.cluster_id] || '')}` : 'Unassigned: no supported grouping'}</div>` : ''}
    ${colourBy === 'anomaly' && r.anomaly_score != null ? `<div>Unusualness ${pct(r.anomaly_score)}</div>` : ''}
    ${r.tsa_agreement != null ? `<div class="tip-minerals">Machine vs analyst (SWIR): first mineral agrees in ${pct(r.tsa_agreement)} of readings</div>` : ''}
    ${colourBy === 'chem' && h ? `<div>max ${analyte} ${num(h.max, 2)} ${chem.unit} · ${h.above} samples &gt; ${threshold}</div>` : ''}
    <div class="tip-minerals">Company target: ${esc(r.commodity || 'unknown')}</div>`;
}

function draw() {
  if (!map) return;
  layer.clearLayers(); markers.clear();
  const shown = visible();
  const ranks = focus ? new Map(focus.items.map((it, i) => [it.hole_id, i + 1])) : null;
  for (const r of shown) {
    const analysed = colourBy === 'cluster' ? r.cluster_id != null : colourBy === 'anomaly' ? r.anomaly_score != null : colourBy === 'chem' ? !!chem?.byHole[r.hole_id]?.above : colourBy === 'tsa' ? r.tsa_agreement != null : true;
    const picked = selection.has(r.hole_id);
    const lit = !focus || ranks.has(r.hole_id) || focus.hole.hole_id === r.hole_id;
    const radius = analysed ? (colourBy === 'chem' ? 6.5 : r.processed_status === 'prepared' ? 5 : 3.5) : 2.5;
    const marker = L.circleMarker([r.lat, r.lon], {
      radius, weight: picked ? 2 : .6, color: picked ? '#111' : '#ffffff', fillColor: colourOf(r),
      fillOpacity: lit ? (analysed ? .92 : .35) : .12, opacity: lit ? (analysed ? 1 : .4) : .12,
    }).on('click', () => side(r)).bindTooltip(() => hoverText(r), { direction: 'top', offset: [0, -4], className: 'wrap-tip' }).addTo(layer);
    markers.set(r.hole_id, marker);
  }
  drawFocus();
  legend(shown);
}

// Similar holes rise: they grow, get a numbered pin and a line to the chosen hole.
function drawFocus() {
  focusLayer.clearLayers();
  if (!focus) return;
  const origin = [focus.hole.lat, focus.hole.lon];
  focus.items.forEach((it, i) => {
    if (it.lat == null) return;
    L.polyline([origin, [it.lat, it.lon]], { color: '#0f6e68', weight: 1.5, opacity: .55, dashArray: '4 4', interactive: false }).addTo(focusLayer);
  });
  focus.items.forEach((it, i) => {
    if (it.lat == null) return;
    const marker = markers.get(it.hole_id);
    if (marker) { marker.bringToFront(); grow(marker, marker.getRadius(), 9, i); }
    L.marker([it.lat, it.lon], { icon: L.divIcon({ className: 'rank-pin', html: `<span style="--i:${i}">${i + 1}</span>`, iconSize: [24, 24] }), zIndexOffset: 1000 - i, keyboard: false })
      .bindTooltip(`<b>${i + 1}. ${esc(it.hole_id)}</b> · ${pct(1 - it.distance)} similar<br>${(it.why || []).slice(0, 2).map(esc).join('<br>')}`, { direction: 'top', offset: [0, -10], className: 'wrap-tip' })
      .on('click', () => { const r = rows.find(x => x.hole_id === it.hole_id); if (r) side(r); })
      .on('mouseover', () => $(`[data-sim="${CSS.escape(it.hole_id)}"]`)?.classList.add('on'))
      .on('mouseout', () => $(`[data-sim="${CSS.escape(it.hole_id)}"]`)?.classList.remove('on'))
      .addTo(focusLayer);
  });
  L.marker(origin, { icon: L.divIcon({ className: 'rank-pin focus', html: '<span>●</span>', iconSize: [28, 28] }), zIndexOffset: 2000, interactive: false }).addTo(focusLayer);
}

function grow(marker, from, to, delay) {
  const started = performance.now() + delay * 40;
  const step = now => {
    const t = Math.max(0, Math.min(1, (now - started) / 400));
    marker.setRadius(from + (to - from) * (1 - (1 - t) ** 3));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function clearFocus() { focus = null; draw(); }

function legend(shown) {
  let items = [];
  if (colourBy === 'status') items = Object.entries(STATUS).map(([k, [label, color]]) => [label, color, shown.filter(r => statusOf(r) === k).length]);
  else if (colourBy === 'cluster' && !groupingSupported) items = [['Unassigned: no supported grouping', '#d5dcdf', shown.filter(r => r.cluster_id != null).length]];
  else if (colourBy === 'cluster') items = [...new Set(shown.map(r => r.cluster_id).filter(v => v != null))].sort((a, b) => a - b).map(id => [`${id} · ${clusterNames[id] || 'Hole cluster'}`, clusterColor(id), shown.filter(r => r.cluster_id === id).length]);
  else if (colourBy === 'anomaly') items = [['Typical', scoreColor(.5)], ['Unusual', scoreColor(.85)], ['Most unusual', scoreColor(1)], ['Not analysed', '#d5dcdf']];
  else if (colourBy === 'assays') items = [['Has assays', '#0f6e68'], ['No assays', '#d5dcdf']];
  else if (colourBy === 'tsa') items = [...TSA_BANDS.map(([low, colour, label]) => [label, colour, shown.filter(r => r.tsa_agreement != null && tsaColour(r.tsa_agreement) === colour).length]), ['Not compared', '#d5dcdf']];
  else if (colourBy === 'chem') items = [[`No ${analyte} assays`, '#d5dcdf'], [`Max ≤ ${threshold} ${chem?.unit || ''}`, '#9fb7c2'], [`Above ${threshold}`, scoreColor(.6)], ['Highest', scoreColor(1)], [`${num(chem?.holes_above)} of ${num(chem?.holes_with_assays)} holes above`, 'transparent']];
  else {
    const key = colourBy === 'commodity' ? r => (r.commodity || '').split(',')[0] : r => r.tectonic_region;
    const counts = {}; shown.forEach(r => { const k = key(r) || 'Unknown'; counts[k] = (counts[k] || 0) + 1; });
    items = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 14).map(([k, n]) => [k, categoryColor(k === 'Unknown' ? '' : k), n]);
  }
  const note = colourBy === 'cluster' || colourBy === 'anomaly' ? ` · based on ${ANALYSES[view]}` : colourBy === 'commodity' ? ` · ${TERMS.commodity}` : colourBy === 'tsa' ? ' · share of SWIR readings where the automatic (sTSAS) and reviewed (uTSAS) first mineral agree' : '';
  $('#legend').innerHTML = `${focus ? `<span class="chip accent">Showing the ${focus.items.length} holes most similar to ${esc(focus.hole.hole_id)} (${esc(ANALYSES[focus.view])}) <button type="button" id="clear-focus" aria-label="Clear highlight">×</button></span>` : ''}
    ${items.map(([label, color, n]) => `<span><i style="background:${color}"></i>${esc(label)}${n != null ? ` <span class="muted">${num(n)}</span>` : ''}</span>`).join('')}
    <span class="muted">${num(shown.length)} shown${esc(note)}${$('#legend').dataset.offline ? ' · basemap offline' : ''}</span>`;
  const clear = $('#clear-focus'); if (clear) clear.onclick = clearFocus;
}

function whiteMica(position, depth) {
  if (position == null) return '';
  if (depth != null && depth < 0.02) return 'weak Al-OH absorption: little white mica or kaolin';
  if (position < 2198) return 'sodic to muscovitic white mica (paragonite–muscovite)';
  if (position <= 2206) return 'muscovitic white mica';
  return 'phengitic (Fe/Mg-rich) white mica';
}

async function side(r) {
  const node = $('#side');
  node.innerHTML = `<h2>${esc(r.hole_id)}</h2><p class="muted">Loading…</p>`;
  const rawDone = String(r.raw_status).startsWith('summarised');
  const [detail, nir, tir] = await Promise.all([
    api(`/api/v2/holes/${encodeURIComponent(r.hole_id)}`),
    rawDone ? api(`/api/v2/spectra?holes=${encodeURIComponent(r.hole_id)}&sensor=nir`).catch(() => null) : null,
    rawDone ? api(`/api/v2/spectra?holes=${encodeURIComponent(r.hole_id)}&sensor=tir`).catch(() => null) : null,
  ]);
  const h = detail.hole;
  const [pLabel, pText] = statusText(PROCESSED, h.processed_status);
  const [rLabel, rText] = statusText(RAW, h.raw_status, h.raw_available);
  const minerals = (sensor, label) => {
    const top = detail.profiles[sensor]?.summary?.top_minerals;
    const metres = detail.sensors[sensor];
    return top ? `<h3 style="margin-top:12px">${label} minerals ${info(TERMS[sensor])}</h3>${bars(ranked(top, 6), mineralColor)}
      <p class="muted small">${metres ? `${num(metres.with_minerals)} of ${num(metres.intervals)} metres with a diagnostic reading` : ''}</p>` : '';
  };
  const spectrum = (d, label) => {
    const item = d?.items?.[0];
    if (!item?.mean) return '';
    const aloh = item.features?.aloh_2200_pos;
    return `<h3 style="margin-top:12px">Raw ${label} spectrum ${info(d.method)}</h3>
      ${lineChart({ x: d.wavelengths, bands: [{ low: item.p10, high: item.p90, color: '#0f6e68', name: '10–90% of metres' }], series: [{ values: item.mean, color: '#0f6e68', name: `mean of ${num(item.metres)} m` }],
        marks: label === 'NIR/SWIR' ? NIR_MARKS : [], xLabel: 'Wavelength (nm)', width: 340, height: 160 })}
      ${label === 'NIR/SWIR' && aloh ? `<p class="small">Al-OH absorption at <b>${num(aloh, 1)} nm</b>: ${esc(whiteMica(aloh, item.features.aloh_2200_depth))}.</p>` : ''}`;
  };
  node.innerHTML = `<h2>${esc(h.hole_id)} <span class="muted small">${esc(h.name && h.name !== h.hole_id ? h.name : '')}</span></h2>
    <p class="muted small">Company target: ${esc(h.commodity || 'unknown')} ${info(TERMS.commodity)} · ${esc(h.tectonic_region || 'Region unknown')} · ${esc(h.operator || '')}</p>
    <section><h3>What data this hole has</h3><div class="data-status">
      <div><span>Mineral logs</span><b data-tip="${esc(pText)}" tabindex="0">${esc(pLabel)} <span class="info" aria-hidden="true">i</span></b></div>
      <div><span>Raw spectra</span><b data-tip="${esc(rText)}" tabindex="0">${esc(rLabel)} <span class="info" aria-hidden="true">i</span></b></div>
      <div><span>Assays</span>${h.n_assay ? `${num(h.n_assay)} intervals` : 'none linked'}</div>
      <div><span>Logged geology</span>${h.n_geology ? `${num(h.n_geology)} intervals` : 'none linked'}</div>
      <div><span>Length</span>${num(h.length_m)} m</div></div></section>
    ${minerals('swir', 'SWIR') || minerals('tir', 'TIR') ? `<section>${minerals('swir', 'SWIR')}${minerals('tir', 'TIR')}</section>` : '<section><p class="notice">No mineral data yet. Use Data to download and process it.</p></section>'}
    ${nir?.items?.[0]?.mean || tir?.items?.[0]?.mean ? `<section>${spectrum(nir, 'NIR/SWIR')}${spectrum(tir, 'TIR')}</section>` : ''}
    <section><div class="row gap" style="justify-content:space-between"><h3 style="margin:0">Most similar holes ${info(TERMS.similarity)}</h3>
      <label class="field"><select id="sim-view" aria-label="Similarity based on">${Object.entries(ANALYSES).map(([k, v]) => `<option value="${k}" ${k === simView ? 'selected' : ''}>${v}</option>`).join('')}</select></label></div>
      <div id="similar" style="margin-top:8px"></div></section>
    <div class="row gap" style="margin-top:12px">
      <button type="button" data-open="${esc(h.hole_id)}">Open strip log</button>
      <button type="button" class="secondary" id="add">${selection.has(h.hole_id) ? 'Remove from selection' : 'Add to selection'}</button>
      <button type="button" class="secondary" id="compare" hidden>Compare these holes</button>
    </div>`;
  node.onclick = event => {
    const open = event.target.closest('[data-open]')?.dataset.open;
    if (open) go(`hole/${encodeURIComponent(open)}`);
    if (event.target.id === 'add') { selection.toggle(h.hole_id); event.target.textContent = selection.has(h.hole_id) ? 'Remove from selection' : 'Add to selection'; draw(); }
    if (event.target.id === 'compare' && focus) { selection.set([h.hole_id, ...focus.items.map(i => i.hole_id)]); go(`similarity/${encodeURIComponent(h.hole_id)}`); }
  };
  $('#sim-view').onchange = e => { simView = e.target.value; similar(r); };
  await similar(r);
}

async function similar(r) {
  const node = $('#similar'); loading(node, 'Finding similar holes…');
  const d = await api(`/api/v2/holes/${encodeURIComponent(r.hole_id)}/similar?view=${simView}&limit=10`).catch(() => ({ items: [] }));
  if (!d.items.length) { node.innerHTML = `<p class="muted small">This hole is not in the ${esc(ANALYSES[simView])} analysis.</p>`; focus = null; draw(); return; }
  focus = { hole: r, items: d.items, view: simView };
  draw();
  node.innerHTML = `<div class="similar-list">${d.items.map((s, i) => `<div class="similar-item" data-sim="${esc(s.hole_id)}"><span class="rank">${i + 1}</span>
      <div><button class="link" data-open="${esc(s.hole_id)}">${esc(s.hole_id)}</button> <span class="muted small">${pct(1 - s.distance)} similar</span>
      <ul>${(s.why || []).slice(0, 3).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div></div>`).join('')}</div>
    <p class="muted small" style="margin-top:6px">${esc(d.distance || '')}. Numbers on the map show the ranking; press Esc to clear.</p>`;
  $('#compare').hidden = false;
  node.querySelectorAll('[data-sim]').forEach(item => {
    const marker = markers.get(item.dataset.sim);
    item.onmouseenter = () => marker?.setStyle({ weight: 3, color: '#111' });
    item.onmouseleave = () => marker?.setStyle({ weight: .6, color: '#ffffff' });
  });
}

export function leave() {
  document.removeEventListener('keydown', onKey);
  focus = null;
  if (map) { map.remove(); map = null; }
}
