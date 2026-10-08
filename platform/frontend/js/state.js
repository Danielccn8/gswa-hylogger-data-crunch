// Shared selection basket, remembered per browser (a convenience only).
const KEY = 'hylogger-explorer-basket';
const listeners = new Set();
let basket = [];
try { basket = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { basket = []; }

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(basket)); } catch { /* storage unavailable */ }
  listeners.forEach(fn => fn(basket));
}
export const selection = {
  list: () => [...basket],
  has: id => basket.includes(id),
  add(ids) { ids.forEach(id => { if (!basket.includes(id)) basket.push(id); }); save(); },
  remove(id) { basket = basket.filter(x => x !== id); save(); },
  toggle(id) { this.has(id) ? this.remove(id) : this.add([id]); },
  clear() { basket = []; save(); },
  set(ids) { basket = [...new Set(ids)]; save(); },
  on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
};
export const cache = new Map();
