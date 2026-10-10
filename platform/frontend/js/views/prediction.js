import { $, api, esc, num, pct, bars, stack, ranked, loading, failure } from '../util.js';
import { groupColor } from '../palette.js';
import { go } from '../main.js';
import { info } from '../glossary.js';

let sensor = 'swir', map, marker, links, cvLayer;
const SKILL = 'Skill = 1 − (model error ÷ error of simply using the tectonic-region average). 0 = no better than the regional average; 1 = perfect. Measured by hiding holes and predicting them from the others.';

export async function render(root) {
  root.innerHTML = `<div class="page-head"><div><h1>Expected mineralogy at a location</h1>
    <p class="muted">Click anywhere on the map. The app finds the nearest analysed holes, lifts them on the map, and combines their mineral mix — closer holes count more — overall and by depth, with how well this worked when tested on known holes.</p></div>
    <div class="toolbar"><label class="field">Sensor<select id="sensor"><option value="swir">SWIR</option><option value="tir">TIR</option></select></label>
    <label class="field">Map layer ${info('“Validation error per hole” colours every analysed hole by how wrong the prediction was when that hole was hidden and predicted from its neighbours (green = good, red = poor).')}<select id="layer"><option value="none">Holes used</option><option value="error">Validation error per hole</option></select></label></div></div>
    <section class="panel" style="margin-bottom:14px"><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">
      <div><h3>How it works</h3><p class="small">The 8 nearest holes with mineral data are combined by inverse-distance weighting: a hole twice as close counts four times as much. The result is the mix of mineral groups you would expect to log at this spot.</p></div>
      <div><h3>How reliable it is</h3><p class="small">It was tested by hiding each hole (and, more strictly, each whole district) and predicting it from the rest. Near existing holes the estimate beats the regional average; far from them it is only a regional prior, and the app says so.</p></div>
      <div><h3>By depth</h3><p class="small">The same weighting is repeated in vertical-depth bands (0–50 m, 50–100 m, …), using the surveyed hole path where available, so you can see whether the expected mix changes with depth.</p></div>
    </div></section>
    <div id="cv" class="metrics"></div>
    <div class="grid cols-main-side"><div id="map" class="map"></div><aside class="panel side-panel" id="result"><h2>Estimate</h2><p class="muted">Click anywhere on the map.</p></aside></div>
    <section class="panel" style="margin-top:14px" id="by-depth"><h2>Expected by depth vs the holes used</h2><p class="muted">Click the map to see the predicted mix in each depth band next to the holes it comes from.</p></section>
    <section class="panel" style="margin-top:14px" id="by-distance"></section>`;
  $('#sensor').value = sensor;
  $('#sensor').onchange = e => { sensor = e.target.value; summary(); if (marker) estimate(marker.getLatLng().lat, marker.getLatLng().lng); };
  $('#layer').onchange = () => summary();
  map = L.map('map', { preferCanvas: true }).setView([-26, 121.5], 5);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors', maxZoom: 17 }).addTo(map);
  cvLayer = L.layerGroup().addTo(map); links = L.layerGroup().addTo(map);
  map.on('click', e => estimate(e.latlng.lat, e.latlng.lng));
  summary();
}

const rowsOf = rows => rows.map(r => `<tr><td>${num(r.from_km)}–${r.to_km > 5000 ? '∞' : num(r.to_km)} km</td><td class="num">${r.holes}</td><td class="num">${r.model_error.toFixed(3)}</td><td class="num">${r.region_baseline_error.toFixed(3)}</td><td class="num">${pct(r.skill)}</td></tr>`).join('');

