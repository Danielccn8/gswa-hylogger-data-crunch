import { $, api, esc, num, pct, bars, stack, ranked, loading, failure } from '../util.js';
import { clusterColor, mineralColor } from '../palette.js';
import { lineChart } from '../charts.js';
import { info, TERMS } from '../glossary.js';
import { go } from '../main.js';

let sensor = 'swir', level = 'fine', search = '';
const CHEM = ['SiO2', 'Al2O3', 'Fe2O3T', 'MgO', 'CaO', 'Na2O', 'K2O', 'LOI', 'S', 'Au', 'Cu', 'Ni', 'Zn', 'U', 'Th'];
const PPM = ['Au', 'Cu', 'Ni', 'Zn', 'S', 'U', 'Th'];
const ARI = 'ARI (adjusted Rand index) measures how often two groupings put the same pairs of metres together, corrected for chance: 1 = identical groupings, 0 = no better than random.';
const WHY = {
  'WHITE-MICA|K2O': 'white micas (muscovite, phengite) contain potassium',
  'PLAGIOCLASE|Na2O': 'albite-rich plagioclase carries sodium',
  'CARBONATE|CaO': 'calcite and dolomite are calcium carbonates',
  'CARBONATE|LOI': 'carbonates lose CO₂ on ignition',
  'SERPENTINE|MgO': 'serpentine is a magnesium silicate',
  'OTHER-MGOH|MgO': 'talc and brucite are magnesium minerals',
  'CHLORITE|MgO': 'chlorite holds magnesium and iron',
  'KAOLIN|Al2O3': 'kaolinite is an aluminium clay',
  'AMPHIBOLE|CaO': 'tremolite–actinolite and hornblende contain calcium',
};
const verdict = (value, good, fair) => value == null ? '' : value >= good ? '<span class="chip accent">stable</span>' : value >= fair ? '<span class="chip">moderate</span>' : '<span class="chip warn">fragile</span>';

export async function render(root) {
  root.innerHTML = `<div class="page-head"><div><h1>Interval clusters</h1>
    <p class="muted">Every metre of every hole, grouped by its mineral mix. Use them to find the same mineral assemblage in different holes.</p></div>
    <div class="toolbar"><label class="field">Sensor ${info(`${TERMS.swir} ${TERMS.tir}`)}<select id="sensor"><option value="swir">SWIR</option><option value="tir">TIR</option></select></label>
    <label class="field">Level ${info('Fine clusters are the detailed groups. Broad groups merge similar fine clusters (Ward linkage on their centres), useful for a first overview.')}<select id="level"><option value="fine">Fine clusters</option><option value="coarse">Broad groups</option></select></label>
    <label class="field">Find a mineral<input id="search" type="search" placeholder="e.g. Talc" value="${esc(search)}"></label></div></div>
    <section class="panel" style="margin-bottom:14px"><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">
      <div><h3>What is a cluster?</h3><p class="small">Each metre is described by its mineral mix (from the TSG interpretation). Metres with similar mixes are grouped automatically, across all holes at once, without using location or depth. Each cluster is named after the minerals that dominate it.</p></div>
      <div><h3>How many clusters?</h3><p class="small">The number is chosen by a rule, not by hand: the simplest number of clusters whose separation (silhouette) is within 0.02 of the best, with no cluster under 0.5% of metres.</p></div>
      <div><h3>How to use them</h3><p class="small">Find a cluster below, see where it occurs (depth and holes), then open those holes. In the Hole tab the “Clus.” ribbon shows the clusters down each hole.</p></div>
    </div></section>
    <div id="body"></div>`;
  $('#sensor').value = sensor; $('#level').value = level;
  $('#sensor').onchange = e => { sensor = e.target.value; draw(); };
  $('#level').onchange = e => { level = e.target.value; draw(); };
  $('#search').oninput = e => { search = e.target.value.trim(); filterCards(); };
  draw();
}

function filterCards() {
  const q = search.toLowerCase();
  document.querySelectorAll('.cluster-cards > section').forEach(card => { card.hidden = q && !card.dataset.minerals.includes(q); });
}

