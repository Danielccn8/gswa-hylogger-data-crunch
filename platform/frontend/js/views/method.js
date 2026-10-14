import { $, api, esc, num, pct } from '../util.js';

export async function render(root) {
  const d = await api('/api/v2/method');
  const runs = Object.fromEntries(d.runs.map(r => [`${r.kind}:${r.sensor}`, r]));
  const swir = runs['interval:swir']?.card, tir = runs['interval:tir']?.card;
  const pred = runs['prediction:swir']?.card?.summary;
  const combined = runs['hole:combined']?.card;
  const modelNotes = [['SWIR', swir], ['TIR', tir]].map(([label, card]) => {
    const q = card?.data_quality, p = q?.policy;
    return p ? `${label}: ${num(q.eligible_intervals)} eligible / ${num(q.descriptive_intervals)} descriptive intervals; ${num(q.excluded_intervals)} excluded. Requires at least ${p.min_admitted} diagnostic readings and fraction ${p.min_diagnostic_fraction}. Primary-frequency allowed: ${p.allow_primary_frequency}; unknown representation allowed: ${p.allow_unknown_representation}.` : `${label}: active model has no corrected policy card (legacy or unavailable).`;
  }).join(' ');
  const geometryNotes = [['SWIR', swir], ['TIR', tir]].map(([label, card]) => `${label}: ${card?.clustering?.algorithm || 'active model algorithm unavailable'}`).join(' ');
  const rawNotes = ['raw_nir', 'raw_tir'].map(view => `${view}: ${runs[`hole:${view}`]?.card?.distance || 'unavailable'}`).join('; ');
  const virtual = runs['virtual_assay:combined']?.card;
  const virtualSkill = (virtual?.skill || []).filter(v => Number.isFinite(v.r2_holdout_holes));
  const virtualNotes = virtualSkill.length ? `${virtualSkill.length} analytes evaluated on held-out holes; recorded R² ${Math.min(...virtualSkill.map(v => v.r2_holdout_holes)).toFixed(2)} to ${Math.max(...virtualSkill.map(v => v.r2_holdout_holes)).toFixed(2)}. See active run card for analyte coverage and offered predictions.` : 'No held-hole skill summary in the active card.';
  root.innerHTML = `<div class="page-head"><div><h1>Data &amp; method</h1><p class="muted">How every number in this application is produced, and what it does not mean.</p></div></div>
  <div class="grid cols-2">
    <section class="panel"><h2>1 · Data</h2><ul>
      <li><b>Processed TSG logs</b> from the NVCL Data Services API: up to three mineral components (Min1–3) with weights (Wt1–3) per reading, for SWIR (uTSAS), TIR (ujCLST) and VNIR (uTSAV). Logs are fetched one per request (multi-log requests fail on the WA server) and joined only after checking identical depth rows.</li>
      <li><b>Raw spectra</b> from NCI THREDDS. Only the spectral members of each ZIP are read with HTTP byte ranges (≈14% of the package), CRC-checked, then summarised to 1 m medians under Final Mask, with absorption-feature positions (e.g. Al-OH 2200 nm).</li>
      <li><b>GSWA interim package</b>: ${d.reference ? `separately imported index, collars, surveys, assays (${num(d.reference.row_counts?.ref_assay)} intervals) and geology. Not QA/QC'd; internal use only.` : 'not installed. Assays, logged geology and survey-based validation are unavailable; public spectra and mineral logs work independently.'}</li></ul></section>
    <section class="panel"><h2>2 · Preparation</h2><ul>
      <li>Every reading keeps its original row; invalid depths, masked readings and non-diagnostic readings (Aspectral, INVALID) and not-rock artefacts (NOTAROK: marker pen, core tray, wood, vegetation) are counted separately and never treated as minerals.</li>
      <li>Each reading's component weights are normalised to one; a 1 m interval's <b>abundance</b> is their mean over its diagnostic readings. It is a relative interpretation weight, <b>not mass percent</b>.</li>
      <li>${esc(modelNotes)} This support policy is not a calibrated accuracy threshold. Masks remain unknown where not recorded.</li></ul></section>
    <section class="panel"><h2>3 · Clusters, domains and anomalies</h2><ul>
      <li>Hellinger geometry (square root of abundance); hole-balanced training sample (≤150 intervals per hole) so long holes do not dominate.</li>
      <li>Fine clusters: K-Means (10 starts), K = smallest within 0.02 of the best silhouette with no cluster under 0.5% (Sathi's rule). SWIR K=${swir?.clustering?.k_fine ?? '–'}, TIR K=${tir?.clustering?.k_fine ?? '–'}. ${esc(geometryNotes)}</li>
      <li>Names describe the mean mix (“X-dominant” when one mineral is ≥ 50%). Stability, scaling and largest-mineral baselines follow Sathi's and Avantika's diagnostics.</li>
      <li>Domains: penalised optimal segmentation of each hole's depth sequence (≥ 3 m).</li>
      <li>Anomaly score: mean percentile of poor fit to its cluster, Isolation Forest, contrast with ±5 m, plus rare-mineral flags. A review list, not a finding.</li></ul></section>
    <section class="panel"><h2>4 · Similarity, raw whole-hole clusters, prediction</h2><ul>
      <li>Hole fingerprint = share of its intervals in each fine cluster; distance = Jensen–Shannon (Sathi). Active combined view: ${esc(combined?.distance || 'unavailable')}.${combined?.coverage ? ` ${num(combined.coverage.common_holes)} common holes; ${num(combined.coverage.excluded_holes)} outside the shared cohort remain in per-sensor views.` : ' Legacy combined results may include unsupported pairs; corrected common-cohort runs require refresh.'}</li>
      <li>Down-hole sequence similarity: each hole becomes 10 m true-vertical-depth steps of its mineral mix; holes are compared with dynamic time warping (banded), so the order of zones matters while their thickness may differ. Similarity → “Align two holes” shows which zone matches which.</li>
      <li>Automatic vs reviewed: TSG interprets each reading twice — a system run (sTSAS, sjCLST) and a user run (uTSAS, ujCLST) that an analyst may have re-run or edited; NVCL publishes only the user run. Both are read from the raw spectral file and compared metre by metre (Hole tab).</li>
      <li>Similar-hole reasons and anomaly evidence are computed from the stored fingerprints and labels: shared interval clusters, the largest mineral differences, matched down-hole order, and for anomalies the cluster misfit, the contrast with ±5 m, rare-mineral prevalence and assay-vs-prediction gaps.</li>
      <li>Active raw whole-hole distances: ${esc(rawNotes)}. New runs use joint sliced Wasserstein over unwhitened PCA scores; stored legacy distances retain their original name.</li>
      <li>Virtual assays fit chemistry-from-mineralogy models from intervals with paired assays. ${esc(virtualNotes)} Recorded residuals contribute to the chemistry-mismatch review score.</li>
      <li>Robustness is current only when the report links to the exact interval run and matches algorithm, frozen schema and eligible-data fingerprints. Historical reports remain inspectable; their ARI and width results do not validate a later model.</li>
      <li>Prediction = inverse-distance weighting of the 8 nearest holes. Tested by leaving each hole out (skill ${pct(pred?.loho_skill_vs_region)}) and leaving its whole district out (skill ${pct(pred?.lodo_skill_vs_region)}): useful within a few km of data, not as a regional map.</li></ul></section>
  </div>
  <section class="panel" style="margin-top:14px"><h2>Active results</h2><table><thead><tr><th>Kind</th><th>Sensor</th><th>Run</th><th>Created</th><th>Key facts</th></tr></thead><tbody>
    ${d.runs.map(r => `<tr><td>${esc(r.kind)}</td><td>${esc(r.sensor)}</td><td class="small">${esc(r.run_id)}</td><td class="small">${new Date(r.created_at).toLocaleString()}</td><td class="small">${esc(facts(r))}</td></tr>`).join('')}</tbody></table>
    <details style="margin-top:10px"><summary>Full run cards (JSON)</summary><pre>${esc(JSON.stringify(d.runs, null, 2)).slice(0, 60000)}</pre></details></section>
  <section class="panel" style="margin-top:14px"><h2>Credits</h2><p class="small">UWA CITS5553 Group 7. Mineral-abundance tables, K rule, naming, hole fingerprints and unusual-interval ranking adapted from Sathi's sTSAS/uTSAS pipeline; TIR whole-hole diagnostics from Avantika's notebook; raw whole-hole Wasserstein distance from Yeshwanth's raw spectral PCA notebook; TIR code decoding from Jovan's TIR processing. Data © State of Western Australia (DMPE), interim package licence applies.</p></section>`;
}

function facts(run) {
  const c = run.card;
  if (run.kind === 'interval') return `${num(c.intervals)} intervals, ${num(c.holes)} holes, K=${c.clustering?.k_fine}/${c.clustering?.k_coarse}`;
  if (run.kind === 'hole') return `${num(c.holes)} holes, ${c.hole_clustering?.descriptive_container ? 'unassigned: no supported grouping' : `${c.hole_clustering?.k ?? '–'} hole clusters`} · ${c.distance || ''}`;
  if (run.kind === 'prediction') return `LOHO skill ${pct(c.summary?.loho_skill_vs_region)}, LODO ${pct(c.summary?.lodo_skill_vs_region)}, support ${c.summary?.support_limit_km ?? '–'} km`;
  return '';
}
export { $ };