async function summary() {
  let d; try { d = await api(`/api/v2/prediction/cv?sensor=${sensor}`); } catch (error) { failure($('#cv'), error); return; }
  const s = d.card.summary;
  $('#cv').innerHTML = [
    ['Holes the estimate can use', num(s.holes), 'with at least 10 diagnostic metres', 'Analysed holes with enough mineral data to be used as neighbours.'],
    ['Skill near known holes', pct(s.loho_skill_vs_region), 'one hole hidden at a time', `${SKILL} Here each hole was hidden in turn; its neighbours, often in the same district, remained.`],
    ['Skill in an unseen district', pct(s.lodo_skill_vs_region), 'all holes within ~5 km hidden', `${SKILL} Here all holes of the same district were hidden: the honest test for a brand-new area.`],
    ['Reliable up to', s.support_limit_km == null ? 'no skill' : `${num(s.support_limit_km)} km`, 'from the nearest analysed hole', 'Beyond this distance to the nearest analysed hole the interpolation no longer beat the regional average in the tests.'],
  ].map(([a, b, c, tip]) => `<div class="metric" data-tip="${esc(tip)}" tabindex="0"><span>${a} <span class="info" aria-hidden="true">i</span></span><strong>${b}</strong><small>${c}</small></div>`).join('');
  $('#by-distance').innerHTML = `<details><summary>How the error grows with distance to the nearest hole</summary>
    <p class="muted small" style="margin-top:8px">Median Hellinger error between predicted and observed hole composition (0 = identical). ${esc(SKILL)} ${esc(d.card.method)}.</p>
    <table><thead><tr><th>Nearest hole</th><th class="num">Holes</th><th class="num">Model error</th><th class="num">Regional baseline</th><th class="num">Skill</th></tr></thead><tbody>
    ${rowsOf(d.card.by_distance)}</tbody></table>
    ${s.lodo_by_distance ? `<h3 style="margin-top:14px">When the whole district is unseen</h3><p class="muted small">${esc(s.support_rule || '')}.</p>
    <table><thead><tr><th>Nearest hole</th><th class="num">Holes</th><th class="num">Model error</th><th class="num">Regional baseline</th><th class="num">Skill</th></tr></thead><tbody>${rowsOf(s.lodo_by_distance)}</tbody></table>` : ''}
    ${d.card.covariate_model ? `<p class="small" style="margin-top:10px">A model with regional covariates (${esc(d.card.covariate_model.method)}) reached a median error of ${d.card.covariate_model.median_error} in unseen districts vs ${s.lodo_median_error} for distance weighting alone: ${d.card.covariate_model.beats_lodo_idw ? '<span class="chip accent">used beyond the support limit</span>' : '<span class="chip">not better, not used</span>'}</p>` : ''}</details>`;
  cvLayer.clearLayers();
  if ($('#layer').value === 'error') {
    for (const h of d.holes) {
      if (h.lat == null) continue;
      const t = Math.min(1, h.loho_error / 0.6);
      L.circleMarker([h.lat, h.lon], { radius: 4, weight: .5, color: '#fff', fillOpacity: .9, fillColor: `rgb(${Math.round(30 + 200 * t)},${Math.round(150 - 90 * t)},${Math.round(130 - 90 * t)})` })
        .bindTooltip(`${esc(h.hole_id)} · error ${h.loho_error.toFixed(3)} · nearest ${num(h.nearest_km, 1)} km`).addTo(cvLayer);
    }
  }
}

const top3 = mix => ranked(mix, 3).map(([g, v]) => `${esc(g)} ${pct(v)}`).join(' · ');

