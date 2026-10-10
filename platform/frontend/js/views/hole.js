import { $, api, esc, num, pct, bars, stack, ranked, title, loading, failure, toast } from '../util.js';
import { selection } from '../state.js';
import { groupColor, mineralColor, clusterColor, scoreColor, categoryColor } from '../palette.js';
import { stripLog, lineChart } from '../charts.js';
import { go } from '../main.js';
import { PROCESSED, RAW, statusText, TERMS, NIR_MARKS, info } from '../glossary.js';

const DEFAULT_ANALYTES = ['K2O', 'MgO', 'CaO', 'Na2O', 'Fe2O3T', 'Au', 'Cu'];
const PRODUCT = { swir: ['SWIR', 'sTSAS', 'uTSAS'], tir: ['TIR', 'sjCLST', 'ujCLST'], vnir: ['VNIR', 'sTSAV', 'uTSAV'] };
const VERSIONS_HELP = 'TSG interprets every reading twice. The system run (s, e.g. sTSAS) is automatic. The user run (u, e.g. uTSAS) was re-run or edited by an analyst, usually with a mineral library chosen for the area; it is the only version the NVCL service publishes for Western Australia. Comparing them shows where the analyst changed the automatic answer.';
let chosenAnalytes = null, showPredicted = true, showVersions = true, chart = null, strip = null, holeId = null;

