import { $, api, esc, num, bytes, toast, loading, failure } from '../util.js';
import { selection } from '../state.js';
import { go } from '../main.js';

let timer = null, rows = [], downloadScope = 'selected', actionsVersion = 0, stopSelection = null;
let analyzeAfter = true, fullZip = false, discardSource = false;

export async function render(root) {
  root.innerHTML = `<div class="page-head"><div><h1>Data: download, process, analyse</h1>
    <p class="muted">Search the catalogue, choose holes, then fetch processed logs from the NVCL API or raw spectra from NCI THREDDS. Jobs run in the background worker and can be cancelled.</p></div></div>
    <section class="panel" id="autofill" style="margin-bottom:14px"><h2>Automatic collection</h2><p>Loading progress…</p></section>
    <div class="grid cols-main-side">
      <section class="panel">
        <div class="toolbar">
          <label class="field" style="flex:1;min-width:180px">Search<input id="q" type="search" placeholder="Hole, project, operator"></label>
          <label class="field">Commodity<input id="commodity" placeholder="gold, nickel…"></label>
          <label class="field">Region<select id="region"><option value="">All</option></select></label>
          <label class="field">Status<select id="status"><option value="">Any</option><option value="not_fetched">Not fetched</option><option value="prepared">Prepared</option><option value="no_logs">No public logs</option><option value="failed">Failed</option><option value="summarised">Raw summarised</option></select></label>
          <label class="field"><span><input id="assays" type="checkbox"> with assays</span></label>
          <button id="search" type="button">Search</button>
        </div>
        <div class="row gap" style="margin-bottom:8px"><button id="add-all" class="secondary" type="button">Add all shown to selection</button><span id="count" class="muted small"></span></div>
        <div class="table-wrap" id="catalog"></div>
      </section>
      <aside class="grid" style="align-content:start">
        <section class="panel" id="actions"></section>
        <section class="panel" id="storage"><h2>Storage</h2><p class="muted">Loading…</p></section>
      </aside>
    </div>
    <section class="panel" style="margin-top:14px"><div class="row gap" style="justify-content:space-between"><h2>Jobs</h2><span class="muted small">Refreshes automatically while jobs run</span></div><div id="jobs"></div><div id="job-detail"></div></section>`;
  $('#search').onclick = search;
  $('#q').onkeydown = e => { if (e.key === 'Enter') search(); };
  $('#add-all').onclick = () => { selection.add(rows.map(r => r.hole_id)); toast(`${rows.length} holes added`); actions(); };
  stopSelection?.();
  stopSelection = selection.on(() => actions());
  await search();
  actions(); storage(); jobs();
  timer = setInterval(jobs, 3000);
}

