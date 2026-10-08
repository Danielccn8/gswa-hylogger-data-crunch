// One fixed colour per mineral group, used by every view, so a colour always means the same thing.
const GROUPS = {
  'WHITE-MICA': '#6fa8dc', 'CHLORITE': '#4f9a5b', 'KAOLIN': '#e8b4c8', 'CARBONATE': '#f2a33a',
  'AMPHIBOLE': '#2f6b3f', 'SERPENTINE': '#9bb04a', 'OTHER-MGOH': '#7ccfb9', 'EPIDOTE': '#c6d64f',
  'DARK-MICA': '#8a5a3b', 'OTHER-ALOH': '#a9c7ee', 'TOURMALINE': '#3d3d4a', 'SMECTITE': '#d7c3a3',
  'SULPHATE': '#f4dc6b', 'PYROPHYLLITE': '#c39bd3', 'SILICA': '#bfc7cc', 'PLAGIOCLASE': '#9d8fd6',
  'K-FELDSPAR': '#e98a80', 'FELDSPAR': '#d39ad1', 'PYROXENE': '#5b6b2e', 'OLIVINE': '#89a02c', 'GARNET': '#c0392b',
  'OXIDE': '#b5502a', 'SULPHIDE': '#d4af37', 'HYDROXIDE': '#c77d4a', 'PHOSPHATE': '#57a0a0', 'OPAL': '#e3e7ea',
  'NOTAROK': '#888888', 'UNKNOWN': '#9aa5ab',
};
const CATEGORICAL = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
  '#393b79', '#e7969c', '#637939', '#ad494a', '#8c6d31', '#7b4173', '#3182bd', '#e6550d', '#31a354', '#756bb1', '#636363', '#9ecae1', '#fdae6b', '#a1d99b'];

let mineralGroup = {};
export function setMineralGroups(map) { mineralGroup = Object.assign({}, ...Object.values(map || {})); }

function hashColor(text) {
  let h = 0; for (const c of String(text)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return CATEGORICAL[h % CATEGORICAL.length];
}
export function groupColor(name) {
  const key = String(name || 'UNKNOWN').toUpperCase().replace(' (RARE)', '');
  return GROUPS[key] || hashColor(key);
}
export function mineralColor(name) {
  const group = mineralGroup[name];
  if (!group) return name && name.endsWith('(rare)') ? groupColor(name.replace(' (rare)', '')) : hashColor(name);
  // Shade minerals of one group around the group colour.
  const base = groupColor(group); let h = 0; for (const c of String(name)) h = (h * 17 + c.charCodeAt(0)) % 7;
  return shade(base, (h - 3) * 0.09);
}
export function clusterColor(id) { return id == null ? '#c9d1d5' : CATEGORICAL[Number(id) % CATEGORICAL.length]; }
export function categoryColor(value) { return value == null || value === '' ? '#c9d1d5' : hashColor(value); }
export function scoreColor(value) {
  if (value == null) return '#c9d1d5';
  const t = Math.max(0, Math.min(1, (value - 0.5) / 0.5));
  return `rgb(${Math.round(240 - 70 * t)},${Math.round(220 - 170 * t)},${Math.round(150 - 110 * t)})`;
}
function shade(hex, amount) {
  const n = parseInt(hex.slice(1), 16); let r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  const f = amount < 0 ? 0 : 255, t = Math.abs(amount);
  r = Math.round((f - r) * t + r); g = Math.round((f - g) * t + g); b = Math.round((f - b) * t + b);
  return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
}
