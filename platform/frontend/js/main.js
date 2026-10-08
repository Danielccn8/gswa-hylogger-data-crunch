import { $, $$, api, esc, toast, tooltip } from './util.js';
import { selection } from './state.js';
import { setMineralGroups } from './palette.js';

const views = {
  map: () => import('./views/map.js'),
  hole: () => import('./views/hole.js'),
  similarity: () => import('./views/similarity.js'),
  clusters: () => import('./views/clusters.js'),
  anomalies: () => import('./views/anomalies.js'),
  prediction: () => import('./views/prediction.js'),
  data: () => import('./views/data.js'),
  method: () => import('./views/method.js'),
};
let current = null, currentKey = null;

export function go(path) { location.hash = `#/${path}`; }

async function route() {
  const [name = 'map', ...rest] = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  const key = views[name] ? name : 'map';
  $$('.tabs a').forEach(a => a.classList.toggle('active', a.dataset.tab === key));
  if (current?.leave) current.leave();
  tooltip(null);
  const root = $('#view');
  if (key !== currentKey) window.scrollTo(0, 0);
  currentKey = key;
  root.innerHTML = '<p class="empty">Loading…</p>';
  try {
    current = await views[key]();
    await current.render(root, rest);
  } catch (error) {
    root.innerHTML = `<div class="notice danger">${esc(error.message)}</div>`;
  }
}

function renderBasket(list) {
  $('#basket-count').textContent = list.length;
  $('#basket-list').innerHTML = list.length
    ? list.map(id => `<span class="chip">${esc(id)}<button type="button" data-remove="${esc(id)}" aria-label="Remove ${esc(id)}">×</button></span>`).join('')
    : '<p class="empty">No holes selected.</p>';
}

$('#basket').addEventListener('click', () => { renderBasket(selection.list()); $('#basket-dialog').showModal(); });
$('#basket-list').addEventListener('click', event => {
  const id = event.target.closest('[data-remove]')?.dataset.remove; if (id) selection.remove(id);
});
$('#basket-clear').addEventListener('click', () => selection.clear());
$('#basket-compare').addEventListener('click', () => { $('#basket-dialog').close(); go('similarity'); });
$('#basket-data').addEventListener('click', () => { $('#basket-dialog').close(); go('data'); });
selection.on(list => renderBasket(list));
renderBasket(selection.list());

window.addEventListener('hashchange', route);
api('/api/v2/mineral-groups').then(setMineralGroups).catch(() => {}).finally(route);
api('/api/v2/health').catch(error => toast(`Database unavailable: ${error.message}`));
