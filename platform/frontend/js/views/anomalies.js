import { $, api, esc, num, pct, bars, ranked, loading, failure } from '../util.js';
import { scoreColor, mineralColor, groupColor } from '../palette.js';
import { stripLog, lineChart } from '../charts.js';
import { go } from '../main.js';
import { info, NIR_MARKS } from '../glossary.js';

let sensor = 'swir', context = null;
const REASONS = {
  poor_fit: ['Poor fit to its cluster', 'Its mineral mix is far from the typical metre of the cluster it was assigned to.'],
  isolation: ['Rare combination of minerals', 'An Isolation Forest separates it from all other metres with very few random splits: few metres anywhere look like it.'],
  downhole_contrast: ['Unlike the metres around it', 'Its mix differs sharply from the 5 m above and below: a thin band, a contact, a vein, or a logging artefact.'],
  rare_mineral: ['Contains a rare mineral', 'At least 10% of the metre is a mineral found in fewer than 0.2% of all metres.'],
  chemistry_mismatch: ['Assay disagrees with the mineralogy', 'Where assays exist, the measured value is far from what the minerals predict (virtual assay).'],
  unlike_other_holes: ['Unlike every other hole', 'Even its five most similar holes are far away in fingerprint terms.'],
};
const rank = s => s == null ? '–' : s >= 0.999 ? 'top 0.1%' : `top ${Math.max(0.1, (1 - s) * 100).toFixed(1)}%`;
const chip = r => { const [key, detail] = r.split(':'); return `<span class="chip warn" data-tip="${esc(REASONS[key]?.[1] || r)}">${esc(REASONS[key]?.[0] || key)}${detail ? `: ${esc(detail)}` : ''}</span>`; };

export async function render(root) {
  root.innerHTML = `<div class="page-head"><div><h1>Unusual metres and holes</h1>
    <p class="muted">Metres and holes that stand out from the rest of the dataset, with the evidence for each. Click any row to see why.</p></div></div>
    <section class="panel" style="margin-bottom:14px"><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">
      <div><h3>The score</h3><p class="small">Every metre goes through four checks. Each check is turned into a percentile across all metres of the sensor, and the score is their average. <b>“Top 0.5%”</b> means the metre is more unusual than 99.5% of all metres. A reason is listed when one check on its own is in the top 2%.</p></div>
      <div><h3>The checks</h3><ul class="small" style="margin:0;padding-left:18px">${Object.entries(REASONS).slice(0, 5).map(([, [name, text]]) => `<li><b>${esc(name)}</b>: ${esc(text)}</li>`).join('')}</ul></div>
      <div><h3>What it is not</h3><p class="small">Unusual is not the same as mineralised or wrong. It is a list of places worth a look: some are real geology (veins, alteration halos, rare lithologies), others are data problems (mislabelled depths, a different processing level). The evidence on the right helps tell them apart.</p></div>
    </div></section>
    <div class="toolbar"><label class="field">Sensor<select id="sensor"><option value="swir">SWIR</option><option value="tir">TIR</option></select></label>
      <label class="field">Reason<select id="reason"><option value="">Any</option>${Object.entries(REASONS).slice(0, 5).map(([k, [v]]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
      <label class="field">Company target<input id="commodity" placeholder="e.g. gold"></label>
      <button id="apply" type="button">Apply</button></div>
    <div class="grid cols-main-side"><div id="lists" class="grid" style="align-content:start"></div>
      <aside class="panel side-panel" id="why"><h2>Why is it unusual?</h2><p class="muted">Click a metre or a hole in the lists to see the evidence: what the check found, the metres around it and its raw spectrum.</p></aside></div>`;
  $('#sensor').value = sensor;
  $('#apply').onclick = draw; $('#sensor').onchange = e => { sensor = e.target.value; draw(); };
  draw();
}

