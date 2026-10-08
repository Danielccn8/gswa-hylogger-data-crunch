// Optional re-cut of the stored hole-cluster tree at a user-chosen number of clusters.
import { api } from './util.js';

const cache = new Map();
export const K_OPTIONS = ['auto', 4, 6, 8, 10, 12, 15, 20];
export function kSelect(id, value) {
  return `<label class="field">Hole clusters<select id="${id}">${K_OPTIONS.map(k => `<option value="${k}" ${String(k) === String(value) ? 'selected' : ''}>${k === 'auto' ? 'Automatic' : k}</option>`).join('')}</select></label>`;
}
export async function recut(view, k) {
  if (k === 'auto') return null;
  const key = `${view}:${k}`;
  if (!cache.has(key)) cache.set(key, api(`/api/v2/hole-clusters?view=${view}&k=${k}`));
  return cache.get(key);
}
