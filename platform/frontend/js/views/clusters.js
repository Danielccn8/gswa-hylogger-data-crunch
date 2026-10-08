import { $, api, esc, num, pct, bars, stack, ranked, loading, failure } from '../util.js';
import { clusterColor, mineralColor } from '../palette.js';
import { lineChart } from '../charts.js';

let sensor = 'swir', level = 'fine';
const CHEM = ['SiO2', 'Al2O3', 'Fe2O3T', 'MgO', 'CaO', 'Na2O', 'K2O', 'LOI', 'S', 'Au', 'Cu', 'Ni', 'Zn'];

export async function render(root) {
  root.innerHTML = `<div class="page-head"><div><h1>Interval clusters</h1>
    <p class="muted">Groups of 1 m intervals with a similar mineral mix, checked against assay chemistry. Fine clusters nest inside broader groups.</p></div>
    <div class="toolbar"><label class="field">Sensor<select id="sensor"><option value="swir">SWIR</option><option value="tir">TIR</option></select></label>
    <label class="field">Level<select id="level"><option value="fine">Fine clusters</option><option value="coarse">Broad groups</option></select></label></div></div>
    <div id="body"></div>`;
  $('#sensor').value = sensor; $('#level').value = level;
  $('#sensor').onchange = e => { sensor = e.target.value; draw(); };
  $('#level').onchange = e => { level = e.target.value; draw(); };
  draw();
}

async function draw() {
  const body = $('#body'); loading(body);
  let d; try { d = await api(`/api/v2/clusters?sensor=${sensor}`); } catch (error) { failure(body, error); return; }
  const card = d.card, c = card.clustering, v = card.validation || {};
  const profiles = d.profiles.filter(p => p.level === level);
  const coarse = Object.fromEntries(d.profiles.filter(p => p.level === 'coarse').map(p => [p.cluster_id, p.name]));
  body.innerHTML = `
    <div class="metrics">
      <div class="metric"><span>Intervals clustered</span><strong>${num(card.intervals)}</strong><small>${num(card.holes)} holes</small></div>
      <div class="metric"><span>Fine clusters / groups</span><strong>${c.k_fine} / ${c.k_coarse}</strong><small>${esc(c.k_rule)}</small></div>
      <div class="metric"><span>Seed stability (ARI)</span><strong>${c.seed_ari.map(x => x.toFixed(2)).join(' · ')}</strong><small>1 = identical across random starts</small></div>
      <div class="metric"><span>vs largest-mineral grouping</span><strong>${c.dominant_mineral_baseline.ari.toFixed(2)}</strong><small>ARI · NMI ${c.dominant_mineral_baseline.nmi.toFixed(2)}</small></div>
      <div class="metric"><span>Assay intervals matched</span><strong>${num(v.assay_intervals)}</strong><small>${num(v.holes_with_assays)} holes with assays</small></div>
      <div class="metric"><span>Domains</span><strong>${num(card.domains.count)}</strong><small>median ${num(card.domains.median_length_m)} m long</small></div>
    </div>
    <div class="cluster-cards">${profiles.map(p => card_(p, coarse)).join('')}</div>
    <div class="grid cols-2" style="margin-top:14px">
      <section class="panel"><h2>Does the mineralogy agree with the chemistry?</h2>
        <p class="muted small">Spearman rank correlation between mineral-group abundance and the assay over the same depth (assays from the GSWA package, unverified).</p>
        <table><thead><tr><th>Mineral group</th><th>Analyte</th><th class="num">ρ all</th><th class="num">ρ within hole</th><th class="num">n</th></tr></thead><tbody>
        ${(v.correlations || []).map(r => `<tr><td>${esc(r.feature)}</td><td>${esc(r.analyte)}</td><td class="num">${r.rho.toFixed(2)}</td><td class="num">${r.within_hole_median_rho == null ? '–' : r.within_hole_median_rho.toFixed(2)}</td><td class="num">${num(r.n)}</td></tr>`).join('')}</tbody></table></section>
      <section class="panel"><h2>How much chemistry do the clusters explain?</h2>
        <p class="muted small">Share of variance in log(analyte) explained by the fine clusters (η²). Higher means the clusters separate that element well.</p>
        ${bars((v.cluster_separation || []).slice(0, 12).map(r => [r.analyte, r.eta_squared]), () => 'var(--accent)', { max: 1 })}
        <details style="margin-top:10px"><summary>Choosing the number of clusters</summary>
        ${lineChart({ x: c.fine_scores.map(s => s.k), series: [{ values: c.fine_scores.map(s => s.silhouette), color: 'var(--accent)', name: 'silhouette (fine K)' }], xLabel: 'K', width: 460, height: 180 })}
        <p class="muted small">Scaled-vs-Hellinger agreement ARI ${c.scaled_vs_hellinger_ari} · training rows ${num(c.training_rows)} (hole-balanced).</p></details></section>
    </div>
    ${rawSection(d.raw)}`;
}