async function search() {
  const node = $('#catalog'); loading(node);
  const q = new URLSearchParams({ limit: '800' });
  if ($('#q').value.trim()) q.set('search', $('#q').value.trim());
  if ($('#commodity').value.trim()) q.set('commodity', $('#commodity').value.trim());
  if ($('#region').value) q.set('region', $('#region').value);
  if ($('#status').value) q.set('status', $('#status').value === 'summarised' ? 'summarised' : $('#status').value);
  if ($('#assays').checked) q.set('assays_only', 'true');
  let d; try { d = await api(`/api/v2/catalog?${q}`); } catch (error) { failure(node, error); return; }
  if ($('#region').options.length === 1) $('#region').innerHTML += d.regions.map(r => `<option>${esc(r)}</option>`).join('');
  rows = d.rows;
  $('#count').textContent = `${num(rows.length)} holes shown${rows.length === 800 ? ' (first 800)' : ''}`;
  node.innerHTML = `<table><thead><tr><th></th><th>Hole</th><th>Commodity</th><th>Region</th><th class="num">Assays</th><th>Processed</th><th>Raw</th><th class="num">Raw ZIP</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td><input type="checkbox" data-pick="${esc(r.hole_id)}" ${selection.has(r.hole_id) ? 'checked' : ''} aria-label="Select ${esc(r.hole_id)}"></td>
      <td><button class="link" data-hole="${esc(r.hole_id)}">${esc(r.hole_id)}</button><br><span class="muted small">${esc(r.project || r.operator || '')}</span></td>
      <td class="small">${esc(r.commodity || '')}</td><td class="small">${esc(r.tectonic_region || '')}</td><td class="num">${num(r.n_assay)}</td>
      <td><span class="chip ${r.processed_status === 'prepared' ? 'accent' : r.processed_status === 'no_logs' ? 'warn' : ''}">${esc(r.nvcl_available ? r.processed_status : 'not in NVCL')}</span></td>
      <td><span class="chip ${String(r.raw_status).startsWith('summarised') ? 'accent' : ''}">${esc(r.raw_available ? r.raw_status : 'no package')}</span></td>
      <td class="num small">${r.raw_size_bytes ? bytes(r.raw_size_bytes) : '–'}</td></tr>`).join('')}</tbody></table>`;
  node.onchange = event => { const id = event.target.dataset.pick; if (id) { event.target.checked ? selection.add([id]) : selection.remove(id); } };
  node.onclick = event => { const id = event.target.closest('[data-hole]')?.dataset.hole; if (id) go(`hole/${encodeURIComponent(id)}`); };
}

async function actions() {
  const node = $('#actions'); if (!node) return;
  if ($('#analyze')) analyzeAfter = $('#analyze').checked;
  if ($('#full-zip')) fullZip = $('#full-zip').checked;
  if ($('#discard')) discardSource = $('#discard').checked;
  const version = ++actionsVersion, scope = downloadScope, holes = selection.list();
  const all = scope === 'all_catalogue';
  const scopeParams = all ? { scope } : { scope, holes };
  let e = null, error = '';
  const draw = () => {
    node.innerHTML = `<h2>Download data</h2>
      <label class="field" for="download-scope">Download scope<select id="download-scope"><option value="selected" ${all ? '' : 'selected'}>Selected holes (${num(holes.length)})</option><option value="all_catalogue" ${all ? 'selected' : ''}>All catalogue (missing data)</option></select></label>
      <p class="muted small">${all ? 'Uses the complete catalogue, including holes outside the displayed table and search. Skips prepared logs, known no-public logs and existing raw summaries.' : 'Uses your current selection. Selected processed logs can be fetched again.'}</p>
      ${e ? `<p class="small">${num(e.holes)} catalogue holes in this scope.</p><dl class="kv">
        <dt>Processed (NVCL)</dt><dd>${num(e.processed.holes)} available · ${num(e.processed.already_prepared)} ready · ${num(e.processed.no_public_logs)} no public logs · ${num(e.processed.pending)} missing</dd>
        <dt>Processed download</dt><dd>${num(e.processed.download_holes)} holes · ~${num(e.processed.estimated_minutes, 0)} min · ~${bytes(e.processed.estimated_bytes)}</dd>
        <dt>Raw (NCI)</dt><dd>${num(e.raw.holes)} available · ${num(e.raw.already_summarised)} summarised · ${num(e.raw.pending)} missing</dd>
        <dt>Raw download</dt><dd>${num(e.raw.download_holes)} holes · ZIPs ${bytes(e.raw.zip_bytes)}</dd>
        <dt>Spectra only</dt><dd>~${bytes(e.raw.spectral_only_transfer_bytes)} transfer · ~${bytes(e.raw.spectral_only_disk_bytes)} on disk (≈${num(e.raw.estimated_minutes_spectral_only, 0)} min)</dd>
        <dt>Summary mode</dt><dd>~${bytes(e.raw.summary_bytes)} kept</dd><dt>Free disk</dt><dd>${bytes(e.free_bytes)}</dd></dl><p class="muted small">${esc(e.estimate_note)}</p>` : `<p class="muted">${esc(error || (holes.length || all ? 'Loading source counts and estimates…' : 'Select holes, or choose All catalogue to acquire missing data.'))}</p>`}
      <div class="grid" style="margin-top:10px">
        <button type="button" data-job="processed_fetch" ${e?.processed.download_holes ? '' : 'disabled'}>Fetch processed logs (NVCL API) + prepare${e ? ` (${num(e.processed.download_holes)})` : ''}</button>
        <label class="small"><input type="checkbox" id="analyze" ${analyzeAfter ? 'checked' : ''}> then re-run clusters, similarity and prediction (re-fits all prepared holes as a new version)</label>
        <button type="button" data-job="raw_fetch" class="secondary" ${e?.raw.download_holes ? '' : 'disabled'}>Fetch raw spectra + summarise${e ? ` (${num(e.raw.download_holes)})` : ''}</button>
        <label class="small"><input type="checkbox" id="full-zip" ${fullZip ? 'checked' : ''}> download the full ZIP (includes core imagery; much larger)</label>
        <label class="small"><input type="checkbox" id="discard" ${discardSource ? 'checked' : ''}> summary mode: delete spectra after summarising (keeps ~3 MB per hole)</label>
        <hr style="border:0;border-top:1px solid var(--line);width:100%">
        <button type="button" data-job="analysis_refresh" class="secondary">Re-run analysis on all prepared holes</button>
        <button type="button" data-job="catalog_refresh" class="ghost">Refresh catalogue from NVCL/THREDDS</button>
        <button type="button" data-job="reference_load" class="ghost">Reload GSWA reference package</button>
      </div>`;
    node.onchange = event => {
      if (event.target.id === 'analyze') analyzeAfter = event.target.checked;
      if (event.target.id === 'full-zip') fullZip = event.target.checked;
      if (event.target.id === 'discard') discardSource = event.target.checked;
      if (event.target.id === 'download-scope') { downloadScope = event.target.value; actions(); }
    };
    node.onclick = async event => {
      const kind = event.target.closest('[data-job]')?.dataset.job; if (!kind) return;
      const params = {};
      if (kind === 'processed_fetch') Object.assign(params, scopeParams, { analyze: analyzeAfter, parallel: 4 });
      if (kind === 'raw_fetch') Object.assign(params, scopeParams, { mode: fullZip ? 'full_zip' : 'spectral_only', discard_source: discardSource, analyze: analyzeAfter, parallel: 2 });
      if (['processed_fetch', 'raw_fetch'].includes(kind) && !e) return;
      if (kind === 'analysis_refresh') Object.assign(params, { mode: 'retrain', promote: true });
      if (kind === 'raw_fetch' && discardSource && !confirm('Summary mode deletes the downloaded spectra after summarising. Continue?')) return;
      try { const job = await api('/api/v2/jobs', { method: 'POST', body: { kind, params } }); toast(`Job ${job.job_id.slice(0, 8)} queued`); jobs(); }
      catch (failure) { toast(failure.message); }
    };
  };
  draw();
  if (!holes.length && !all) return;
  try { e = await api('/api/v2/estimate', { method: 'POST', body: scopeParams }); }
  catch (failure) { error = failure.message; }
  if (version !== actionsVersion || !node.isConnected) return;
  draw();
}

async function storage() {
  const s = await api('/api/v2/storage').catch(() => null); if (!s) return;
  $('#storage').innerHTML = `<h2>Storage</h2><p class="muted small">${esc(s.data_dir)}</p><dl class="kv">${Object.entries(s.folders).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${bytes(v)}</dd>`).join('')}<dt>Free</dt><dd>${bytes(s.free_bytes)} of ${bytes(s.total_bytes)}</dd></dl>`;
}

