import { $, api, esc, num, pct, bars, ranked, title, loading, failure } from '../util.js';
import { selection } from '../state.js';
import { groupColor, mineralColor, clusterColor, scoreColor, categoryColor } from '../palette.js';
import { stripLog, lineChart } from '../charts.js';
import { go } from '../main.js';

const DEFAULT_ANALYTES = ['K2O', 'MgO', 'CaO', 'Na2O', 'Fe2O3T', 'Au', 'Cu'];
let chosenAnalytes = null;

export async function render(root, [holeId]) {
  root.innerHTML = `<div class="page-head"><div><h1>Hole strip log</h1><p class="muted">Mineralogy by depth from every sensor, with clusters, domains, unusual intervals, assays and logged geology on one depth scale.</p></div>
    <form id="pick" class="row gap"><input id="hole-input" list="hole-options" placeholder="Hole ID" value="${esc(holeId || '')}" aria-label="Hole ID"><datalist id="hole-options"></datalist><button>Open</button></form></div>
    <div id="hole-body"></div>`;
  $('#pick').onsubmit = event => { event.preventDefault(); const id = $('#hole-input').value.trim(); if (id) go(`hole/${encodeURIComponent(id)}`); };
  api('/api/v2/catalog?status=prepared&limit=3000').then(d => { $('#hole-options').innerHTML = d.rows.map(r => `<option value="${esc(r.hole_id)}">`).join(''); }).catch(() => {});
  if (!holeId) { $('#hole-body').innerHTML = '<p class="empty">Choose a hole, or open one from the map, a table or a similarity list.</p>'; return; }
  const body = $('#hole-body'); loading(body, `Loading ${holeId}…`);
  let detail, strip;
  try {
    [detail, strip] = await Promise.all([api(`/api/v2/holes/${encodeURIComponent(holeId)}`), api(`/api/v2/holes/${encodeURIComponent(holeId)}/striplog`)]);
  } catch (error) { failure(body, error); return; }
  const h = detail.hole;
  const links = [['NVCL mosaic', h.mosaic_url], ['Summary histogram', h.summary_histogram_url], ['Summary report', h.summary_report_url]].filter(([, u]) => u);
  body.innerHTML = `
    <section class="panel" style="margin-bottom:14px">
      <div class="row gap" style="justify-content:space-between"><div><h2>${esc(h.hole_id)} <span class="muted small">${esc(h.name && h.name !== h.hole_id ? h.name : '')}</span></h2>
      <p class="muted">${esc(h.commodity || 'Commodity unknown')} · ${esc(h.tectonic_region || 'Region unknown')} · ${esc(h.operator || '')} ${esc(h.project || '')}</p></div>
      <div class="row gap"><button type="button" class="secondary" id="select">${selection.has(h.hole_id) ? 'Remove from selection' : 'Add to selection'}</button>
      <button type="button" class="secondary" id="similar-btn">Find similar holes</button>
      <a class="button secondary" href="/api/v2/holes/${encodeURIComponent(h.hole_id)}/export.csv" download>Export CSV</a></div></div>
      <div class="facts">
        <div><span>Length</span>${num(h.length_m)} m</div><div><span>Coordinates</span>${num(h.lat, 4)}, ${num(h.lon, 4)}</div>
        <div><span>SWIR / TIR scans</span>${esc(h.scan_swir || '–')} / ${esc(h.scan_tir || '–')}</div><div><span>Assays · geology logs</span>${num(h.n_assay)} · ${num(h.n_geology)}</div>
        <div><span>Processed logs</span>${esc(h.processed_status)}</div><div><span>Raw spectra</span>${esc(h.raw_status)}</div>
        <div><span>Hole cluster (SWIR+TIR)</span>${detail.profiles.combined ? `<span class="swatch" style="background:${clusterColor(detail.profiles.combined.cluster_id)}"></span>${detail.profiles.combined.cluster_id}` : '–'}</div>
        <div><span>Links</span>${links.length ? links.map(([l, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${l}</a>`).join(' · ') : '–'}</div>
      </div>
      <p class="muted small" style="margin-top:8px">${Object.entries(detail.sensors).map(([s, v]) => `${s.toUpperCase()}: ${num(v.with_minerals)}/${num(v.intervals)} metres with a diagnostic reading`).join(' · ') || 'No processed sensors prepared.'}${Object.keys(detail.sensors).length ? ' · Final Mask is not published with processed logs; raw summaries apply it.' : ''}</p>
    </section>
    <div class="grid cols-main-side">
      <section class="panel">
        <div class="toolbar" id="strip-controls"></div>
        <div id="strip" class="strip-wrap"></div>
        <div id="strip-legend" class="legend"></div>
      </section>
      <aside class="grid" style="align-content:start">
        <section class="panel" id="interval"><h2>Interval</h2><p class="muted">Click any track to inspect that depth.</p></section>
        <section class="panel" id="similar-holes"><h2>Similar holes</h2><p class="muted">Loading…</p></section>
      </aside>
    </div>`;
  $('#select').onclick = () => { selection.toggle(h.hole_id); $('#select').textContent = selection.has(h.hole_id) ? 'Remove from selection' : 'Add to selection'; };
  $('#similar-btn').onclick = () => { selection.add([h.hole_id]); go(`similarity/${encodeURIComponent(h.hole_id)}`); };
  drawStrip(strip);
  similarHoles(h.hole_id);
}

function drawStrip(strip, highlight) {
  const tracks = [];
  const depths = [];
  const sensorLabel = { swir: 'SWIR', tir: 'TIR', vnir: 'VNIR' };
  for (const [sensor, track] of Object.entries(strip.tracks)) {
    const rows = track.intervals.map(r => ({ f: r.depth_from_m, t: r.depth_to_m, parts: r.groups, row: r, sensor }));
    rows.forEach(r => depths.push(r.f, r.t));
    const names = track.cluster_names;
    tracks.push({ label: `${sensorLabel[sensor]} minerals`, title: 'Mineral-group mix per metre (share of TSG component weight)', width: sensor === 'vnir' ? 70 : 130, kind: 'stack', rows, color: groupColor,
      describe: r => `${ranked(r.row.top_minerals).map(([m, v]) => `${esc(m)} ${pct(v)}`).join('<br>') || 'No diagnostic reading'}<br><span class="muted">${r.row.n_admitted}/${r.row.n_total} readings diagnostic</span>` });
    if (sensor !== 'vnir' && track.run_id) {
      tracks.push({ label: 'Clus.', title: `${sensorLabel[sensor]} interval cluster`, width: 30, kind: 'ribbon', rows: track.intervals.filter(r => r.cluster_id != null).map(r => ({ f: r.depth_from_m, t: r.depth_to_m, value: r.cluster_id, row: r, sensor })),
        describe: r => `Cluster ${r.value}: ${esc(names[`fine:${r.value}`] || '')}<br>Group ${r.row.coarse_cluster_id}: ${esc(names[`coarse:${r.row.coarse_cluster_id}`] || '')}` });
      tracks.push({ label: 'Dom.', title: 'Contiguous depth zones with a consistent mineral mix', width: 30, kind: 'ribbon', rows: track.domains.map((d, i) => ({ f: d.depth_from_m, t: d.depth_to_m, value: d.cluster_id, color: i % 2 ? '#8fa3ad' : '#c3d0d6', row: d, sensor })),
        describe: r => `Domain ${r.row.domain_id + 1}: ${num(r.row.depth_from_m)}–${num(r.row.depth_to_m)} m<br>${ranked(r.row.minerals, 4).map(([m, v]) => `${esc(m)} ${pct(v)}`).join('<br>')}` });
      tracks.push({ label: 'Unusual', title: 'Anomaly score (percentile, combined reasons)', width: 40, kind: 'line', min: 0.5, max: 1, rows: track.intervals.filter(r => r.anomaly_score != null).map(r => ({ f: r.depth_from_m, t: r.depth_to_m, value: r.anomaly_score, color: scoreColor(r.anomaly_score), row: r, sensor })),
        describe: r => `Score ${pct(r.value)}${r.row.anomaly_reasons?.length ? `<br>${r.row.anomaly_reasons.map(esc).join('<br>')}` : ''}` });
    }
  }
  for (const [sensor, table] of Object.entries(strip.raw || {})) {
    if (!table.bin) continue;
    const rows = table.bin.map((b, i) => ({ f: table.depth_from_m[i], t: table.depth_to_m[i], i }));
    if (sensor === 'nir' && table.aloh_2200_pos) {
      tracks.push({ label: 'Al-OH nm', title: 'Raw spectra: 2200 nm Al-OH absorption position (white mica chemistry: low = muscovitic/paragonitic, high = phengitic)', width: 50, kind: 'line', min: 2190, max: 2225,
        rows: rows.map(r => ({ ...r, value: table.aloh_2200_depth[r.i] > 0.02 ? table.aloh_2200_pos[r.i] : null, color: '#6fa8dc' })), describe: r => `Al-OH position ${num(r.value, 1)} nm<br>depth ${num(table.aloh_2200_depth[r.i], 3)}` });
    }
    if (table.spectral_class) {
      tracks.push({ label: `Raw ${sensor.toUpperCase()} class`, width: 34, kind: 'ribbon', rows: rows.filter(r => table.spectral_class[r.i] != null).map(r => ({ ...r, value: table.spectral_class[r.i] })), describe: r => `Spectral class ${r.value}` });
    }
  }
  const analytes = chosenAnalytes || DEFAULT_ANALYTES.filter(a => strip.analytes.includes(a)).slice(0, 4);
  for (const analyte of analytes) {
    const values = strip.assays.filter(a => a[analyte] != null);
    if (!values.length) continue;
    const sorted = values.map(v => v[analyte]).sort((a, b) => a - b);
    const max = sorted[Math.floor(sorted.length * .98)] || sorted[sorted.length - 1];
    const log = ['Au', 'Cu', 'Zn', 'Pb', 'Ni', 'As', 'S', 'Cr', 'U', 'Th'].includes(analyte);
    tracks.push({ label: analyte, title: `Assay ${analyte} (${log ? 'ppm, log' : 'wt%'})`, width: 46, kind: 'line', log, min: log ? Math.max(sorted[0], 1e-3) : 0, max: Math.max(max, 1e-6), fill: '#b5502a',
      rows: values.map(v => ({ f: v.f, t: v.t, value: v[analyte] })), describe: r => `${analyte} ${num(r.value, log ? 3 : 2)} ${log ? 'ppm' : '%'}` });
    values.forEach(v => depths.push(v.f, v.t));
  }
  if (strip.lithology.length) {
    tracks.push({ label: 'Logged geology', title: 'Company lithology (not harmonised) and GSWA rock name where provided', width: 110, kind: 'text',
      rows: strip.lithology.map(l => ({ f: l.f, t: l.t, value: l.code || l.name, label: l.rock || l.name || l.code, row: l })),
      describe: r => `${esc(r.row.code || '')} ${esc(r.row.name || '')}<br>${esc(r.row.rock || '')} ${esc(r.row.regolith || '')}<br><span class="muted">${esc(r.row.comments || '')}</span>` });
  }
  if (!tracks.length) { $('#strip').innerHTML = '<p class="empty">No prepared data for this hole yet. Use the Data tab to download and process it.</p>'; return; }
  const depthMin = Math.max(0, Math.min(...depths)), depthMax = Math.max(...depths);
  const controls = $('#strip-controls');
  controls.innerHTML = `<label class="field">Scale<select id="scale"><option value="1">1 px/m</option><option value="2">2 px/m</option><option value="4">4 px/m</option><option value="8">8 px/m</option></select></label>
    ${strip.analytes.length ? `<label class="field">Assays shown<select id="analytes" multiple size="1" style="min-width:160px">${strip.analytes.map(a => `<option ${analytes.includes(a) ? 'selected' : ''}>${a}</option>`).join('')}</select></label>` : '<span class="muted small">No assays linked to this hole.</span>'}
    <span class="muted small">${num(depthMax - depthMin)} m · hover for values, click to inspect</span>`;
  const scale = Number(sessionStorage.getItem('strip-scale') || (depthMax - depthMin > 800 ? 1 : 2));
  $('#scale').value = String(scale);
  $('#scale').onchange = e => { try { sessionStorage.setItem('strip-scale', e.target.value); } catch { /* ignore */ } drawStrip(strip, highlight); };
  if ($('#analytes')) $('#analytes').onchange = e => { chosenAnalytes = [...e.target.selectedOptions].map(o => o.value); drawStrip(strip, highlight); };
  const chart = stripLog($('#strip'), { tracks, depthMin, depthMax, pxPerMetre: Number($('#scale').value), highlight,
    onPick: hit => { if (hit.row?.row?.feature_set_id) inspect(strip, hit.row.row); } });
  const groups = new Set(); Object.values(strip.tracks).forEach(t => t.intervals.forEach(r => Object.keys(r.groups || {}).forEach(g => groups.add(g))));
  $('#strip-legend').innerHTML = [...groups].sort().map(g => `<span><i style="background:${groupColor(g)}"></i>${esc(title(g))}</span>`).join('');
  if (highlight) chart.scrollTo(highlight.f);
}

async function inspect(strip, row) {
  drawStrip(strip, { f: row.depth_from_m, t: row.depth_to_m });
  const node = $('#interval'); loading(node);
  const d = await api(`/api/v2/intervals/${row.feature_set_id}/${row.bin}`);
  const i = d.interval;
  const minerals = ranked(i.minerals, 8);
  node.innerHTML = `<h2>${esc(i.hole_id)} · ${num(i.depth_from_m, 1)}–${num(i.depth_to_m, 1)} m <span class="muted small">${i.sensor.toUpperCase()}</span></h2>
    <p class="muted small">${i.n_admitted} of ${i.n_total} readings diagnostic · mean TSG error ${num(i.error_mean, 0)}</p>
    ${bars(minerals, mineralColor)}
    ${d.label ? `<dl class="kv" style="margin-top:10px"><dt>Cluster</dt><dd><span class="swatch" style="background:${clusterColor(d.label.cluster_id)}"></span>${d.label.cluster_id} (group ${d.label.coarse_cluster_id})</dd>
      <dt>Domain</dt><dd>${d.label.domain_id + 1}</dd><dt>Unusualness</dt><dd>${pct(d.label.anomaly_score)} ${(d.label.anomaly_reasons || []).map(r => `<span class="chip warn">${esc(r)}</span>`).join(' ')}</dd></dl>` : ''}
    ${d.spectrum ? `<h3 style="margin-top:10px">Raw ${d.spectrum.sensor.toUpperCase()} median spectrum</h3>${lineChart({ x: d.spectrum.wavelengths, series: [{ values: d.spectrum.values, color: 'var(--accent)', name: '1 m median reflectance' }], xLabel: 'Wavelength (nm)', width: 340, height: 180 })}` : ''}
    <h3 style="margin-top:10px">Most similar intervals in other holes</h3>
    ${d.similar.length ? `<table><tbody>${d.similar.slice(0, 8).map(s => `<tr><td><button class="link" data-hole="${esc(s.hole_id)}">${esc(s.hole_id)}</button></td><td class="num">${num(s.depth_from_m)}–${num(s.depth_to_m)} m</td><td class="num">${pct(1 - s.distance)}</td></tr>`).join('')}</tbody></table><p class="muted small">${esc(d.similar_method)}</p>` : '<p class="muted">None found.</p>'}`;
  node.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
}

async function similarHoles(holeId) {
  const node = $('#similar-holes');
  const d = await api(`/api/v2/holes/${encodeURIComponent(holeId)}/similar?view=combined&limit=10`).catch(() => ({ items: [] }));
  node.innerHTML = `<h2>Similar holes</h2>${d.items.length ? `<table><thead><tr><th>Hole</th><th class="num">Similarity</th><th class="num">Distance</th></tr></thead><tbody>${d.items.map(s => `<tr><td><button class="link" data-hole="${esc(s.hole_id)}">${esc(s.hole_id)}</button><br><span class="muted small">${esc((s.why || []).join(' · '))}</span></td><td class="num">${pct(1 - s.distance)}</td><td class="num">${num(s.km)} km</td></tr>`).join('')}</tbody></table>
    <p class="muted small">${esc(d.distance || '')}. Similarity = 1 − distance.</p>` : '<p class="muted">Not analysed yet.</p>'}`;
  node.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
}

export { categoryColor };