export async function render(root, [id, rangeSpec]) {
  holeId = id;
  root.innerHTML = `<div class="page-head"><div><h1>Hole strip log</h1><p class="muted">Mineralogy by depth from every sensor, with clusters, domains, unusual intervals, the automatic vs reviewed interpretation, raw spectra, assays and logged geology on one depth scale.</p></div>
    <form id="pick" class="row gap"><input id="hole-input" list="hole-options" placeholder="Hole ID" value="${esc(id || '')}" aria-label="Hole ID"><datalist id="hole-options"></datalist><button>Open</button></form></div>
    <div id="hole-body"></div>`;
  $('#pick').onsubmit = event => { event.preventDefault(); const value = $('#hole-input').value.trim(); if (value) go(`hole/${encodeURIComponent(value)}`); };
  api('/api/v2/catalog?status=prepared&limit=3000').then(d => { $('#hole-options').innerHTML = d.rows.map(r => `<option value="${esc(r.hole_id)}">`).join(''); }).catch(() => {});
  if (!id) { $('#hole-body').innerHTML = '<p class="empty">Choose a hole, or open one from the map, a table or a similarity list.</p>'; return; }
  const body = $('#hole-body'); loading(body, `Loading ${id}…`);
  let detail;
  try {
    [detail, strip] = await Promise.all([api(`/api/v2/holes/${encodeURIComponent(id)}`), api(`/api/v2/holes/${encodeURIComponent(id)}/striplog`)]);
  } catch (error) { failure(body, error); return; }
  const h = detail.hole;
  const links = [['NVCL mosaic', h.mosaic_url], ['Summary histogram', h.summary_histogram_url], ['Summary report', h.summary_report_url]].filter(([, u]) => u);
  const [pLabel, pText] = statusText(PROCESSED, h.processed_status);
  const [rLabel, rText] = statusText(RAW, h.raw_status, h.raw_available);
  body.innerHTML = `
    <section class="panel" style="margin-bottom:14px">
      <div class="row gap" style="justify-content:space-between"><div><h2>${esc(h.hole_id)} <span class="muted small">${esc(h.name && h.name !== h.hole_id ? h.name : '')}</span></h2>
      <p class="muted">Company target: ${esc(h.commodity || 'unknown')} ${info(TERMS.commodity)} · ${esc(h.tectonic_region || 'Region unknown')} · ${esc(h.operator || '')} ${esc(h.project || '')}</p></div>
      <div class="row gap"><button type="button" class="secondary" id="select">${selection.has(h.hole_id) ? 'Remove from selection' : 'Add to selection'}</button>
      <a class="button secondary" href="#/map/${encodeURIComponent(h.hole_id)}">Show on map</a>
      <button type="button" class="secondary" id="similar-btn">Compare with similar holes</button>
      <a class="button secondary" href="/api/v2/holes/${encodeURIComponent(h.hole_id)}/export.csv" download>Export CSV</a></div></div>
      <div class="facts">
        <div><span>Length</span>${num(h.length_m)} m</div><div><span>Coordinates</span>${num(h.lat, 4)}, ${num(h.lon, 4)}</div>
        <div><span>Mineral logs</span><span data-tip="${esc(pText)}" tabindex="0">${esc(pLabel)} <span class="info" aria-hidden="true">i</span></span></div>
        <div><span>Raw spectra</span><span data-tip="${esc(rText)}" tabindex="0">${esc(rLabel)} <span class="info" aria-hidden="true">i</span></span></div>
        <div><span>Assays · geology logs</span>${num(h.n_assay)} · ${num(h.n_geology)}</div>
        <div><span>Hole cluster (SWIR+TIR)</span>${detail.profiles.combined ? `<span class="swatch" style="background:${clusterColor(detail.profiles.combined.cluster_id)}"></span>${detail.profiles.combined.cluster_id}` : '–'}</div>
        <div><span>Links</span>${links.length ? links.map(([l, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${l}</a>`).join(' · ') : '–'}</div>
      </div>
      <p class="muted small" style="margin-top:8px">${Object.entries(detail.sensors).map(([s, v]) => `${s.toUpperCase()}: ${num(v.with_minerals)} of ${num(v.intervals)} metres with a diagnostic reading`).join(' · ') || 'No processed sensors prepared.'}</p>
    </section>
    <div class="grid cols-main-side">
      <section class="panel">
        <div class="toolbar" id="strip-controls"></div>
        <div class="toolbar" style="margin-top:-4px"><label class="field">From (m)<input id="range-from" type="number" min="0" step="1" style="width:90px"></label>
          <label class="field">To (m)<input id="range-to" type="number" min="0" step="1" style="width:90px"></label>
          <button id="range-go" type="button" class="secondary">Summarise this range</button><button id="range-all" type="button" class="ghost">Whole hole</button>
          <span class="muted small">…or drag up or down on the strip log to choose a range; click once to inspect one metre.</span></div>
        <div id="strip" class="strip-wrap"></div>
        <div id="strip-legend" class="legend"></div>
      </section>
      <aside class="grid" style="align-content:start">
        <section class="panel" id="interval"><h2>Depth detail</h2><p class="muted">Click the strip log to inspect one metre, or drag to summarise a depth range.</p></section>
        <section class="panel" id="versions"></section>
        <section class="panel" id="similar-holes"><h2>Similar holes</h2><p class="muted">Loading…</p></section>
      </aside>
    </div>`;
  $('#select').onclick = () => { selection.toggle(h.hole_id); $('#select').textContent = selection.has(h.hole_id) ? 'Remove from selection' : 'Add to selection'; };
  $('#similar-btn').onclick = () => { selection.add([h.hole_id]); go(`similarity/${encodeURIComponent(h.hole_id)}`); };
  $('#range-go').onclick = () => { const f = Number($('#range-from').value), t = Number($('#range-to').value); if (t > f) showRange(f, t); else toast('“To” must be deeper than “From”'); };
  $('#range-all').onclick = () => { const [f, t] = extent(); showRange(f, t); };
  drawStrip();
  drawVersions(h);
  similarHoles(h.hole_id);
  const match = /^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$/.exec(rangeSpec || '');
  if (match) showRange(Number(match[1]), Number(match[2]));
}

function extent() {
  const depths = Object.values(strip.tracks).flatMap(t => t.intervals.flatMap(r => [r.depth_from_m, r.depth_to_m]));
  return depths.length ? [Math.floor(Math.min(...depths)), Math.ceil(Math.max(...depths))] : [0, 1];
}

function versionTracks(tracks) {
  const v = strip.tsa_versions;
  if (!showVersions || v?.status !== 'available') return;
  for (const product of ['swir', 'tir', 'vnir']) {
    const rows = v.rows.filter(r => r.product === product);
    if (!rows.length) continue;
    const [label, s, u] = PRODUCT[product];
    const describe = which => r => `${which === 'system' ? `${s} (automatic)` : `${u} (reviewed)`}<br>${ranked(r.row[`${which}_minerals`], 4).map(([m, x]) => `${esc(m)} ${pct(x)}`).join('<br>')}
      <br><span class="muted">first mineral agrees in ${pct(r.row.min1_agreement)} of readings</span>`;
    tracks.push({ label: `${label} auto`, title: `${s}: automatic (system) TSA interpretation`, width: 70, kind: 'stack', color: groupColor,
      rows: rows.map(r => ({ f: r.f, t: r.t, parts: r.system_groups, row: r })), describe: describe('system') });
    tracks.push({ label: `${label} reviewed`, title: `${u}: user TSA interpretation (the version NVCL publishes)`, width: 70, kind: 'stack', color: groupColor,
      rows: rows.map(r => ({ f: r.f, t: r.t, parts: r.user_groups, row: r })), describe: describe('user') });
    tracks.push({ label: 'Agree', title: 'Share of readings whose first mineral is the same in both versions (full bar = 100%)', width: 34, kind: 'line', min: 0, max: 1,
      rows: rows.map(r => ({ f: r.f, t: r.t, value: r.min1_agreement, color: r.min1_agreement >= .8 ? '#4f9a5b' : r.min1_agreement >= .5 ? '#e6a23c' : '#a63a32', row: r })),
      describe: r => `${pct(r.value)} of readings agree on the first mineral<br>automatic: ${esc(r.row.system_top)} · reviewed: ${esc(r.row.user_top)}` });
  }
}

function drawStrip(highlight) {
  const tracks = [];
  const depths = [];
  const sensorLabel = { swir: 'SWIR', tir: 'TIR', vnir: 'VNIR' };
  for (const [sensor, track] of Object.entries(strip.tracks)) {
    const rows = track.intervals.map(r => ({ f: r.depth_from_m, t: r.depth_to_m, parts: r.groups, row: r, sensor }));
    rows.forEach(r => depths.push(r.f, r.t));
    const names = track.cluster_names;
    tracks.push({ label: `${sensorLabel[sensor]} minerals`, title: `Mineral-group mix per metre (published, reviewed interpretation). ${TERMS[sensor]}`, width: sensor === 'vnir' ? 70 : 130, kind: 'stack', rows, color: groupColor,
      describe: r => `${ranked(r.row.top_minerals).map(([m, v]) => `${esc(m)} ${pct(v)}`).join('<br>') || 'No diagnostic reading'}<br><span class="muted">${r.row.n_admitted}/${r.row.n_total} readings diagnostic</span>` });
    if (sensor !== 'vnir' && track.run_id) {
      tracks.push({ label: 'Clus.', title: `${sensorLabel[sensor]} interval cluster: metres with a similar mineral mix across all holes`, width: 30, kind: 'ribbon', rows: track.intervals.filter(r => r.cluster_id != null).map(r => ({ f: r.depth_from_m, t: r.depth_to_m, value: r.cluster_id, row: r, sensor })),
        describe: r => `Cluster ${r.value}: ${esc(names[`fine:${r.value}`] || '')}<br>Group ${r.row.coarse_cluster_id}: ${esc(names[`coarse:${r.row.coarse_cluster_id}`] || '')}` });
      tracks.push({ label: 'Dom.', title: 'Domains: contiguous depth zones with a consistent mineral mix in this hole', width: 30, kind: 'ribbon', rows: track.domains.map((d, i) => ({ f: d.depth_from_m, t: d.depth_to_m, value: d.cluster_id, color: i % 2 ? '#8fa3ad' : '#c3d0d6', row: d, sensor })),
        describe: r => `Domain ${r.row.domain_id + 1}: ${num(r.row.depth_from_m)}–${num(r.row.depth_to_m)} m<br>${ranked(r.row.minerals, 4).map(([m, v]) => `${esc(m)} ${pct(v)}`).join('<br>')}` });
      tracks.push({ label: 'Unusual', title: 'Unusualness of each metre (percentile; long bar = more unusual than most metres in the dataset)', width: 40, kind: 'line', min: 0.5, max: 1, rows: track.intervals.filter(r => r.anomaly_score != null).map(r => ({ f: r.depth_from_m, t: r.depth_to_m, value: r.anomaly_score, color: scoreColor(r.anomaly_score), row: r, sensor })),
        describe: r => `More unusual than ${pct(r.value)} of metres${r.row.anomaly_reasons?.length ? `<br>${r.row.anomaly_reasons.map(esc).join('<br>')}` : ''}` });
    }
  }
  versionTracks(tracks);
  for (const [sensor, table] of Object.entries(strip.raw || {})) {
    if (!table.bin) continue;
    const rows = table.bin.map((b, i) => ({ f: table.depth_from_m[i], t: table.depth_to_m[i], i }));
    if (sensor === 'nir' && table.aloh_2200_pos) {
      tracks.push({ label: 'Al-OH nm', title: 'Raw spectra: 2200 nm Al-OH absorption position (white mica chemistry: low = muscovitic/paragonitic, high = phengitic)', width: 50, kind: 'line', min: 2190, max: 2225,
        rows: rows.map(r => ({ ...r, value: table.aloh_2200_depth[r.i] > 0.02 ? table.aloh_2200_pos[r.i] : null, color: '#6fa8dc' })), describe: r => `Al-OH position ${num(r.value, 1)} nm<br>depth ${num(table.aloh_2200_depth[r.i], 3)}` });
    }
    if (table.spectral_class) {
      tracks.push({ label: `Raw ${sensor.toUpperCase()} class`, title: 'Spectral class of the raw 1 m spectrum (clustered independently of TSG)', width: 34, kind: 'ribbon', rows: rows.filter(r => table.spectral_class[r.i] != null).map(r => ({ ...r, value: table.spectral_class[r.i] })), describe: r => `Spectral class ${r.value}` });
    }
  }
  const predictable = Object.fromEntries((strip.virtual_skill || []).map(r => [r.analyte, r]));
  const available = [...new Set([...strip.analytes, ...(strip.virtual.length ? Object.keys(predictable) : [])])];
  const analytes = chosenAnalytes || DEFAULT_ANALYTES.filter(a => available.includes(a)).slice(0, 4);
  for (const analyte of analytes) {
    const values = strip.assays.filter(a => a[analyte] != null);
    const predicted = showPredicted && predictable[analyte] ? strip.virtual.filter(v => v[analyte] != null) : [];
    if (!values.length && !predicted.length) continue;
    const sorted = (values.length ? values : predicted).map(v => v[analyte]).sort((a, b) => a - b);
    const max = sorted[Math.floor(sorted.length * .98)] || sorted[sorted.length - 1];
    const log = ['Au', 'Cu', 'Zn', 'Pb', 'Ni', 'As', 'S', 'Cr', 'U', 'Th'].includes(analyte);
    tracks.push({ label: analyte, title: `Assay ${analyte} (${log ? 'ppm, log' : 'wt%'})`, width: 46, kind: 'line', log, min: log ? Math.max(sorted[0], 1e-3) : 0, max: Math.max(max, 1e-6), fill: '#b5502a',
      rows: values.map(v => ({ f: v.f, t: v.t, value: v[analyte] })), describe: r => `${analyte} ${num(r.value, log ? 3 : 2)} ${log ? 'ppm' : '%'} (measured)` });
    values.forEach(v => depths.push(v.f, v.t));
    if (predicted.length) {
      const skill = predictable[analyte];
      tracks.push({ label: `${analyte} pred.`, title: `${analyte} predicted from mineralogy (virtual assay; R² ${skill.r2_holdout_holes} on held-out holes)`, width: 46, kind: 'line', log, min: log ? Math.max(sorted[0], 1e-3) : 0, max: Math.max(max, 1e-6), fill: '#2f8f83',
        rows: predicted.map(v => ({ f: v.f, t: v.t, value: v[analyte] })), describe: r => `${analyte} ≈ ${num(r.value, log ? 1 : 2)} ${log ? 'ppm' : '%'} predicted<br><span class="muted">virtual assay · typical error ×${skill.median_abs_error_factor}</span>` });
    }
  }
  if (strip.lithology.length) {
    tracks.push({ label: 'Logged geology', title: 'Company lithology (not harmonised) and GSWA rock name where provided', width: 110, kind: 'text',
      rows: strip.lithology.map(l => ({ f: l.f, t: l.t, value: l.code || l.name, label: l.rock || l.name || l.code, row: l })),
      describe: r => `${esc(r.row.code || '')} ${esc(r.row.name || '')}<br>${esc(r.row.rock || '')} ${esc(r.row.regolith || '')}<br><span class="muted">${esc(r.row.comments || '')}</span>` });
  }
  if (!tracks.length) { $('#strip').innerHTML = '<p class="empty">No prepared data for this hole yet. Use the Data tab to download and process it.</p>'; return; }
  const depthMin = Math.max(0, Math.min(...depths)), depthMax = Math.max(...depths);
  const controls = $('#strip-controls');
  const versionsOn = strip.tsa_versions?.status === 'available';
  controls.innerHTML = `<label class="field">Scale<select id="scale"><option value="1">1 px/m</option><option value="2">2 px/m</option><option value="4">4 px/m</option><option value="8">8 px/m</option></select></label>
    ${available.length ? `<label class="field">Chemistry shown<select id="analytes" multiple size="1" style="min-width:160px">${available.map(a => `<option ${analytes.includes(a) ? 'selected' : ''}>${a}</option>`).join('')}</select></label>` : ''}
    ${strip.virtual.length ? `<label class="small"><input type="checkbox" id="predicted" ${showPredicted ? 'checked' : ''}> predicted chemistry (virtual assays)</label>` : ''}
    ${versionsOn ? `<label class="small"><input type="checkbox" id="show-versions" ${showVersions ? 'checked' : ''}> automatic vs reviewed ${info(VERSIONS_HELP)}</label>` : ''}
    ${strip.analytes.length ? '' : '<span class="muted small">No assays linked to this hole.</span>'}
    <span class="muted small">${num(depthMax - depthMin)} m · hover for values</span>`;
  const scale = Number(sessionStorage.getItem('strip-scale') || (depthMax - depthMin > 800 ? 1 : 2));
  $('#scale').value = String(scale);
  $('#scale').onchange = e => { try { sessionStorage.setItem('strip-scale', e.target.value); } catch { /* ignore */ } drawStrip(highlight); };
  if ($('#analytes')) $('#analytes').onchange = e => { chosenAnalytes = [...e.target.selectedOptions].map(o => o.value); drawStrip(highlight); };
  if ($('#predicted')) $('#predicted').onchange = e => { showPredicted = e.target.checked; drawStrip(highlight); };
  if ($('#show-versions')) $('#show-versions').onchange = e => { showVersions = e.target.checked; drawStrip(highlight); };
  const sv = strip.survey;
  const tvd = sv ? d => (d <= sv.md[sv.md.length - 1] ? interp(d, sv.md, sv.tvd) : sv.tvd[sv.tvd.length - 1] + (d - sv.md[sv.md.length - 1]) * sv.tail) : null;
  chart?.destroy?.();
  chart = stripLog($('#strip'), { tracks, depthMin, depthMax, pxPerMetre: Number($('#scale').value), highlight, tvd,
    onPick: hit => { if (hit.row?.row?.feature_set_id) inspect(hit.row.row); },
    onRange: (f, t) => showRange(Math.floor(f), Math.ceil(t)) });
  const groups = new Set(); Object.values(strip.tracks).forEach(t => t.intervals.forEach(r => Object.keys(r.groups || {}).forEach(g => groups.add(g))));
  $('#strip-legend').innerHTML = [...groups].sort().map(g => `<span><i style="background:${groupColor(g)}"></i>${esc(title(g))}</span>`).join('');
  if (highlight) chart.scrollTo(highlight.f);
}

/* ------------------------------------------------- automatic vs reviewed */
function drawVersions(h) {
  const node = $('#versions');
  const v = strip.tsa_versions || {};
  const head = `<h2>Automatic vs reviewed ${info(VERSIONS_HELP)}</h2>`;
  if (v.status === 'available') {
    node.innerHTML = head + Object.entries(v.summary || {}).map(([product, s]) => {
      const [label, sys, user] = PRODUCT[product] || [product.toUpperCase(), 's', 'u'];
      return `<h3 style="margin-top:8px">${label} · ${sys} vs ${user}</h3>
        <p class="small">The first mineral is the same in <b>${pct(s.min1_agreement)}</b> of readings and the mineral mixes overlap <b>${pct(s.mix_overlap)}</b>.
        The main mineral of the metre differs in <b>${num(s.metres_main_mineral_changed)}</b> of ${num(s.metres)} metres.</p>
        ${s.top_changes.length ? `<table class="mini-table"><thead><tr><th>Automatic said</th><th>Reviewed says</th><th class="num">Metres</th></tr></thead><tbody>
          ${s.top_changes.map(([a, b, n]) => `<tr><td>${esc(a)}</td><td>${esc(b)}</td><td class="num">${n}</td></tr>`).join('')}</tbody></table>` : ''}`;
    }).join('') + '<p class="muted small" style="margin-top:6px">The “auto”, “reviewed” and “Agree” tracks in the strip log show where they differ.</p>';
  } else if (v.status === 'needs_download') {
    node.innerHTML = `${head}<p class="small">Both versions are stored only in the raw spectral file, which was removed after this hole was summarised to save disk space.</p>
      <button type="button" class="secondary" id="fetch-versions">Download the raw spectra again to compare</button>
      <p class="muted small">Runs in the background (usually a few minutes); reload this page when the job in the Data tab has finished.</p>`;
    $('#fetch-versions').onclick = async () => {
      try {
        await api('/api/v2/jobs', { method: 'POST', body: { kind: 'raw_fetch', params: { holes: [h.hole_id], mode: 'spectral_only', force: true, discard_source: true } } });
        toast('Queued: raw spectra download for this hole'); $('#fetch-versions').disabled = true;
      } catch (error) { toast(error.message); }
    };
  } else {
    node.innerHTML = `${head}<p class="small muted">No raw spectral package is available for this hole, so only the reviewed interpretation published by NVCL exists.</p>`;
  }
}

/* ------------------------------------------------------------ one metre */
async function inspect(row) {
  drawStrip({ f: row.depth_from_m, t: row.depth_to_m });
  const node = $('#interval'); loading(node);
  const d = await api(`/api/v2/intervals/${row.feature_set_id}/${row.bin}`);
  const i = d.interval;
  const minerals = ranked(i.minerals, 8);
  const version = strip.tsa_versions?.rows?.find(r => r.product === i.sensor && r.f === i.depth_from_m);
  node.innerHTML = `<h2>${esc(i.hole_id)} · ${num(i.depth_from_m, 1)}–${num(i.depth_to_m, 1)} m <span class="muted small">${i.sensor.toUpperCase()}</span></h2>
    <p class="muted small">${i.n_admitted} of ${i.n_total} readings diagnostic · mean TSG error ${num(i.error_mean, 0)}</p>
    ${bars(minerals, mineralColor)}
    ${version ? `<p class="small" style="margin-top:8px">Automatic: <b>${esc(version.system_top)}</b> · reviewed: <b>${esc(version.user_top)}</b> · first mineral agrees in ${pct(version.min1_agreement)} of readings.</p>` : ''}
    ${d.label ? `<dl class="kv" style="margin-top:10px"><dt>Cluster</dt><dd><span class="swatch" style="background:${clusterColor(d.label.cluster_id)}"></span>${d.label.cluster_id} (group ${d.label.coarse_cluster_id})</dd>
      <dt>Domain</dt><dd>${d.label.domain_id + 1}</dd><dt>Unusualness</dt><dd>more unusual than ${pct(d.label.anomaly_score)} of metres ${(d.label.anomaly_reasons || []).map(r => `<span class="chip warn">${esc(r)}</span>`).join(' ')}</dd></dl>` : ''}
    ${d.spectrum ? `<h3 style="margin-top:10px">Raw ${d.spectrum.sensor.toUpperCase()} spectrum of this metre</h3>${lineChart({ x: d.spectrum.wavelengths, series: [{ values: d.spectrum.values, color: '#0f6e68', name: 'median of the readings in this metre' }], marks: d.spectrum.sensor === 'nir' ? NIR_MARKS : [], xLabel: 'Wavelength (nm)', width: 340, height: 180 })}` : ''}
    <h3 style="margin-top:10px">Most similar metres in other holes</h3>
    ${d.similar.length ? `<table><tbody>${d.similar.slice(0, 8).map(s => `<tr><td><button class="link" data-hole="${esc(s.hole_id)}" data-range="${num(s.depth_from_m)}-${num(s.depth_to_m)}">${esc(s.hole_id)}</button></td><td class="num">${num(s.depth_from_m)}–${num(s.depth_to_m)} m</td><td class="num">${pct(1 - s.distance)}</td></tr>`).join('')}</tbody></table><p class="muted small">${esc(d.similar_method)}</p>` : '<p class="muted">None found.</p>'}`;
  node.onclick = event => { const link = event.target.closest('[data-hole]'); if (link) go(`hole/${encodeURIComponent(link.dataset.hole)}`); };
}

/* ------------------------------------------------------------ a range */
async function showRange(f, t) {
  $('#range-from').value = f; $('#range-to').value = t;
  history.replaceState(null, '', `#/hole/${encodeURIComponent(holeId)}/${f}-${t}`);
  drawStrip({ f, t });
  const node = $('#interval'); loading(node, `Summarising ${num(f)}–${num(t)} m…`);
  let d;
  try { d = await api(`/api/v2/holes/${encodeURIComponent(holeId)}/range?from=${f}&to=${t}`); } catch (error) { failure(node, error); return; }
  const label = { swir: 'SWIR', tir: 'TIR', vnir: 'VNIR' };
  const sensors = Object.entries(d.sensors).map(([s, v]) => `<h3 style="margin-top:10px">${label[s]} minerals ${info(TERMS[s])}</h3>
    ${stack(v.groups, groupColor)}<div style="margin-top:6px">${bars(ranked(v.minerals, 6), mineralColor)}</div>
    <p class="muted small">${num(v.diagnostic_metres)} of ${num(v.metres)} metres diagnostic${v.clusters.length ? ` · clusters: ${v.clusters.slice(0, 3).map(([id, name, share]) => `<span class="swatch" style="background:${clusterColor(id)}"></span>${esc(name || `cluster ${id}`)} ${pct(share)}`).join(', ')}` : ''}${v.unusual_metres ? ` · ${v.unusual_metres} unusual metres` : ''}</p>`).join('');
  const versions = d.tsa_versions ? Object.entries(d.tsa_versions).map(([product, v]) => `<h3 style="margin-top:10px">${(PRODUCT[product] || [product])[0]} automatic vs reviewed</h3>
    <div class="compare-cols small"><div><b>Automatic</b>${bars(ranked(v.system, 4), mineralColor)}</div><div><b>Reviewed</b>${bars(ranked(v.user, 4), mineralColor)}</div></div>
    <p class="muted small">First mineral agrees in ${pct(v.min1_agreement)} of readings; main mineral differs in ${v.changed_metres} of ${v.metres} metres.</p>`).join('') : '';
  const spectra = Object.entries(d.spectra || {}).map(([sensor, s]) => {
    const item = s.items?.[0]; if (!item?.mean) return '';
    const aloh = item.features?.aloh_2200_pos;
    return `<h3 style="margin-top:10px">Raw ${sensor === 'nir' ? 'NIR/SWIR' : 'TIR'} spectrum ${info(s.method)}</h3>
      ${lineChart({ x: s.wavelengths, bands: [{ low: item.p10, high: item.p90, color: '#0f6e68', name: '10–90% of metres' }], series: [{ values: item.mean, color: '#0f6e68', name: `mean of ${num(item.metres)} m` }], marks: sensor === 'nir' ? NIR_MARKS : [], xLabel: 'Wavelength (nm)', width: 340, height: 170 })}
      ${sensor === 'nir' && aloh ? `<p class="small">Al-OH absorption at <b>${num(aloh, 1)} nm</b> ${info('Below ~2198 nm: sodic/muscovitic white mica; above ~2206 nm: phengitic (Fe/Mg-rich).')}</p>` : ''}`;
  }).join('');
  const assays = d.assays.length ? `<h3 style="margin-top:10px">Assays in this range</h3><table class="mini-table"><thead><tr><th>Analyte</th><th class="num">Samples</th><th class="num">Median</th><th class="num">Max</th></tr></thead><tbody>
    ${d.assays.map(a => `<tr><td>${esc(a.analyte)}</td><td class="num">${a.n}</td><td class="num">${num(a.median, 2)} ${a.unit}</td><td class="num">${num(a.max, 2)} ${a.unit}</td></tr>`).join('')}</tbody></table>` : '';
  const rocks = {}; d.lithology.forEach(l => { const k = l.rock || 'unnamed'; rocks[k] = (rocks[k] || 0) + (l.t - l.f); });
  const lithology = Object.keys(rocks).length ? `<h3 style="margin-top:10px">Logged geology</h3><p class="small">${ranked(rocks, 6).map(([k, m]) => `${esc(k)} <span class="muted">${num(m)} m</span>`).join(' · ')}</p>` : '';
  node.innerHTML = `<h2>${esc(holeId)} · ${num(d.from)}–${num(d.to)} m <span class="muted small">${num(d.to - d.from)} m</span></h2>
    ${sensors || '<p class="muted">No processed mineral data in this range.</p>'}${versions}${spectra}${assays}${lithology}
    <p class="muted small" style="margin-top:8px">Link to this range: <code>#/hole/${esc(holeId)}/${num(d.from)}-${num(d.to)}</code></p>`;
}