async function jobs() {
  const node = $('#jobs'); if (!node) { clearInterval(timer); return; }
  const list = await api('/api/v2/jobs?limit=25').catch(() => null); if (!list) return;
  const summary = await api('/api/v2/summary').catch(() => null);
  await automatic();
  const workers = summary?.workers || [];
  const banner = workers.length
    ? `<p class="small"><span class="chip accent">Worker online</span> ${workers.map(w => esc(w.lanes)).join(' · ')}</p>`
    : `<div class="notice">No worker is running, so queued jobs will wait. Start it with <code>scripts/start_worker.ps1</code>.</div>`;
  node.innerHTML = banner + (list.length ? `<div class="table-wrap" style="max-height:340px"><table><thead><tr><th>Job</th><th>Kind</th><th>Status</th><th>Progress</th><th>Created</th><th></th></tr></thead><tbody>
    ${list.map(j => { const p = j.progress || {}; const share = p.total ? p.done / p.total : (j.status === 'succeeded' ? 1 : 0);
      return `<tr><td><button class="link" data-detail="${j.job_id}">${j.job_id.slice(0, 8)}</button></td><td>${esc(j.kind)}${j.params?.holes_total || j.params?.holes ? ` <span class="muted small">${num(j.params.holes_total || j.params.holes.length)} holes</span>` : ''}</td>
      <td class="status ${j.status}">${esc(j.status)}</td><td><div class="progress"><span style="width:${100 * share}%"></span></div><span class="muted small">${p.total ? `${num(p.done)}/${num(p.total)} · ${num(p.failed || 0)} failed` : esc(p.stage || '')}</span></td>
      <td class="small">${new Date(j.created_at).toLocaleString()}</td><td>${['queued', 'running'].includes(j.status) ? `<button class="ghost" data-cancel="${j.job_id}">Cancel</button>` : ''}</td></tr>`; }).join('')}</tbody></table></div>` : '<p class="muted">No jobs yet.</p>');
  node.onclick = async event => {
    const cancel = event.target.closest('[data-cancel]')?.dataset.cancel;
    if (cancel) { await api(`/api/v2/jobs/${cancel}/cancel`, { method: 'POST' }); jobs(); }
    const detail = event.target.closest('[data-detail]')?.dataset.detail;
    if (detail) showJob(detail);
  };
}