async function draw() {
  const body = $('#body'); loading(body);
  let d; try { d = await api(`/api/v2/clusters?sensor=${sensor}`); } catch (error) { failure(body, error); return; }
  const card = d.card, c = card.clustering, v = card.validation || {};
  const profiles = d.profiles.filter(p => p.level === level);
  const coarse = Object.fromEntries(d.profiles.filter(p => p.level === 'coarse').map(p => [p.cluster_id, p.name]));
  const seed = c.seed_ari.reduce((s, x) => s + x, 0) / c.seed_ari.length;
  body.innerHTML = `
    <div class="metrics">
      <div class="metric" data-tip="${esc(TERMS.interval)}"><span>Metres grouped</span><strong>${num(card.intervals)}</strong><small>from ${num(card.holes)} holes</small></div>
      <div class="metric" data-tip="${esc(c.k_rule)}"><span>Clusters</span><strong>${c.k_fine} <small style="display:inline">fine</small> · ${c.k_coarse} <small style="display:inline">broad</small></strong><small>chosen by the silhouette rule</small></div>
      <div class="metric" data-tip="${esc(`The clustering was repeated from different random starts. ${ARI}`)}"><span>Same result from new random starts</span><strong>${seed.toFixed(2)}</strong><small>ARI ${verdict(seed, 0.8, 0.6)}</small></div>
      <div class="metric" data-tip="${esc(`Compared with simply grouping metres by their largest mineral. A value well below 1 means the clusters use the whole mix, not just the main mineral. ${ARI}`)}"><span>Same as “largest mineral”?</span><strong>${c.dominant_mineral_baseline.ari.toFixed(2)}</strong><small>ARI · lower = clusters add information</small></div>
      <div class="metric" data-tip="Metres that have a laboratory assay over the same depth; used to check the clusters against chemistry."><span>Metres with assays</span><strong>${num(v.assay_intervals)}</strong><small>in ${num(v.holes_with_assays)} holes</small></div>
      <div class="metric" data-tip="Domains are contiguous zones down each hole with a consistent mix (penalised segmentation, at least 3 m)."><span>Domains</span><strong>${num(card.domains.count)}</strong><small>median ${num(card.domains.median_length_m)} m long</small></div>
    </div>
    <div class="cluster-cards">${profiles.map(p => clusterCard(p, coarse, d.where?.[level]?.[p.cluster_id])).join('')}</div>
    ${robustSection(d.robustness, seed)}
    <div class="grid cols-2" style="margin-top:14px">
      <section class="panel"><h2>Does the mineralogy agree with the chemistry? ${info('Spearman rank correlation (ρ) between how much of a mineral group a metre has and the assay of the same depth. ρ near +1: more of the mineral, more of the element; near 0: no relation. “Within hole” repeats the test inside each hole, which removes differences between areas. Assays: GSWA package, unverified.')}</h2>
        <p class="muted small">Each row tests a relation we expect from the mineral’s chemistry. A clear positive ρ means the HyLogger mineralogy and the laboratory agree.</p>
        <table><thead><tr><th>Mineral group</th><th>Element</th><th>Why expected</th><th class="num">ρ all metres</th><th class="num">ρ within holes</th><th class="num">Metres</th></tr></thead><tbody>
        ${(v.correlations || []).map(r => `<tr><td>${esc(r.feature)}</td><td>${esc(r.analyte)}</td><td class="small muted">${esc(WHY[`${r.feature}|${r.analyte}`] || '')}</td><td class="num">${strength(r.rho)}</td><td class="num">${r.within_hole_median_rho == null ? '–' : strength(r.within_hole_median_rho)}</td><td class="num">${num(r.n)}</td></tr>`).join('')}</tbody></table></section>
      <section class="panel"><h2>How much chemistry do the clusters explain? ${info('η² (eta squared): the share of the variation of an element (log assay) that is explained just by knowing which cluster a metre belongs to. 0.3 = 30% of the variation.')}</h2>
        <p class="muted small">Knowing only the cluster of a metre, this share of the variation of each element is already explained.</p>
        ${bars((v.cluster_separation || []).slice(0, 12).map(r => [r.analyte, r.eta_squared]), () => 'var(--accent)', { max: 1 })}
        <details style="margin-top:10px"><summary>How the number of clusters was chosen</summary>
        ${lineChart({ x: c.fine_scores.map(s => s.k), series: [{ values: c.fine_scores.map(s => s.silhouette), color: 'var(--accent)', name: 'silhouette (higher = better separated)' }], xLabel: 'Number of clusters (K)', width: 460, height: 180 })}
        <p class="muted small">${esc(c.k_rule)} · scaled-vs-Hellinger agreement ARI ${c.scaled_vs_hellinger_ari} · ${num(c.training_rows)} training metres (balanced across holes).</p></details></section>
    </div>
    ${virtualSection(d.virtual)}
    ${rawSection(d.raw)}`;
  body.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
  filterCards();
}