async function estimate(lat, lon) {
  const node = $('#result'); loading(node, 'Estimating…');
  let d; try { d = await api(`/api/v2/predict?lat=${lat}&lon=${lon}&sensor=${sensor}`); } catch (error) { failure(node, error); return; }
  if (marker) marker.remove();
  marker = L.marker([lat, lon], { icon: L.divIcon({ className: 'rank-pin focus', html: '<span>?</span>', iconSize: [28, 28] }), zIndexOffset: 2000 }).addTo(map);
  links.clearLayers();
  const maxWeight = Math.max(...d.neighbours.map(n => n.weight));
  d.neighbours.forEach((n, i) => {
    L.polyline([[lat, lon], [n.lat, n.lon]], { weight: 1 + 5 * n.weight / maxWeight, color: '#0f6e68', opacity: .55, interactive: false }).addTo(links);
    L.marker([n.lat, n.lon], { icon: L.divIcon({ className: 'rank-pin', html: `<span style="--i:${i}">${i + 1}</span>`, iconSize: [24, 24] }), zIndexOffset: 1000 - i })
      .bindTooltip(`<b>${i + 1}. ${esc(n.hole_id)}</b> · ${num(n.km, 1)} km · weight ${pct(n.weight)}<br>${top3(n.composition)}`, { direction: 'top', offset: [0, -10], className: 'wrap-tip' })
      .on('click', () => go(`hole/${encodeURIComponent(n.hole_id)}`)).addTo(links);
  });
  map.fitBounds(L.latLngBounds([[lat, lon], ...d.neighbours.map(n => [n.lat, n.lon])]).pad(0.3), { maxZoom: 11, animate: false });
  const comp = ranked(d.composition);
  node.innerHTML = `<h2>${num(lat, 3)}, ${num(lon, 3)}</h2>
    ${d.within_support ? `<p><span class="chip accent">Within support</span> nearest analysed hole ${num(d.nearest_km, 1)} km away</p>`
      : `<div class="notice">The nearest analysed hole is ${num(d.nearest_km, 1)} km away${d.support_limit_km ? `, beyond the ${num(d.support_limit_km)} km where the interpolation beat the regional average` : ''}. Treat this as a regional prior, not a local estimate.</div>`}
    <h3 style="margin-top:10px">Expected ${sensor.toUpperCase()} mineral groups ${info('Weighted mean of the neighbours’ mixes. ± is the weighted spread among them: a large ± means the neighbours disagree.')}</h3>
    ${stack(d.composition, groupColor)}<div style="margin-top:8px">${bars(comp, groupColor, { spread: d.spread })}</div>
    ${d.expected_error ? `<p class="muted small">At this distance, tests gave a typical error of ${d.expected_error.model_error.toFixed(3)} vs ${d.expected_error.region_baseline_error.toFixed(3)} for the regional average${d.expected_error.skill != null ? ` (skill ${pct(d.expected_error.skill)})` : ''}.</p>` : ''}
    <h3 style="margin-top:10px">Holes used</h3>
    <table class="mini-table"><thead><tr><th></th><th>Hole</th><th class="num">km</th><th class="num">Weight</th><th style="width:40%">Its mix</th></tr></thead><tbody>
    ${d.neighbours.map((n, i) => `<tr><td><span class="rank-pin"><span style="width:20px;height:20px;animation:none">${i + 1}</span></span></td><td><button class="link" data-hole="${esc(n.hole_id)}">${esc(n.hole_id)}</button></td><td class="num">${num(n.km, 1)}</td><td class="num">${pct(n.weight)}</td><td data-tip="${esc(ranked(n.composition, 4).map(([g, v]) => `${g} ${pct(v)}`).join(', '))}">${stack(n.composition, groupColor)}</td></tr>`).join('')}</tbody></table>`;
  node.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
  byDepth(d);
}

function byDepth(d) {
  const node = $('#by-depth');
  if (!d.depth_bands.length) { node.innerHTML = '<h2>Expected by depth vs the holes used</h2><p class="muted">The neighbours have too little data by depth band here.</p>'; return; }
  const shown = d.neighbours.slice(0, 6);
  const label = b => b.to_m >= 2000 ? `${num(b.from_m)} m and deeper` : `${num(b.from_m)}–${num(b.to_m)} m`;
  const groups = new Set(); d.depth_bands.forEach(b => Object.keys(b.composition).forEach(g => groups.add(g)));
  node.innerHTML = `<h2>Expected by depth vs the holes used ${info('Vertical depth (true vertical depth from the surveyed hole path where available). Each cell is the mineral-group mix of that depth band; the first column is the weighted prediction for the clicked location. Empty cells: the hole does not reach that depth or has no data there.')}</h2>
    <p class="muted small">Read across a row to see how the prediction for each depth is built from the neighbouring holes; read down a column to see how the mix changes with depth.</p>
    <div class="table-wrap"><table><thead><tr><th>Vertical depth</th><th style="min-width:170px">Predicted here</th>${shown.map((n, i) => `<th style="min-width:130px">${i + 1}. <button class="link" data-hole="${esc(n.hole_id)}">${esc(n.hole_id)}</button><br><span class="muted small">${num(n.km, 1)} km · weight ${pct(n.weight)}</span></th>`).join('')}</tr></thead><tbody>
    ${d.depth_bands.map(b => `<tr><td class="small"><b>${label(b)}</b><br><span class="muted">${b.holes} holes</span></td>
      <td data-tip="${esc(ranked(b.composition, 4).map(([g, v]) => `${g} ${pct(v)}`).join(', '))}">${stack(b.composition, groupColor)}<span class="small">${top3(b.composition)}</span></td>
      ${shown.map(n => { const mix = n.bands[b.band]; return `<td ${mix ? `data-tip="${esc(ranked(mix, 4).map(([g, v]) => `${g} ${pct(v)}`).join(', '))}"` : ''}>${mix ? stack(mix, groupColor) : '<span class="muted small">–</span>'}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>
    <div class="legend">${[...groups].sort().map(g => `<span><i style="background:${groupColor(g)}"></i>${esc(g)}</span>`).join('')}</div>`;
  node.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
}

export function leave() { if (map) { map.remove(); map = null; marker = null; } }