async function automatic() {
  const node = $('#autofill'); if (!node) return;
  const s = await api('/api/v2/autofill').catch(() => null);
  if (!s) { node.innerHTML = '<h2>Automatic collection</h2><p>Progress unavailable. Check the local service.</p>'; return; }
  node.innerHTML = `<div class="row gap" style="justify-content:space-between"><h2>Automatic collection</h2>
    <span class="chip ${s.control.enabled ? 'accent' : ''}">${s.control.enabled ? 'Enabled' : 'Paused'}</span></div>
    <p class="muted">Build this computer's collection from the public Western Australia catalogue. Download, process and resume automatically while Explorer is open. Progress survives closing the app.</p>
    <div class="table-wrap"><table><thead><tr><th>Source</th><th>Available</th><th>Ready</th><th>Pending</th><th>Working</th><th>Failed / partial</th><th>No usable public data</th></tr></thead><tbody>
    ${Object.entries(s.sources).map(([source,c]) => `<tr><th>${source === 'raw' ? 'Raw spectra' : 'Processed TSG'}</th><td>${num(c.total)}</td><td>${num(c.complete)}</td><td>${num(c.pending)}</td><td>${num(c.running)}</td><td>${num(c.failed + c.partial)}</td><td>${num(c.unavailable)}</td></tr>`).join('')}</tbody></table></div>
    <p class="small">Raw retention: QC-admitted median spectra per metre, wavelength grids, depths and quality reports. Original readings are reduced; source files are removed only after all available sensors validate. Images are not requested.</p>
    <p class="muted small">${esc(s.control.message)}${s.control.catalog_checked_at ? ` · Catalogue checked ${new Date(s.control.catalog_checked_at).toLocaleString()}` : ''}</p>
    <div class="row gap"><button id="auto-toggle">${s.control.enabled ? 'Pause collection' : 'Start / resume collection'}</button>
      <button id="auto-retry" class="secondary">Retry failed / refresh catalogue</button></div>
    ${s.problems.length ? `<details><summary>${s.problems.length} recent problems (automatic retries are limited)</summary><ul>${s.problems.map(p => `<li>${esc(p.hole_id)} · ${esc(p.source)} · attempt ${p.attempts}/3: ${esc(p.error || p.state)}</li>`).join('')}</ul></details>` : ''}`;
  $('#auto-toggle').onclick = () => controlAuto(!s.control.enabled, false);
  $('#auto-retry').onclick = () => controlAuto(true, true);
}

async function controlAuto(enabled, retry) {
  try { await api('/api/v2/autofill', {method:'POST', body:{enabled,retry}}); await automatic(); }
  catch(error) { toast(error.message); }
}

async function showJob(id) {
  const d = await api(`/api/v2/jobs/${id}`);
  const failed = d.job.result?.failed || {};
  $('#job-detail').innerHTML = `<h3 style="margin-top:12px">Job ${id.slice(0, 8)} · ${esc(d.job.kind)} · ${esc(d.job.status)}</h3>
    ${d.job.error ? `<div class="notice danger">${esc(d.job.error)}</div>` : ''}
    ${Object.keys(failed).length ? `<details open><summary>${Object.keys(failed).length} holes failed</summary><pre>${esc(Object.entries(failed).map(([h, e]) => `${h}: ${e}`).join('\n'))}</pre></details>` : ''}
    <details><summary>Log (${d.logs.length} lines)</summary><pre>${esc(d.logs.map(l => `${l.ts.slice(11, 19)} ${l.level} ${l.message}`).join('\n'))}</pre></details>
    <details><summary>Parameters and result</summary><pre>${esc(JSON.stringify({ params: d.job.params, result: d.job.result }, null, 2)).slice(0, 20000)}</pre></details>`;
}

export function leave() { clearInterval(timer); stopSelection?.(); stopSelection = null; actionsVersion++; }