function strength(rho) {
  const colour = rho >= 0.5 ? 'var(--accent)' : rho >= 0.3 ? 'var(--ink)' : 'var(--muted)';
  return `<span style="color:${colour};font-weight:${rho >= 0.3 ? 600 : 400}">${rho.toFixed(2)}</span>`;
}

function clusterCard(p, coarse, where) {
  const minerals = ranked(p.minerals, 7);
  const chem = p.chemistry || {};
  const depth = where?.depth_p10_p50_p90;
  return `<section class="panel" data-minerals="${esc(Object.keys(p.minerals || {}).join(' ').toLowerCase())}"><h2><span class="swatch" style="background:${clusterColor(p.cluster_id)}"></span>${p.cluster_id} · ${esc(p.name)}</h2>
    <p class="muted small">${num(p.n_intervals)} metres in ${num(p.n_holes)} holes${p.parent_id != null ? ` · broad group ${p.parent_id}: ${esc(coarse[p.parent_id] || '')}` : ''}</p>
    ${stack(p.minerals, mineralColor)}<div style="margin-top:8px">${bars(minerals, mineralColor)}</div>
    ${depth ? `<p class="small" style="margin-top:8px"><b>Depth</b> usually ${num(depth[0])}–${num(depth[2])} m (median ${num(depth[1])} m)</p>` : ''}
    ${where?.holes?.length ? `<p class="small"><b>Most in</b> ${where.holes.map(([h, n]) => `<button class="link" data-hole="${esc(h)}">${esc(h)}</button> <span class="muted">${num(n)} m</span>`).join(' · ')}</p>` : ''}
    ${chem.n_assays ? `<h3 style="margin-top:10px">Median chemistry · ${num(chem.n_assays)} assays ${info('Median of the laboratory assays of the metres in this cluster (GSWA package, unverified).')}</h3><div class="chips">${CHEM.filter(a => chem[a] != null).map(a => `<span class="chip">${a} ${a === 'Au' ? Number(chem[a]).toPrecision(2) : num(chem[a], ['Cu', 'Ni', 'Zn', 'S'].includes(a) ? 0 : ['U', 'Th'].includes(a) ? 1 : 2)}${PPM.includes(a) ? ' ppm' : '%'}</span>`).join('')}</div>` : ''}</section>`;
}

function robustSection(r, seed) {
  if (!r) return '';
  const t = r.district_transfer || {}, w = r.width_sensitivity || {};
  const mantel = Object.values(w.mantel_vs_1m || {});
  const low = mantel.length ? Math.min(...mantel) : null;
  const held = (t.repeats || [])[0];
  return `<section class="panel" style="margin-top:14px"><h2>How robust are these clusters? ${info(ARI)}</h2>
    <p class="muted small">Three checks that the clusters are a property of the data, not of one particular run.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr));margin-top:8px">
      <div class="panel" style="box-shadow:none"><h3>New areas ${verdict(t.mean_ari, 0.8, 0.6)}</h3>
        <p class="small">We hid whole districts (${held ? `about ${num(held.held_holes)} holes, ${num(held.held_districts)} districts` : '~20% of holes'}), rebuilt the clusters without them, then sorted the hidden metres into those clusters. They landed in the same clusters as in the full model with ARI <b>${t.mean_ari ?? '–'}</b> (worst of ${(t.repeats || []).length} repeats: ${t.min_ari ?? '–'}). The clusters should hold for holes from new areas.</p></div>
      <div class="panel" style="box-shadow:none"><h3>Interval width ${verdict(low, 0.8, 0.6)}</h3>
        <p class="small">Repeating everything with ${Object.keys(w.mantel_vs_1m || {}).map(k => `${k} m`).join(', ') || 'other'} intervals instead of 1 m gives hole similarities that agree with the 1 m version at ρ ${mantel.map(x => x.toFixed(2)).join(', ') || '–'}. The choice of 1 m does not drive the results.${w.k_by_width ? ` Number of clusters chosen at each width: ${Object.entries(w.k_by_width).map(([k, n]) => `${k} m → ${n}`).join(', ')}.` : ''}</p></div>
      <div class="panel" style="box-shadow:none"><h3>Random starts ${verdict(seed, 0.8, 0.6)}</h3>
        <p class="small">K-Means starts from random centres. Re-running it from different starts gives the same clusters with ARI <b>${seed.toFixed(2)}</b>, so the result does not depend on luck.</p></div>
    </div></section>`;
}

