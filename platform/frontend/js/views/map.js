import { $, api, esc, num, pct, bars, ranked, toast } from '../util.js';
import { selection } from '../state.js';
import { clusterColor, categoryColor, scoreColor, mineralColor } from '../palette.js';
import { go } from '../main.js';
import { kSelect, recut } from '../holeclusters.js';

const STATUS = {
  prepared: ['Processed logs prepared', '#0f6e68'],
  raw: ['Raw spectra summarised', '#7b4173'],
  no_logs: ['Scanned, no public processed logs', '#e6a23c'],
  not_fetched: ['Not yet downloaded', '#9aa5ab'],
  index_only: ['Package index only', '#c9d1d5'],
};
let map, layer, data, columns, rows, colourBy = 'status', view = 'combined', holeK = 'auto', clusterNames = {};
let chem = null, analyte = 'U', threshold = 100;
const ANALYTES = ['Au', 'Cu', 'Zn', 'Pb', 'Ni', 'U', 'Th', 'As', 'Cr', 'S', 'K2O', 'MgO', 'CaO', 'Na2O', 'Fe2O3T', 'SiO2', 'Al2O3'];

function statusOf(r) {
  if (String(r.raw_status).startsWith('summarised')) return 'raw';
  if (r.processed_status === 'prepared') return 'prepared';
  if (r.processed_status === 'no_logs') return 'no_logs';
  if (!r.nvcl_available) return 'index_only';
  return 'not_fetched';
}
async function loadChem() {
  $('#chem-controls').hidden = colourBy !== 'chem';
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
  if (colourBy === 'cluster') return r.cluster_id == null ? '#d5dcdf' : clusterColor(r.cluster_id);
  if (colourBy === 'anomaly') return r.anomaly_score == null ? '#d5dcdf' : scoreColor(r.anomaly_score);
  if (colourBy === 'assays') return r.n_assay > 0 ? '#0f6e68' : '#d5dcdf';
  return categoryColor(colourBy === 'commodity' ? (r.commodity || '').split(',')[0] : r.tectonic_region);
}

export async function render(root) {
  root.innerHTML = `
  <div class="page-head"><div><h1>Drill holes across Western Australia</h1>
    <p class="muted">Every HyLogger-scanned hole in the catalogue and the GSWA package. Colour by data, geology or analysis; select an area to compare or download.</p></div></div>
  <div id="metrics" class="metrics"></div>
  <div class="grid cols-main-side">
    <section>
      <div class="toolbar">
        <label class="field">Colour by<select id="colour">
          <option value="status">Data available</option><option value="cluster">Hole cluster</option>
          <option value="anomaly">Unusualness</option><option value="commodity">Commodity</option>
          <option value="region">Tectonic region</option><option value="assays">Has assays</option><option value="chem">Assay above threshold</option></select></label>
        <span id="chem-controls" class="row gap" hidden><label class="field">Analyte<select id="analyte">${ANALYTES.map(a => `<option ${a === analyte ? 'selected' : ''}>${a}</option>`).join('')}</select></label>
        <label class="field">Threshold<input id="threshold" type="number" min="0" step="any" value="${threshold}" style="width:100px"></label></span>
        <label class="field">Analysis<select id="analysis">
          <option value="combined">Mix · SWIR + TIR</option><option value="swir">Mix · SWIR</option><option value="tir">Mix · TIR</option>
          <option value="seq_combined">Sequence · SWIR + TIR</option><option value="seq_swir">Sequence · SWIR</option><option value="seq_tir">Sequence · TIR</option>
          <option value="raw_nir">Raw NIR/SWIR spectra</option><option value="raw_tir">Raw TIR spectra</option></select></label>
        ${kSelect('hole-k', holeK)}
        <label class="field" style="flex:1;min-width:160px">Filter<input id="filter" type="search" placeholder="Hole, project, operator or commodity"></label>
        <button id="area" class="secondary" type="button" title="Drag a rectangle on the map to add holes to the selection">Select area</button>
      </div>
      <div id="map" aria-label="Map of drill holes"></div>
      <div id="legend" class="legend"></div>
    </section>
    <aside class="panel" id="side"><h2>Hole</h2><p class="muted">Click a marker to see what the hole contains, its cluster and the most similar holes.</p></aside>
  </div>`;
  const summary = await api('/api/v2/summary');
  const c = summary.counts;
  const intervals = Object.fromEntries(summary.intervals.map(r => [r.sensor, r]));
  $('#metrics').innerHTML = [
    ['Holes registered', num(c.holes), `${num(c.nvcl)} with NVCL datasets`],
    ['Processed & prepared', num(c.prepared), `${num(c.no_logs)} scanned but no public logs`],
    ['SWIR intervals', num(intervals.swir?.intervals), `${num(intervals.swir?.holes)} holes · 1 m`],
    ['TIR intervals', num(intervals.tir?.intervals), `${num(intervals.tir?.holes)} holes · 1 m`],
    ['Raw spectra summarised', num(c.raw_summarised), `${num(c.raw)} raw packages exist`],
    ['Holes with assays', num(c.with_assays), `${num(c.with_geology)} with logged geology`],
  ].map(([a, b, s]) => `<div class="metric"><span>${a}</span><strong>${b}</strong><small>${s}</small></div>`).join('');
  $('#colour').value = colourBy; $('#analysis').value = view;
  $('#colour').onchange = async e => { colourBy = e.target.value; await loadChem(); draw(); };
  $('#analyte').onchange = async e => { analyte = e.target.value; await loadChem(); draw(); };
  $('#threshold').onchange = async e => { threshold = Math.max(0, Number(e.target.value) || 0); await loadChem(); draw(); };
  $('#analysis').onchange = async e => { view = e.target.value; await load(); draw(); };
  $('#filter').oninput = () => draw();
  $('#hole-k').onchange = async e => { holeK = e.target.value; await load(); if (colourBy !== 'cluster') { colourBy = 'cluster'; $('#colour').value = 'cluster'; } draw(); };
  setupMap();
  await load();
  draw();
}

