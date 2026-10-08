import { $, api, esc, num, pct, loading, failure } from '../util.js';
import { scoreColor } from '../palette.js';
import { go } from '../main.js';

let sensor = 'swir';
const REASONS = { poor_fit: 'Poor fit to its cluster', isolation: 'Isolated in feature space', downhole_contrast: 'Unlike the metres around it', rare_mineral: 'Contains a rare mineral', unlike_other_holes: 'Unlike any other hole' };

export async function render(root) {
  root.innerHTML = `<div class="page-head"><div><h1>Unusual intervals and holes</h1>
    <p class="muted">Review candidates ranked by how unusual they are within this dataset, with the reasons. Not evidence of mineralisation or of an error.</p></div>
    <div class="toolbar"><label class="field">Sensor<select id="sensor"><option value="swir">SWIR</option><option value="tir">TIR</option></select></label>
    <label class="field">Reason<select id="reason"><option value="">Any</option>${Object.entries(REASONS).slice(0, 4).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
    <label class="field">Commodity<input id="commodity" placeholder="e.g. gold"></label>
    <button id="apply" type="button">Apply</button></div></div><div id="body"></div>`;
  $('#sensor').value = sensor;
  $('#apply').onclick = draw; $('#sensor').onchange = e => { sensor = e.target.value; draw(); };
  draw();
}

async function draw() {
  const body = $('#body'); loading(body);
  const q = new URLSearchParams({ sensor, limit: '300' });
  if ($('#reason').value) q.set('reason', $('#reason').value);
  if ($('#commodity').value.trim()) q.set('commodity', $('#commodity').value.trim());
  let d; try { d = await api(`/api/v2/anomalies?${q}`); } catch (error) { failure(body, error); return; }
  body.innerHTML = `<div class="grid cols-main-side">
    <section class="panel"><h2>Intervals</h2><p class="muted small">${esc(d.card?.meaning || '')}. Reasons appear when a component is in the top ${pct(1 - (d.card?.reason_percentile ?? .98))}.</p>
      <div class="table-wrap"><table><thead><tr><th>Hole</th><th class="num">Depth m</th><th class="num">Score</th><th>Reasons</th><th>Main mineral</th><th>Commodity</th></tr></thead><tbody>
      ${d.intervals.map(r => `<tr><td><button class="link" data-hole="${esc(r.hole_id)}">${esc(r.hole_id)}</button></td><td class="num">${num(r.depth_from_m)}–${num(r.depth_to_m)}</td>
        <td class="num"><span class="swatch" style="background:${scoreColor(r.anomaly_score)}"></span>${pct(r.anomaly_score)}</td>
        <td>${(r.anomaly_reasons || []).map(x => `<span class="chip warn" title="${esc(x)}">${esc(REASONS[x.split(':')[0]] || x)}${x.includes(':') ? `: ${esc(x.split(':')[1])}` : ''}</span>`).join(' ')}</td>
        <td>${esc(r.dominant_mineral || '')}</td><td class="small">${esc(r.commodity || '')}</td></tr>`).join('')}</tbody></table></div></section>
    <aside class="grid" style="align-content:start">
      <section class="panel"><h2>Most unusual holes</h2><table><tbody>${d.holes.slice(0, 15).map(h => `<tr><td><button class="link" data-hole="${esc(h.hole_id)}">${esc(h.hole_id)}</button><br><span class="muted small">${esc(h.commodity || '')}</span></td><td class="num">${pct(h.anomaly_score)}</td></tr>`).join('')}</tbody></table>
        <p class="muted small">Mean distance to the five most similar holes, as a percentile.</p></section>
      <section class="panel"><h2>Data-quality flags</h2><p class="muted small">Holes whose share of diagnostic readings is far from the rest (robust z &gt; 3.5). Often a different processing level or instrument, not geology.</p>
        ${(d.data_quality || []).length ? `<table><tbody>${d.data_quality.map(q => `<tr><td><button class="link" data-hole="${esc(q.hole_id)}">${esc(q.hole_id)}</button></td><td class="num">${pct(q.support)}</td><td class="num">z ${num(q.support_z, 1)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">None.</p>'}</section>
    </aside></div>`;
  body.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
}
