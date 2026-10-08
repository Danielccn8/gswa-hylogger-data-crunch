import { $, api, esc, num, pct, bars, stack, ranked, loading, failure } from '../util.js';
import { groupColor } from '../palette.js';
import { go } from '../main.js';

let sensor = 'swir', map, marker, links, cvLayer;

export async function render(root) {
  root.innerHTML = `<div class="page-head"><div><h1>Expected mineralogy at a location</h1>
    <p class="muted">Click the map. The estimate interpolates the mineral-group mix of the nearest analysed holes and states how well that worked when tested on known holes.</p></div>
    <div class="toolbar"><label class="field">Sensor<select id="sensor"><option value="swir">SWIR</option><option value="tir">TIR</option></select></label>
    <label class="field">Map layer<select id="layer"><option value="none">Holes used</option><option value="error">Validation error per hole</option></select></label></div></div>
    <div id="cv" class="metrics"></div>
    <div class="grid cols-main-side"><div id="map" class="map"></div><aside class="panel" id="result"><h2>Estimate</h2><p class="muted">Click anywhere on the map.</p></aside></div>
    <section class="panel" style="margin-top:14px" id="by-distance"></section>`;
  $('#sensor').value = sensor;
  $('#sensor').onchange = e => { sensor = e.target.value; summary(); };
  $('#layer').onchange = () => summary();
  map = L.map('map', { preferCanvas: true }).setView([-26, 121.5], 5);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors', maxZoom: 17 }).addTo(map);
  links = L.layerGroup().addTo(map); cvLayer = L.layerGroup().addTo(map);
  map.on('click', e => estimate(e.latlng.lat, e.latlng.lng));
  summary();
}

const rowsOf = rows => rows.map(r => `<tr><td>${num(r.from_km)}–${r.to_km > 5000 ? '∞' : num(r.to_km)} km</td><td class="num">${r.holes}</td><td class="num">${r.model_error.toFixed(3)}</td><td class="num">${r.region_baseline_error.toFixed(3)}</td><td class="num" style="color:${r.skill_vs_region >= 0.15 ? 'var(--accent)' : r.skill_vs_region < 0 ? 'var(--danger)' : 'inherit'}">${pct(r.skill_vs_region)}</td></tr>`).join('');

async function summary() {
  let d; try { d = await api(`/api/v2/prediction/cv?sensor=${sensor}`); } catch (error) { failure($('#cv'), error); return; }
  const s = d.card.summary;
  $('#cv').innerHTML = [
    ['Holes used', num(s.holes), 'with ≥ 10 diagnostic metres'],
    ['Skill, hole left out', pct(s.loho_skill_vs_region), 'vs tectonic-region average'],
    ['Skill, district left out', pct(s.lodo_skill_vs_region), 'no holes within ~5 km'],
    ['Reliable up to', s.support_limit_km == null ? 'no skill' : `${num(s.support_limit_km)} km`, 'to the nearest analysed hole'],
  ].map(([a, b, c]) => `<div class="metric"><span>${a}</span><strong>${b}</strong><small>${c}</small></div>`).join('');
  $('#by-distance').innerHTML = `<h2>Validation by distance to the nearest analysed hole</h2>
    <p class="muted small">Median Hellinger error between predicted and observed hole composition (0 = identical). Skill = 1 − model error / regional-average error. ${esc(d.card.method)}.</p>
    <table><thead><tr><th>Nearest hole</th><th class="num">Holes</th><th class="num">Model error</th><th class="num">Regional baseline</th><th class="num">Skill</th></tr></thead><tbody>
    ${rowsOf(d.card.by_distance)}</tbody></table>
    ${s.lodo_by_distance ? `<h3 style="margin-top:14px">When the whole district is unseen (leave-district-out)</h3>
    <p class="muted small">The honest test for a new area: every hole within the same ~5 km district is withheld. ${esc(s.support_rule || '')}.</p>
    <table><thead><tr><th>Nearest hole</th><th class="num">Holes</th><th class="num">Model error</th><th class="num">Regional baseline</th><th class="num">Skill</th></tr></thead><tbody>${rowsOf(s.lodo_by_distance)}</tbody></table>` : ''}
    ${d.card.covariate_model ? `<h3 style="margin-top:14px">Could a model with regional covariates do better far from data?</h3>
    <p class="small">${esc(d.card.covariate_model.method)}. Median error on unseen districts ${d.card.covariate_model.median_error} vs ${s.lodo_median_error} for distance weighting alone (skill vs region ${pct(d.card.covariate_model.skill_vs_region)}).
    ${d.card.covariate_model.beats_lodo_idw ? '<span class="chip accent">used beyond the support limit</span>' : '<span class="chip">not better — not used</span>'}</p>` : ''}`;
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

async function estimate(lat, lon) {
  const node = $('#result'); loading(node, 'Estimating…');
  let d; try { d = await api(`/api/v2/predict?lat=${lat}&lon=${lon}&sensor=${sensor}`); } catch (error) { failure(node, error); return; }
  if (marker) marker.remove();
  marker = L.circleMarker([lat, lon], { radius: 8, weight: 3, color: '#111', fillColor: '#f2a33a', fillOpacity: 1 }).addTo(map);
  links.clearLayers();
  d.neighbours.forEach(n => L.polyline([[lat, lon], [n.lat, n.lon]], { weight: 1, color: '#0f6e68', opacity: .6 }).addTo(links));
  const comp = ranked(d.composition);
  node.innerHTML = `<h2>${num(lat, 3)}, ${num(lon, 3)}</h2>
    ${d.within_support ? `<p class="chip accent">Within support · nearest hole ${num(d.nearest_km, 1)} km</p>`
      : `<div class="notice">Nearest analysed hole is ${num(d.nearest_km, 1)} km away${d.support_limit_km ? `, beyond the ${num(d.support_limit_km)} km where interpolation beat the regional average` : ''}. Treat this as a regional prior, not a local estimate.</div>`}
    <h3 style="margin-top:10px">Expected ${sensor.toUpperCase()} mineral groups (± spread among neighbours)</h3>
    ${stack(d.composition, groupColor)}<div style="margin-top:8px">${bars(comp, groupColor, { spread: d.spread })}</div>
    ${d.expected_error ? `<p class="muted small">Typical validation error at this distance: ${d.expected_error.model_error.toFixed(3)} (regional average ${d.expected_error.region_baseline_error.toFixed(3)}).</p>` : ''}
    ${d.depth_bands.length ? `<h3 style="margin-top:10px">By vertical depth</h3><table><tbody>${d.depth_bands.map(b => `<tr><td class="small">${b.from_m}–${b.to_m > 2000 ? '…' : b.to_m} m</td><td style="width:60%">${stack(b.composition, groupColor)}</td><td class="num small">${b.holes} holes</td></tr>`).join('')}</tbody></table>` : ''}
    <h3 style="margin-top:10px">Holes used</h3><table><tbody>${d.neighbours.map(n => `<tr><td><button class="link" data-hole="${esc(n.hole_id)}">${esc(n.hole_id)}</button></td><td class="num">${num(n.km, 1)} km</td></tr>`).join('')}</tbody></table>`;
  node.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
}

export function leave() { if (map) { map.remove(); map = null; } }