function card_(p, coarse) {
  const minerals = ranked(p.minerals, 7);
  const chem = p.chemistry || {};
  return `<section class="panel"><div class="row gap" style="justify-content:space-between"><h2><span class="swatch" style="background:${clusterColor(p.cluster_id)}"></span>${p.cluster_id} · ${esc(p.name)}</h2></div>
    <p class="muted small">${num(p.n_intervals)} intervals in ${num(p.n_holes)} holes${p.parent_id != null ? ` · group ${p.parent_id}: ${esc(coarse[p.parent_id] || '')}` : ''}</p>
    ${stack(p.minerals, mineralColor)}<div style="margin-top:8px">${bars(minerals, mineralColor)}</div>
    ${chem.n_assays ? `<h3 style="margin-top:10px">Median chemistry · ${num(chem.n_assays)} assays</h3><div class="chips">${CHEM.filter(a => chem[a] != null).map(a => `<span class="chip">${a} ${['Au'].includes(a) ? Number(chem[a]).toPrecision(2) : num(chem[a], ['Cu', 'Ni', 'Zn', 'S'].includes(a) ? 0 : 2)}${['Au', 'Cu', 'Ni', 'Zn', 'S'].includes(a) ? ' ppm' : '%'}</span>`).join('')}</div>` : ''}</section>`;
}

function rawSection(raw) {
  const blocks = Object.entries(raw || {}).filter(([, r]) => r && r.class_profiles);
  if (!blocks.length) return `<section class="panel" style="margin-top:14px"><h2>Raw spectral classes</h2><p class="muted">No raw spectra summarised yet. Use Data → “Fetch raw spectra” to add holes.</p></section>`;
  return blocks.map(([view, r]) => `<section class="panel" style="margin-top:14px"><h2>Raw ${view === 'raw_nir' ? 'NIR/SWIR' : 'TIR'} spectral classes</h2>
    <p class="muted small">${esc(r.preprocessing)} → PCA (${r.pca_components} components, ${pct(r.pca_variance)} variance) → ${r.interval_classes} interval classes · ${num(r.holes)} holes, ${num(r.intervals)} intervals.
    ${r.agreement_with_processed ? `Whole-hole distances agree with the processed view at ρ=${r.agreement_with_processed.spearman_rho} (${r.agreement_with_processed.holes} shared holes).` : ''}</p>
    ${lineChart({ x: r.grid_nm, series: r.class_profiles.map(p => ({ values: p.mean_spectrum, color: clusterColor(p.cluster_id), name: `class ${p.cluster_id} (${num(p.n_intervals)})` })), xLabel: 'Wavelength (nm)', width: 900, height: 260 })}
    <div class="table-wrap"><table><thead><tr><th>Class</th><th class="num">Intervals</th><th class="num">Holes</th>${Object.keys(r.class_profiles[0].bands).map(b => `<th class="num">${esc(b)}</th>`).join('')}</tr></thead>
    <tbody>${r.class_profiles.map(p => `<tr><td><span class="swatch" style="background:${clusterColor(p.cluster_id)}"></span>${p.cluster_id}</td><td class="num">${num(p.n_intervals)}</td><td class="num">${num(p.n_holes)}</td>${Object.values(p.bands).map(x => `<td class="num">${x == null ? '–' : num(x, Math.abs(x) > 100 ? 0 : 3)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`).join('');
}
