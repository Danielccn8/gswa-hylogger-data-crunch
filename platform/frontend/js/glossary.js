// Plain-language explanations shared by every view, so a term always means the same thing.
import { esc } from './util.js';

export const PROCESSED = {
  prepared: ['Mineral data ready', 'The processed HyLogger logs (the TSG mineral interpretation) were downloaded from the NVCL service and summarised into 1 m intervals. This hole appears in clusters, similarity and anomalies.'],
  no_logs: ['Scanned, no public mineral logs', 'The hole was scanned by a HyLogger, but the NVCL service publishes no processed mineral logs for it. Raw spectra may still exist.'],
  no_usable_logs: ['Logs not usable', 'Processed logs exist but none passed the quality checks (no diagnostic readings).'],
  not_fetched: ['Not downloaded yet', 'The processed logs have not been downloaded. Use the Data tab to download and process this hole.'],
  failed: ['Download failed', 'The last download attempt failed; it can be retried from the Data tab.'],
};

export const RAW = {
  summarised: ['Raw spectra summarised (files kept)', 'The raw spectra were downloaded and reduced to one median spectrum per metre, with absorption features (for example the 2200 nm Al-OH position). The original spectral files are still on disk.'],
  summarised_discarded: ['Raw spectra summarised (originals removed)', 'The raw spectra were downloaded and reduced to one median spectrum per metre, with absorption features. The original spectral files (hundreds of MB per hole) were then deleted to save disk space; the summary is what the app uses. They can be downloaded again at any time from the Data tab.'],
  not_fetched: ['Raw spectra not downloaded', 'A raw spectral package exists on the NCI server but has not been downloaded.'],
  downloading: ['Downloading raw spectra', 'The raw spectra are being downloaded and summarised now.'],
  fetched: ['Raw spectra downloaded, not summarised', 'The spectral files are on disk but the 1 m summary has not been built yet.'],
  failed: ['Raw download failed', 'The last attempt to download the raw spectra failed (often the server closed the connection); it will be retried.'],
  summary_failed: ['Raw summary failed', 'The spectra were downloaded but could not be summarised (for example no readings passed the Final Mask).'],
};

export function statusText(table, value, available = true) {
  if (table === RAW && value === 'not_fetched' && !available) return ['No raw package published', 'No raw spectral package for this hole is listed on the NCI server.'];
  return table[value] || [value || 'Unknown', ''];
}

export const TERMS = {
  interval: 'One metre of core. The HyLogger takes a reading roughly every centimetre; all diagnostic readings inside each metre are summarised into its mineral mix.',
  swir: 'Short-wave infrared (1000–2500 nm): sees hydrous minerals such as micas, chlorite, clays, carbonates and amphiboles.',
  tir: 'Thermal infrared (6000–14500 nm): sees anhydrous minerals such as quartz, feldspars, garnet, pyroxene and carbonates.',
  vnir: 'Visible and near infrared (380–1000 nm): sees iron oxides and other iron-bearing minerals.',
  cluster: 'A group of metres (interval cluster) or holes (hole cluster) with a similar mineral mix, found automatically. Names come from the minerals that dominate each group.',
  holeCluster: 'Holes grouped by their whole-hole fingerprint: the share of their metres in each interval cluster (mineral mix), the order of zones down the hole (sequence) or their raw spectra.',
  analysis: 'Which whole-hole comparison the hole clusters and unusualness come from: mineral mix (what the hole contains), down-hole sequence (also in what order) or raw spectra (independent of the TSG interpretation).',
  unusualness: 'Percentile of how far a hole is from its five most similar holes. 0.99 = more isolated than 99% of holes. A review candidate, not a finding.',
  commodity: 'The commodity the operator declared for the project in WAMEX or the NVCL catalogue. It is what they were exploring for, not something measured in the hole.',
  assays: 'Laboratory chemistry for the hole from the GSWA package (company assays; unverified interim data).',
  similarity: 'Similarity = 1 − distance between whole-hole fingerprints. 100% = identical fingerprints.',
};

// Small (i) marker; the global tooltip in main.js shows the text on hover or keyboard focus.
export function info(text) {
  return `<span class="info" tabindex="0" role="img" aria-label="${esc(text)}" data-tip="${esc(text)}">i</span>`;
}

// Absorption features marked on NIR/SWIR spectrum charts.
export const NIR_MARKS = [{ x: 1410, label: 'OH' }, { x: 1910, label: 'H₂O' }, { x: 2200, label: 'Al-OH' }, { x: 2335, label: 'Mg-OH' }];