async function load() {
  data = await api(`/api/v2/map?view=${view}`);
  columns = data.columns;
  rows = data.rows.map(r => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
  clusterNames = data.cluster_names || {};
  const cut = await recut(view, holeK).catch(() => null);
  if (cut) { rows.forEach(r => { r.cluster_id = cut.assignments[r.hole_id] ?? null; }); clusterNames = cut.names; }
}

function setupMap() {
  if (!globalThis.L) { $('#map').innerHTML = '<p class="empty">Map library unavailable.</p>'; return; }
  map = L.map('map', { preferCanvas: true, boxZoom: false }).setView([-25.5, 121.5], 5);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors', maxZoom: 17 })
    .on('tileerror', () => { $('#legend').dataset.offline = '1'; }).addTo(map);
  layer = L.layerGroup().addTo(map);
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

function draw() {
  if (!map) return;
  layer.clearLayers();
  const shown = visible();
  for (const r of shown) {
    const analysed = colourBy === 'cluster' ? r.cluster_id != null : colourBy === 'anomaly' ? r.anomaly_score != null : colourBy === 'chem' ? !!chem?.byHole[r.hole_id]?.above : true;
    const picked = selection.has(r.hole_id);
    L.circleMarker([r.lat, r.lon], { radius: analysed ? (colourBy === 'chem' ? 6.5 : r.processed_status === 'prepared' ? 5 : 3.5) : 2.5, weight: picked ? 2 : .6, color: picked ? '#111' : '#ffffff', fillColor: colourOf(r), fillOpacity: analysed ? .92 : .35, opacity: analysed ? 1 : .4 })
      .on('click', () => side(r)).bindTooltip(colourBy === 'chem' && chem?.byHole[r.hole_id] ? `${esc(r.hole_id)} · max ${analyte} ${num(chem.byHole[r.hole_id].max, 2)} ${chem.unit} · ${chem.byHole[r.hole_id].above} samples > ${threshold}` : `${esc(r.hole_id)} · target: ${esc(r.commodity || 'unknown')}`).addTo(layer);
  }
  legend(shown);
}

function legend(shown) {
  let items = [];
  if (colourBy === 'status') items = Object.entries(STATUS).map(([k, [label, color]]) => [label, color, shown.filter(r => statusOf(r) === k).length]);
  else if (colourBy === 'cluster') items = [...new Set(shown.map(r => r.cluster_id).filter(v => v != null))].sort((a, b) => a - b).map(id => [`${id} · ${clusterNames[id] || 'Hole cluster'}`, clusterColor(id), shown.filter(r => r.cluster_id === id).length]);
  else if (colourBy === 'anomaly') items = [['Typical', scoreColor(.5)], ['Unusual', scoreColor(.85)], ['Most unusual', scoreColor(1)], ['Not analysed', '#d5dcdf']];
  else if (colourBy === 'assays') items = [['Has assays', '#0f6e68'], ['No assays', '#d5dcdf']];
  else if (colourBy === 'chem') items = [[`No ${analyte} assays`, '#d5dcdf'], [`Max ≤ ${threshold} ${chem?.unit || ''}`, '#9fb7c2'], [`Above ${threshold}`, scoreColor(.6)], ['Highest', scoreColor(1)], [`${num(chem?.holes_above)} of ${num(chem?.holes_with_assays)} holes above`, 'transparent']];
  else {
    const key = colourBy === 'commodity' ? r => (r.commodity || '').split(',')[0] : r => r.tectonic_region;
    const counts = {}; shown.forEach(r => { const k = key(r) || 'Unknown'; counts[k] = (counts[k] || 0) + 1; });
    items = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 14).map(([k, n]) => [k, categoryColor(k === 'Unknown' ? '' : k), n]);
  }
  $('#legend').innerHTML = `${items.map(([label, color, n]) => `<span><i style="background:${color}"></i>${esc(label)}${n != null ? ` <span class="muted">${num(n)}</span>` : ''}</span>`).join('')}
    <span class="muted">${num(shown.length)} shown${$('#legend').dataset.offline ? ' · basemap offline' : ''}</span>`;
}

async function side(r) {
  const node = $('#side');
  node.innerHTML = `<h2>${esc(r.hole_id)}</h2><p class="muted">Loading…</p>`;
  const [detail, similar] = await Promise.all([
    api(`/api/v2/holes/${encodeURIComponent(r.hole_id)}`),
    api(`/api/v2/holes/${encodeURIComponent(r.hole_id)}/similar?view=${view}&limit=6`).catch(() => ({ items: [] })),
  ]);
  const h = detail.hole;
  const profile = detail.profiles.swir?.summary || detail.profiles.tir?.summary;
  node.innerHTML = `<h2>${esc(h.hole_id)}</h2>
    <p class="muted">${esc(h.commodity || 'Commodity unknown')} · ${esc(h.tectonic_region || 'Region unknown')}</p>
    <div class="facts"><div><span>Operator</span>${esc(h.operator || '–')}</div><div><span>Length</span>${num(h.length_m)} m</div>
    <div><span>Assays</span>${num(h.n_assay)} intervals</div><div><span>Processed</span>${esc(h.processed_status)}</div><div><span>Raw</span>${esc(h.raw_status)}</div></div>
    ${profile ? `<h3 style="margin-top:12px">Main minerals (SWIR)</h3>${bars(ranked(profile.top_minerals, 6), mineralColor)}` : '<p class="notice">No prepared mineral data yet. Use Data to download and process it.</p>'}
    ${similar.items.length ? `<h3 style="margin-top:12px">Most similar holes</h3><table><tbody>${similar.items.map(s => `<tr><td><button class="link" data-open="${esc(s.hole_id)}">${esc(s.hole_id)}</button></td><td class="num">${pct(1 - s.distance)}</td><td class="num muted">${num(s.km)} km</td></tr>`).join('')}</tbody></table>` : ''}
    <div class="row gap" style="margin-top:12px">
      <button type="button" data-open="${esc(h.hole_id)}">Open strip log</button>
      <button type="button" class="secondary" id="add">${selection.has(h.hole_id) ? 'Remove from selection' : 'Add to selection'}</button>
    </div>`;
  node.onclick = event => {
    const open = event.target.closest('[data-open]')?.dataset.open;
    if (open) go(`hole/${encodeURIComponent(open)}`);
    if (event.target.id === 'add') { selection.toggle(h.hole_id); side(r); draw(); }
  };
}

export function leave() { if (map) { map.remove(); map = null; } }
