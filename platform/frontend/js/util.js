export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const num = (value, digits = 0) => value == null || Number.isNaN(Number(value)) ? '–' : Number(value).toLocaleString('en-AU', { maximumFractionDigits: digits, minimumFractionDigits: digits });
export const pct = (value, digits = 0) => value == null ? '–' : `${(100 * value).toFixed(digits)}%`;
export const bytes = value => {
  if (value == null) return '–';
  const units = ['B', 'KB', 'MB', 'GB', 'TB']; let v = value, i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i += 1; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
};
export const ranked = (obj, n = Infinity) => Object.entries(obj || {}).filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]).slice(0, n);
export const title = text => String(text ?? '').toLowerCase().replace(/(^|[\s-])\w/g, m => m.toUpperCase());

export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const detail = (await response.json()).detail;
      message = Array.isArray(detail) ? detail.map(d => d.msg || JSON.stringify(d)).join('; ') : (detail || message);
    } catch { /* non-JSON */ }
    const error = new Error(message); error.status = response.status; throw error;
  }
  return response.json();
}

let toastTimer;
export function toast(message) {
  const node = $('#toast');
  node.textContent = message; node.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true; }, 4200);
}

export function tooltip(html, event) {
  const node = $('#tooltip');
  if (!html) { node.hidden = true; return; }
  node.innerHTML = html; node.hidden = false;
  const x = Math.min(event.clientX + 14, innerWidth - node.offsetWidth - 8);
  const y = Math.min(event.clientY + 14, innerHeight - node.offsetHeight - 8);
  node.style.left = `${x}px`; node.style.top = `${y}px`;
}

export function bars(entries, colorFor, { max = null, spread = null } = {}) {
  if (!entries.length) return '<p class="empty">No values.</p>';
  const top = max ?? Math.max(...entries.map(([, v]) => v), 1e-9);
  return `<div class="bars">${entries.map(([name, value]) => `<div class="bar"><span title="${esc(name)}">${esc(name)}</span>
    <div class="track"><div class="fill" style="width:${Math.max(0, Math.min(100, 100 * value / top))}%;background:${colorFor(name)}"></div></div>
    <span class="value">${pct(value)}${spread && spread[name] != null ? `<small class="muted"> ±${Math.round(100 * spread[name])}</small>` : ''}</span></div>`).join('')}</div>`;
}

export function stack(composition, colorFor) {
  const entries = Object.entries(composition || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, v]) => s + v, 0) || 1;
  return `<div class="stack">${entries.map(([k, v]) => `<span title="${esc(k)} ${pct(v / total)}" style="width:${100 * v / total}%;background:${colorFor(k)}"></span>`).join('')}</div>`;
}

export function loading(node, text = 'Loading…') { node.innerHTML = `<p class="empty">${esc(text)}</p>`; }
export function failure(node, error) { node.innerHTML = `<div class="notice danger">${esc(error.message || error)}</div>`; }