async function similarHoles(id) {
  const node = $('#similar-holes');
  const d = await api(`/api/v2/holes/${encodeURIComponent(id)}/similar?view=combined&limit=10`).catch(() => ({ items: [] }));
  node.innerHTML = `<h2>Similar holes ${info(TERMS.similarity)}</h2>${d.items.length ? `<div class="similar-list">${d.items.map((s, i) => `<div class="similar-item"><span class="rank">${i + 1}</span>
      <div><button class="link" data-hole="${esc(s.hole_id)}">${esc(s.hole_id)}</button> <span class="muted small">${pct(1 - s.distance)} similar</span>
      <ul>${(s.why || []).slice(0, 3).map(w => `<li>${esc(w)}</li>`).join('')}</ul></div></div>`).join('')}</div>
    <p class="muted small" style="margin-top:6px">Mineral mix, SWIR + TIR. <a href="#/map/${encodeURIComponent(id)}">See them on the map</a>.</p>` : '<p class="muted">Not analysed yet.</p>'}`;
  node.onclick = event => { const hole = event.target.closest('[data-hole]')?.dataset.hole; if (hole) go(`hole/${encodeURIComponent(hole)}`); };
}

export function leave() { chart?.destroy?.(); chart = null; }

export { categoryColor };

function interp(x, xs, ys) {
  for (let i = 1; i < xs.length; i += 1) if (x <= xs[i]) return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / ((xs[i] - xs[i - 1]) || 1);
  return ys[ys.length - 1];
}