function virtualSection(v) {
  if (!v || !v.skill) return '';
  const judge = r => r.r2_holdout_holes >= 0.5 ? '<span class="chip accent">good</span>' : r.r2_holdout_holes >= 0.3 ? '<span class="chip">usable</span>' : '<span class="chip warn">not offered</span>';
  const rows = v.skill.slice().sort((a, b) => b.r2_holdout_holes - a.r2_holdout_holes).map(r => `<tr><td>${esc(r.analyte)}</td><td class="num">${r.r2_holdout_holes.toFixed(2)}</td><td class="num">×${r.median_abs_error_factor}</td><td class="num">${num(r.n)}</td><td class="num">${num(r.holes)}</td><td>${judge(r)}</td></tr>`).join('');
  return `<section class="panel" style="margin-top:14px"><h2>Can the mineralogy predict the chemistry? (virtual assays) ${info('A model learns, from metres that have both, how the SWIR + TIR minerals relate to each element. It is then tested on whole holes it never saw. R² is the share of the variation it predicts correctly on those holes (1 = perfect, 0 = no better than the average). “Typical error ×1.8” means predictions are usually within a factor of 1.8 of the assay. Predictions appear in the Hole tab as “pred.” tracks only where R² ≥ 0.3.')}</h2>
    <p class="small">For elements that live in minerals the HyLogger sees (Mg in serpentine and talc, Ca in carbonate and amphibole, K in mica, Na in albite), the mineralogy predicts the chemistry reasonably well in new holes. Elements carried by minerals it cannot see well (gold, uranium, sulphides, fine silica) cannot be predicted.</p>
    <p class="muted small">${esc(v.method)} · ${num(v.assay_intervals)} assay intervals.</p>
    <table><thead><tr><th>Element</th><th class="num">R² on unseen holes</th><th class="num">Typical error</th><th class="num">Samples</th><th class="num">Holes</th><th>Verdict</th></tr></thead><tbody>${rows}</tbody></table></section>`;
}

function rawSection(raw) {
  const blocks = Object.entries(raw || {}).filter(([, r]) => r && r.class_profiles);
  if (!blocks.length) return '<section class="panel" style="margin-top:14px"><h2>Raw spectral classes</h2><p class="muted">No raw spectra summarised yet. Use Data → “Fetch raw spectra” to add holes.</p></section>';
  return blocks.map(([view, r]) => `<section class="panel" style="margin-top:14px"><h2>Raw ${view === 'raw_nir' ? 'NIR/SWIR' : 'TIR'} spectral classes ${info('The same idea as the clusters above, but on the measured spectra themselves instead of the TSG mineral names: each 1 m median spectrum is normalised, reduced with PCA and grouped. Each curve is the average spectrum of one class.')}</h2>
    <p class="small">Metres grouped by the shape of their raw spectrum, independently of the TSG interpretation. Each line is the average spectrum of one class. To compare the full spectra of particular holes or zones, use <a href="#/similarity">Compare holes → 6 · Compare raw spectra</a>.</p>
    <p class="muted small">${esc(r.preprocessing)} → PCA (${r.pca_components} components, ${pct(r.pca_variance)} of variance) → ${r.interval_classes} classes · ${num(r.holes)} holes, ${num(r.intervals)} metres.
    ${r.agreement_with_processed ? `Hole similarities from raw spectra agree with the processed view at ρ=${r.agreement_with_processed.spearman_rho} (${r.agreement_with_processed.holes} shared holes).` : ''}</p>
    ${lineChart({ x: r.grid_nm, series: r.class_profiles.map(p => ({ values: p.mean_spectrum, color: clusterColor(p.cluster_id), name: `class ${p.cluster_id} (${num(p.n_intervals)} m)` })), xLabel: 'Wavelength (nm)', width: 900, height: 260 })}
    <div class="table-wrap"><table><thead><tr><th>Class</th><th class="num">Metres</th><th class="num">Holes</th>${Object.keys(r.class_profiles[0].bands).map(b => `<th class="num">${esc(b)}</th>`).join('')}</tr></thead>
    <tbody>${r.class_profiles.map(p => `<tr><td><span class="swatch" style="background:${clusterColor(p.cluster_id)}"></span>${p.cluster_id}</td><td class="num">${num(p.n_intervals)}</td><td class="num">${num(p.n_holes)}</td>${Object.values(p.bands).map(x => `<td class="num">${x == null ? '–' : num(x, Math.abs(x) > 100 ? 0 : 3)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`).join('');
}