async function draw() {
  const node = $('#lists'); loading(node);
  const q = new URLSearchParams({ sensor, limit: '300' });
  if ($('#reason').value) q.set('reason', $('#reason').value);
  if ($('#commodity').value.trim()) q.set('commodity', $('#commodity').value.trim());
  let d; try { d = await api(`/api/v2/anomalies?${q}`); } catch (error) { failure(node, error); return; }
  node.innerHTML = `<section class="panel"><h2>Unusual metres ${info('Sorted by score. Hover a reason for its definition; click a row for the evidence.')}</h2>
      <p class="muted small">${num(d.intervals.length)} shown${d.intervals.length >= 300 ? ' (the 300 highest)' : ''}.</p>
      <div class="table-wrap"><table><thead><tr><th>Hole</th><th class="num">Depth</th><th>Unusualness</th><th>Why</th><th>Main mineral</th><th>Company target</th></tr></thead><tbody>
      ${d.intervals.map(r => `<tr data-fs="${esc(r.feature_set_id)}" data-bin="${r.bin}" style="cursor:pointer"><td><b>${esc(r.hole_id)}</b></td><td class="num">${num(r.depth_from_m)}–${num(r.depth_to_m)} m</td>
        <td class="num"><span class="swatch" style="background:${scoreColor(r.anomaly_score)}"></span>${rank(r.anomaly_score)}</td>
        <td>${(r.anomaly_reasons || []).map(chip).join(' ')}</td><td>${esc(r.dominant_mineral || '')}</td><td class="small">${esc(r.commodity || '')}</td></tr>`).join('')}</tbody></table></div></section>
    <section class="panel"><h2>Unusual holes ${info(REASONS.unlike_other_holes[1] + ' Based on the SWIR + TIR mineral-mix comparison.')}</h2>
      <div class="table-wrap" style="max-height:360px"><table><thead><tr><th>Hole</th><th>Unusualness</th><th>Company target</th><th>Region</th></tr></thead><tbody>
      ${d.holes.slice(0, 25).map(h => `<tr data-hole="${esc(h.hole_id)}" style="cursor:pointer"><td><b>${esc(h.hole_id)}</b></td><td><span class="swatch" style="background:${scoreColor(h.anomaly_score)}"></span>${rank(h.anomaly_score)}</td><td class="small">${esc(h.commodity || '')}</td><td class="small">${esc(h.tectonic_region || '')}</td></tr>`).join('')}</tbody></table></div></section>
    <section class="panel"><h2>Data-quality flags ${info('Holes whose share of usable (diagnostic) readings is far from the other holes (robust z-score above 3.5). Usually a different instrument, processing level or sample type rather than geology; treat their clusters and scores with care.')}</h2>
      ${(d.data_quality || []).length ? `<table><thead><tr><th>Hole</th><th class="num">Usable readings</th><th class="num">How far from typical</th></tr></thead><tbody>${d.data_quality.map(q => `<tr data-open="${esc(q.hole_id)}" style="cursor:pointer"><td>${esc(q.hole_id)}</td><td class="num">${pct(q.support)}</td><td class="num">${num(q.support_z, 1)} robust SD</td></tr>`).join('')}</tbody></table>` : '<p class="muted">None.</p>'}</section>`;
  node.onclick = event => {
    const row = event.target.closest('tr');
    if (!row) return;
    node.querySelectorAll('tr.on').forEach(r => r.classList.remove('on')); row.classList.add('on');
    if (row.dataset.fs) explainInterval(row.dataset.fs, row.dataset.bin);
    else if (row.dataset.hole) explainHole(row.dataset.hole);
    else if (row.dataset.open) go(`hole/${encodeURIComponent(row.dataset.open)}`);
  };
}

