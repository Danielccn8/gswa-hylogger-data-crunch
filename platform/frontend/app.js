const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => Number(value || 0).toLocaleString('en-AU');
const pct = value => `${(100 * value).toFixed(1)}%`;
const label = value => String(value || 'Unavailable').toLowerCase().replaceAll('-', ' ').replace(/^./, c => c.toUpperCase());
const ratio = hole => hole.readings ? hole.diagnostic_readings / hole.readings : 0;
const state = {data:null, rows:[], compare:new Set(), view:'overview', hole:null, set:null, model:null, offset:0, next:null};
let generation = 0, profileGeneration = 0, detailGeneration = 0, map, markers;

async function get(url) {
  const response = await fetch(url);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch { /* Non-JSON server response. */ }
    throw new Error(message);
  }
  return response.json();
}
const bar = (name, value, hole = null) => `<div class="bar-row"><span class="bar-label">${hole ? `<button class="link-button" type="button" data-hole="${esc(hole)}">${esc(name)}</button>` : esc(name)}</span><div class="track"><div class="fill" style="width:${Math.max(0,Math.min(100,100*value))}%"></div></div><span class="bar-value">${pct(value)}</span></div>`;
const metric = (name,value,note) => `<div class="metric"><span>${esc(name)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;
function error(message) { $('error').textContent = message; $('error').hidden = !message; }

async function load() {
  const token = ++generation;
  state.data = null; state.rows = []; state.compare.clear(); closeDetail();
  $('connection').textContent = 'Loading…'; error('');
  $('feedback').textContent = 'Reading prepared results…';
  ['metrics','hole-table','group-chart','support-chart','compare-content','method-content'].forEach(id => $(id).replaceChildren());
  try {
    const data = await get(`/api/system/dashboard?region=${$('sensor').value}&cohort=${$('cohort').value}`);
    if (token !== generation) return;
    state.data = data;
    const regions = [...new Set(data.holes.map(h=>h.study_region))].sort();
    $('study-region').innerHTML = '<option value="">All regions</option>' + regions.map(r=>`<option>${esc(r)}</option>`).join('');
    $('connection').textContent = 'Local database connected';
    $('version-note').textContent = `${data.region.toUpperCase()} · ${data.holes.length} active source revisions`;
    render();
  } catch (e) { if (token === generation) { $('connection').textContent = 'Data unavailable'; error(`${e.message}. Check the local API/database, then use Reset filters to retry.`); $('feedback').textContent = ''; } }
}

function render() {
  if (!state.data) return;
  const search = $('search').value.toLowerCase().trim(), region = $('study-region').value;
  state.rows = state.data.holes.filter(h => (!region || h.study_region === region) && (!search || `${h.hole_id} ${h.project || ''}`.toLowerCase().includes(search)));
  const visible = new Set(state.rows.map(h=>h.hole_id));
  state.compare = new Set([...state.compare].filter(id=>visible.has(id)));
  if (state.hole && !visible.has(state.hole)) closeDetail();
  const populated = state.rows.filter(h=>h.diagnostic_intervals>0);
  const total = key => state.rows.reduce((sum,h)=>sum+h[key],0);
  const allCount = state.data.requested_holes.length;
  $('metrics').innerHTML = metric('Visible drill holes',num(state.rows.length),`${state.data.holes.length} prepared / ${allCount} requested`) + metric('Diagnostic intervals',num(total('diagnostic_intervals')),'intervals with usable group labels') + metric('Reading support',pct(total('readings')?total('diagnostic_readings')/total('readings'):0),'diagnostic / valid-depth readings') + metric('Unavailable holes',num(state.data.missing_holes.length),'in the full selected dataset and sensor');
  const groups = [...new Set(populated.flatMap(h=>Object.keys(h.group_frequencies)))];
  const means = groups.map(k=>[k,populated.reduce((sum,h)=>sum+(h.group_frequencies[k]||0),0)/populated.length]).sort((a,b)=>b[1]-a[1]);
  $('group-chart').innerHTML = populated.length ? `<div class="bar-list">${means.slice(0,6).map(([k,v])=>bar(label(k),v)).join('')}</div>` : '<p class="empty">No diagnostic interpretations in this selection.</p>';
  $('support-chart').innerHTML = state.rows.length ? `<div class="bar-list">${[...state.rows].sort((a,b)=>ratio(a)-ratio(b)).slice(0,6).map(h=>bar(h.hole_id,ratio(h),h.hole_id)).join('')}</div>` : '<p class="empty">No prepared readings in this selection.</p>';
  $('feedback').textContent = `${num(state.rows.length)} holes shown · ${state.data.region.toUpperCase()} · filtered statistics use only these holes. ${state.data.missing_holes.length ? `Unavailable in this dataset/sensor: ${state.data.missing_holes.join(', ')}.` : 'All requested holes have a prepared product.'}`;
  renderTable(); renderCompare(); renderMethod(); renderBatch();
  if (state.view === 'map') renderMap();
}

function renderTable() {
  const ordered = [...state.rows].sort((a,b)=>$('sort').value==='support' ? ratio(a)-ratio(b) : $('sort').value==='intervals' ? b.diagnostic_intervals-a.diagnostic_intervals : a.hole_id.localeCompare(b.hole_id));
  $('hole-table').innerHTML = ordered.map(h=>`<tr><td><input type="checkbox" aria-label="Compare ${esc(h.hole_id)}" data-compare="${esc(h.hole_id)}" ${state.compare.has(h.hole_id)?'checked':''}></td><td><button type="button" class="link-button" data-hole="${esc(h.hole_id)}">${esc(h.hole_id)}</button></td><td>${esc(label(h.dominant_group))}</td><td>${num(h.diagnostic_intervals)} / ${num(h.intervals)}</td><td>${pct(ratio(h))}</td><td>${esc(h.study_region)}</td></tr>`).join('');
  $('table-empty').hidden = !!ordered.length;
}
function renderCompare() {
  $('compare-count').textContent = state.compare.size;
  const chosen = state.rows.filter(h=>state.compare.has(h.hole_id));
  if (!chosen.length) { $('compare-content').innerHTML = '<div class="panel empty">Select two to four holes in Overview to compare their interpretation profiles.</div>'; return; }
  const groups = [...new Set(chosen.flatMap(h=>Object.keys(h.group_frequencies)))].sort((a,b)=>chosen.reduce((n,h)=>n+(h.group_frequencies[b]||0)-(h.group_frequencies[a]||0),0)).slice(0,8);
  $('compare-content').innerHTML = `<div class="compare-grid">${chosen.map(h=>`<section class="panel"><h3><button type="button" class="link-button" data-hole="${esc(h.hole_id)}">${esc(h.hole_id)}</button></h3><p class="caption compare-subtitle">${esc(h.study_region)}<br>${num(h.diagnostic_intervals)} diagnostic intervals · ${pct(ratio(h))} reading support</p>${h.diagnostic_intervals ? groups.map(g=>bar(label(g),h.group_frequencies[g]||0)).join('') : '<p class="empty">No diagnostic interpretation; values are unknown.</p>'}<p class="caption footnote">Equal interval weighting · ${esc(state.data.region.toUpperCase())}</p></section>`).join('')}</div>`;
}
function renderMethod() {
  const d=state.data, card=d.model_card;
  const reasons={}; state.rows.forEach(h=>Object.entries(h.primary_reasons).forEach(([k,v])=>reasons[k]=(reasons[k]||0)+v));
  const masks=state.rows.filter(h=>h.mask_status==='available').length;
  const provenance = state.rows.map(h=>({hole:h.hole_id,interval_set:h.interval_set_id,dataset:h.dataset_id,width_m:h.width_m,qc_version:h.qc_version,source_sha256:h.source_sha256}));
  $('method-content').innerHTML = `<div class="method-grid"><section class="panel"><h2>Coverage &amp; preparation</h2><p>${num(state.rows.length)} filtered holes from ${num(d.requested_holes.length)} requested in this dataset.</p><ul><li>Primary TSG group labels aggregated into depth intervals.</li><li>Original source rows retained; repeated depths are not silently deleted.</li><li>Final Mask available: ${masks}/${state.rows.length} visible holes. Its absence limits rock-quality interpretation.</li><li>Missing categories are zero only within a populated interpretation log.</li><li>Raw spectra use a separate adapter with paired spectral and wavelength checks.</li></ul><h3>Reading outcomes · filtered holes</h3><div class="table-wrap"><table><tbody>${Object.entries(reasons).map(([k,v])=>`<tr><td>${esc(label(k.replaceAll('_',' ')))}</td><td>${num(v)}</td></tr>`).join('')}</tbody></table></div><details><summary>Source and preparation versions</summary><pre>${esc(JSON.stringify(provenance,null,2))}</pre></details></section><section class="panel"><h2>Interval clustering</h2>${card ? `<p>${esc(card.clustering)} · ${card.selected_k} interval groups · ${d.region.toUpperCase()}</p><ul><li>${esc(card.representation)}. PCA: ${esc(card.pca)}.</li><li>${esc(card.training_weighting || 'Legacy interval weighting')}.</li><li>Development: ${esc(card.development_holes.join(', '))}.</li><li>Held out: ${esc((card.test_holes || [card.test_hole]).filter(Boolean).join(', ') || 'None')}.</li><li>Held-out silhouette: ${card.test_silhouette == null ? 'unavailable' : card.test_silhouette.toFixed(3)}. Separation in feature space, not geological accuracy.</li></ul><p class="caption">The hole profiles on Overview and Compare summarize original interpretations. Interval cluster IDs appear in depth detail.</p><details><summary>Model card and validation limits</summary><pre>${esc(JSON.stringify(card,null,2))}</pre></details>` : '<p>No active interval model. Descriptive profiles remain available.</p>'}<h3 class="footnote">Interpret with care</h3><p class="caption">Clusters and distances are exploratory. Assay/lithology matching and independent geological review remain separate validation steps. Geographic filters change the displayed population; they do not retrain the model.</p></section></div>`;
}

function renderBatch() {
  const batch=state.data.latest_batch;if(!batch)return;
  const unseen=Object.values(batch.out_of_schema || {}).reduce((a,b)=>a+b,0);
  $('method-content').insertAdjacentHTML('beforeend',`<section class="panel"><h2>Latest processing batch</h2><p>${num(batch.requested_holes.length)} holes requested · ${num(batch.completed.length)} sensor products · ${num(batch.failed.length)} unavailable or failed products · ${num(unseen)} intervals outside the model feature schema.</p><p class="caption footnote">These totals cover the full batch, independently of the display filters. Frozen inference does not retrain the active model.</p><details><summary>Failures and batch record</summary><pre>${esc(JSON.stringify({batch:batch.batch_id,mode:batch.mode,failures:batch.failed,out_of_schema:batch.out_of_schema},null,2))}</pre></details></section>`);
}

function showTab(name) {
  state.view=name;
  const panels={overview:'overview',compare:'compare',map:'map-view',method:'method'};
  Object.entries(panels).forEach(([key,id])=>{ $(id).hidden=key!==name; $(`tab-${key}`).setAttribute('aria-selected',String(key===name)); });
  if(name==='map') renderMap();
}
function renderMap() {
  if(!state.data) return;
  if(!globalThis.L){$('map-status').textContent='Map library unavailable. Use Overview and Compare to inspect the same holes.';return;}
  if(!map){
    map=L.map('map').setView([-26,121],5);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors',maxZoom:19}).on('tileerror',()=>{$('map-status').textContent='Basemap tiles are unavailable. Hole coordinates and other views remain usable.';}).addTo(map);
    markers=L.featureGroup().addTo(map);
  }
  map.invalidateSize();markers.clearLayers();
  const geo=state.rows.filter(h=>Number.isFinite(h.lat)&&Number.isFinite(h.lon));
  $('map-count').textContent=`${geo.length} mapped / ${state.rows.length} visible`;
  $('map-status').textContent=geo.length===state.rows.length?'Markers show prepared holes in the current filters.':`${state.rows.length-geo.length} holes lack usable coordinates.`;
  geo.forEach(h=>{
    const popup=document.createElement('div');
    const heading=document.createElement('strong');heading.textContent=h.hole_id;
    const text=document.createElement('p');text.textContent=`${h.study_region} · ${pct(ratio(h))} reading support`;
    const button=document.createElement('button');button.type='button';button.textContent='Open depth profile';button.onclick=()=>openHole(h.hole_id);
    popup.append(heading,text,button);
    L.circleMarker([h.lat,h.lon],{radius:7,color:'#116e68',weight:2,fillColor:'#68b7a8',fillOpacity:.8}).bindPopup(popup).addTo(markers);
  });
  if(geo.length)map.fitBounds(markers.getBounds().pad(.18),{maxZoom:11});
}

function closeDetail(){state.hole=null;state.set=null;++profileGeneration;++detailGeneration;$('hole-detail').hidden=true;}
async function openHole(id){
  const hole=state.rows.find(h=>h.hole_id===id);if(!hole)return;
  state.hole=id;state.set=hole.interval_set_id;state.model=state.data.model_id;state.offset=0;
  $('depth-from').value='';$('depth-to').value='';$('hole-detail').hidden=false;
  $('detail-title').textContent=id;
  $('detail-context').textContent=`${state.data.region.toUpperCase()} · ${hole.study_region} · ${hole.width_m} m intervals · ${num(hole.intervals)} prepared bins`;
  $('hole-detail').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  await loadProfile();
}
function profileQuery(){
  const q=new URLSearchParams({region:state.data.region,interval_set_id:state.set});
  if(state.model)q.set('model_id',state.model);
  if($('depth-from').value)q.set('from_m',$('depth-from').value);
  if($('depth-to').value)q.set('to_m',$('depth-to').value);
  return q;
}
async function loadProfile(){
  if(!state.hole)return;
  const token=++profileGeneration;++detailGeneration;
  $('profile-status').textContent='Loading interval evidence…';$('profile').replaceChildren();$('export').hidden=true;
  $('interval-detail').innerHTML='<p class="empty">Choose an interval to inspect its evidence.</p>';
  try{
    const q=profileQuery();q.set('limit','120');q.set('offset',state.offset);
    const d=await get(`/api/system/holes/${encodeURIComponent(state.hole)}/intervals?${q}`);
    if(token!==profileGeneration)return;
    state.next=d.next_offset;
    $('previous-page').disabled=state.offset===0;$('next-page').disabled=d.next_offset==null;
    $('profile-status').textContent=d.intervals.length ? `Showing intervals ${state.offset+1}–${state.offset+d.intervals.length}${d.next_offset!=null?' · more available':''}. Export includes all intervals in the applied depth range.` : 'No intervals in this depth range.';
    $('profile').innerHTML=d.intervals.map(i=>`<button type="button" class="interval-row" data-interval="${esc(i.interval_id)}" aria-pressed="false"><span>${i.depth_from_m.toFixed(1)}–${i.depth_to_m.toFixed(1)} m</span><span>${esc(label(i.dominant_group))}</span><small>${i.cluster_id==null?'Unassigned':`Group ${i.cluster_id}`}</small><span>${num(i.n_admitted)}/${num(i.n_total)}</span><span class="track"><span class="fill" style="display:block;width:${i.n_total?100*i.n_admitted/i.n_total:0}%"></span></span></button>`).join('');
    $('profile').scrollTop=0;
    q.delete('limit');q.delete('offset');
    $('export').href=`/api/system/holes/${encodeURIComponent(state.hole)}/export.csv?${q}`;$('export').hidden=!d.intervals.length;
  }catch(e){if(token===profileGeneration)$('profile-status').textContent=`Profile unavailable: ${e.message}`;}
}
async function openInterval(id){
  const token=++detailGeneration;
  document.querySelectorAll('[data-interval]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.interval===id)));
  $('interval-detail').textContent='Loading selected interval…';
  const query=state.model?`?model_id=${encodeURIComponent(state.model)}`:'';
  try{
    const d=await get(`/api/system/intervals/${encodeURIComponent(id)}${query}`);if(token!==detailGeneration)return;
    $('interval-detail').innerHTML=`<h3>${esc(d.hole_id)} · ${d.depth_from_m}–${d.depth_to_m} m</h3><p class="evidence-line">${num(d.n_admitted)} diagnostic / ${num(d.n_total)} source readings in this bin. ${d.assignment?.cluster_id==null?'No compatible model assignment.':`Interval group ${d.assignment.cluster_id}; distance to its centre ${Number(d.assignment.distance).toFixed(3)}.`}</p>${Object.entries(d.features).filter(([,v])=>v!=null).sort((a,b)=>b[1]-a[1]).slice(0,6).map(([k,v])=>bar(label(k),v)).join('')}<details><summary>How this interval was calculated</summary><pre>${esc(JSON.stringify({source:d.dataset_id,interval_set:d.interval_set_id,model:d.model_id,source_rows:d.source_rows,reason_counts:d.reason_counts,mask:d.qc.mask_status,qc_version:d.qc.qc_version},null,2))}</pre></details><h3 class="footnote">Similar intervals</h3><p id="similar-method" class="caption"></p><div id="similar" class="similar-list"></div><div id="spectrum" class="footnote"></div>`;
    const similar=await get(`/api/system/intervals/${encodeURIComponent(id)}/similar${query}`);if(token!==detailGeneration)return;
    $('similar-method').textContent=similar.method;
    $('similar').innerHTML=similar.items.length?similar.items.slice(0,5).map(i=>`<button type="button" class="link-button" data-related="${esc(i.hole_id)}" data-related-depth="${i.depth_from_m}">${esc(i.hole_id)} · ${i.depth_from_m}–${i.depth_to_m} m · distance ${i.distance.toFixed(3)}</button>`).join(''):'<p class="caption">No compatible intervals available.</p>';
    const response=await fetch(`/api/system/spectral/${d.region}/${encodeURIComponent(d.hole_id)}/${d.depth_from_m}`);if(token!==detailGeneration)return;
    if(!response.ok){$('spectrum').innerHTML='<p class="caption">No adjusted spectral pilot is prepared for this interval.</p>';return;}
    const spectral=await response.json();if(token!==detailGeneration)return;
    renderSpectrum(spectral);
  }catch(e){if(token===detailGeneration)$('interval-detail').textContent=`Interval unavailable: ${e.message}`;}
}
function renderSpectrum(d){
  const x=d.wavelengths,a=d.observation.spectrum,b=d.representative_mean,values=a.concat(b).filter(Number.isFinite);
  if(!values.length || x.length<2)return;
  const lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1;
  const px=i=>52+420*(x[i]-x[0])/(x.at(-1)-x[0]),py=v=>150-120*(v-lo)/span;
  const points=series=>series.map((v,i)=>`${px(i)},${py(v)}`).join(' ');
  $('spectrum').innerHTML=`<h3>Adjusted spectrum · separate pilot</h3><svg class="spectrum-svg" viewBox="0 0 500 200" role="img" aria-label="Adjusted interval spectrum and pilot cluster mean against wavelength"><path class="axis" d="M52 25V150H472"/><polyline class="observation" points="${points(a)}"/><polyline class="representative" points="${points(b)}"/><text x="48" y="30" text-anchor="end">${hi.toPrecision(2)}</text><text x="48" y="150" text-anchor="end">${lo.toPrecision(2)}</text><text x="52" y="170">${x[0]}</text><text x="472" y="170" text-anchor="end">${x.at(-1)}</text><text x="260" y="195" text-anchor="middle">Wavelength (nm)</text></svg><p class="caption">Solid: interval median. Dashed: development-cluster mean. ${d.region==='swir'?'NIR package (VNIR + SWIR)':'TIR package'} · ${esc(label(d.observation.status.replaceAll('_',' ')))}. This separate raw-package pilot shares the depth bin; API-to-raw row alignment is not established.</p>`;
}

$('filters').addEventListener('submit',e=>e.preventDefault());
['sensor','cohort'].forEach(id=>$(id).addEventListener('change',load));
['study-region','sort'].forEach(id=>$(id).addEventListener('change',render));
$('search').addEventListener('input',render);
$('reset').onclick=()=>{$('search').value='';$('study-region').value='';$('sort').value='id';if(state.data)render();else load();};
['overview','compare','map','method'].forEach(name=>$(`tab-${name}`).onclick=()=>showTab(name));
document.querySelector('.tabs').addEventListener('keydown',e=>{
  const buttons=[...document.querySelectorAll('[role=tab]')],index=buttons.indexOf(document.activeElement);
  if(index<0 || !['ArrowRight','ArrowLeft','Home','End'].includes(e.key))return;
  e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?3:(index+(e.key==='ArrowRight'?1:3))%4;
  buttons[next].focus();buttons[next].click();
});
document.addEventListener('click',e=>{
  const hole=e.target.closest('[data-hole]');if(hole)openHole(hole.dataset.hole);
  const interval=e.target.closest('[data-interval]');if(interval)openInterval(interval.dataset.interval);
  const related=e.target.closest('[data-related]');if(related){
    if(!state.rows.some(h=>h.hole_id===related.dataset.related)){error('That similar interval belongs to a hole outside the current dataset/filters. Choose All prepared holes and clear filters to inspect it.');return;}
    openHole(related.dataset.related).then(()=>{if(state.hole!==related.dataset.related)return;$('depth-from').value=related.dataset.relatedDepth;$('depth-to').value=Number(related.dataset.relatedDepth)+1;state.offset=0;loadProfile();});
  }
});
document.addEventListener('change',e=>{
  const input=e.target.closest('[data-compare]');if(!input)return;
  const id=input.dataset.compare;
  if(input.checked && state.compare.size>=4){input.checked=false;$('feedback').textContent='Compare up to four holes. Deselect one to add another.';return;}
  if(input.checked)state.compare.add(id);else state.compare.delete(id);renderCompare();
});
$('clear-compare').onclick=()=>{state.compare.clear();renderTable();renderCompare();};
$('close-detail').onclick=closeDetail;
$('depth-filter').onsubmit=e=>{e.preventDefault();state.offset=0;loadProfile();};
$('previous-page').onclick=()=>{state.offset=Math.max(0,state.offset-120);loadProfile();};
$('next-page').onclick=()=>{if(state.next!=null){state.offset=state.next;loadProfile();}};
load();