async function explainInterval(fs, bin) {
  const node = $('#why'); loading(node, 'Gathering the evidence…');
  let d; try { d = await api(`/api/v2/anomalies/interval/${fs}/${bin}`); } catch (error) { failure(node, error); return; }
  const i = d.interval;
  const from = Math.max(0, i.depth_from_m - 10), to = i.depth_to_m + 10;
  node.innerHTML = `<h2>${esc(i.hole_id)} · ${num(i.depth_from_m)}–${num(i.depth_to_m)} m <span class="muted small">${i.sensor.toUpperCase()}</span></h2>
    <p><span class="swatch" style="background:${scoreColor(d.score)}"></span><b>${rank(d.score)}</b> of all ${i.sensor.toUpperCase()} metres ${info(d.meaning)}</p>
    <h3 style="margin-top:10px">Why it is flagged</h3>
    ${d.evidence.map(e => `<div class="notice" style="margin:6px 0"><b>${esc(e.title)}</b><br><span class="small">${esc(e.text || '')}</span></div>`).join('') || '<p class="muted small">Score only (no single check in the top 2%).</p>'}
    <h3 style="margin-top:10px">This metre</h3>${bars(ranked(d.minerals, 6), mineralColor)}
    <p class="muted small">Cluster: ${esc(d.cluster.name || d.cluster.id)}</p>
    <h3 style="margin-top:10px">Metres around it (±10 m)</h3><div id="why-strip" class="strip-wrap" style="max-height:none"></div>
    ${d.spectrum ? `<h3 style="margin-top:10px">Raw spectrum of this metre</h3>${lineChart({ x: d.spectrum.wavelengths, series: [{ values: d.spectrum.values, color: '#0f6e68', name: 'median of the readings in this metre' }], marks: d.spectrum.sensor === 'nir' ? NIR_MARKS : [], xLabel: 'Wavelength (nm)', width: 340, height: 170 })}` : ''}
    <div class="row gap" style="margin-top:12px"><button type="button" id="open-here">Open the strip log here</button><a class="button secondary" href="#/map/${encodeURIComponent(i.hole_id)}">Show on map</a></div>`;
  $('#open-here').onclick = () => go(`hole/${encodeURIComponent(i.hole_id)}/${Math.floor(from)}-${Math.ceil(to)}`);
  context?.destroy?.();
  context = stripLog($('#why-strip'), {
    tracks: [
      { label: 'Minerals', width: 150, kind: 'stack', color: groupColor, rows: d.context.map(r => ({ f: r.depth_from_m, t: r.depth_to_m, parts: r.groups, row: r })),
        describe: r => ranked(r.row.minerals, 4).map(([m, v]) => `${esc(m)} ${pct(v)}`).join('<br>') },
      { label: 'Unusual', width: 60, kind: 'line', min: 0.5, max: 1, rows: d.context.filter(r => r.anomaly_score != null).map(r => ({ f: r.depth_from_m, t: r.depth_to_m, value: r.anomaly_score, color: scoreColor(r.anomaly_score), row: r })),
        describe: r => `${rank(r.value)}${r.row.anomaly_reasons?.length ? `<br>${r.row.anomaly_reasons.map(x => esc(REASONS[x.split(':')[0]]?.[0] || x)).join('<br>')}` : ''}` },
    ],
    depthMin: Math.min(...d.context.map(r => r.depth_from_m)), depthMax: Math.max(...d.context.map(r => r.depth_to_m)), pxPerMetre: 9,
    highlight: { f: i.depth_from_m, t: i.depth_to_m },
  });
}

async function explainHole(holeId) {
  const node = $('#why'); loading(node, 'Gathering the evidence…');
  let d; try { d = await api(`/api/v2/anomalies/hole/${encodeURIComponent(holeId)}`); } catch (error) { failure(node, error); return; }
  node.innerHTML = `<h2>${esc(d.hole_id)}</h2>
    <p><b>${rank(d.percentile)}</b> of holes by isolation ${info(d.meaning)}</p>
    <div class="notice"><b>${esc(REASONS.unlike_other_holes[0])}</b><br><span class="small">On average its five most similar holes are at distance <b>${num(d.mean_distance_to_5_nearest, 2)}</b>; for a typical hole that figure is ${num(d.typical_mean_distance, 2)} (0 = identical fingerprints, 1 = nothing in common).</span></div>
    ${d.distinctive.length ? `<h3 style="margin-top:10px">What makes it different</h3><table class="mini-table"><thead><tr><th>Mineral</th><th class="num">This hole</th><th class="num">Typical hole</th></tr></thead><tbody>
      ${d.distinctive.map(m => `<tr><td>${esc(m.mineral)} <span class="muted small">${esc(m.sensor)}</span></td><td class="num">${pct(m.share)}</td><td class="num">${pct(m.typical, 1)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted small" style="margin-top:8px">No single mineral stands out; the combination is what is unusual.</p>'}
    <h3 style="margin-top:10px">Its closest holes</h3><table class="mini-table"><tbody>${d.neighbours.map(n => `<tr><td><button class="link" data-hole="${esc(n.hole_id)}">${esc(n.hole_id)}</button></td><td class="num">${pct(1 - n.distance)} similar</td><td class="num muted">${num(n.km)} km</td></tr>`).join('')}</tbody></table>
    <div class="row gap" style="margin-top:12px"><button type="button" data-hole="${esc(d.hole_id)}">Open strip log</button><a class="button secondary" href="#/map/${encodeURIComponent(d.hole_id)}">Show on map</a></div>`;
  node.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
}

export function leave() { context?.destroy?.(); context = null; }
