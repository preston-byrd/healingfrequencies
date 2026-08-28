// Per-frequency ideal default tone-volume map.
//
// Rationale: different frequency ranges have very different perceived
// loudness on typical playback hardware. Sub-bass brainwave bands
// (Delta / Theta / Schumann / Alpha) feel almost inaudible without
// headphones at 30%, while high Solfeggio (852/963/1111 Hz) can feel
// piercing at the same level. This module gives every frequency a
// tuned starting point so the app never surprises the user with a
// jarringly loud or unusably quiet session.
//
// The baseline map below is the code-shipped default. An admin can
// override any value via PUT /api/admin/frequency-defaults; overrides
// are fetched once per app load and merged over the baseline.
//
// Volume values are 0..1 (matches audioEngine.setToneVolume contract).
//
// Consumer contract:
//   getIdealVolume(hz)           -> number (0..1)
//   applyIdealVolume(engine, hz) -> smoothly ramps toneGain to ideal
//   loadAdminOverrides()         -> Promise<void>, called on Dashboard mount

import api from './api';

// Baseline table. Tuned by ear + perceived-loudness curve (Fletcher-Munson
// aware): the very low bands need a boost because most speakers roll off
// below ~50 Hz, and the high bands are dialled down because 800+ Hz sits
// squarely in the ear's most sensitive band.
const BASELINE = {
  // Sub-bass brainwave (feel > hear)
  2:    0.55,
  6:    0.52,
  7.83: 0.50,
  10:   0.48,
  40:   0.42,
  // Solfeggio low/mid
  111:  0.38,
  174:  0.36,
  222:  0.36,
  285:  0.34,
  369:  0.34,
  396:  0.33,
  417:  0.32,
  432:  0.32,
  444:  0.31,
  528:  0.30,
  // Solfeggio high
  639:  0.28,
  741:  0.26,
  852:  0.25,
  963:  0.24,
  1111: 0.22,
};

// In-memory admin overrides, populated by loadAdminOverrides().
let overrides = {};
let overridesLoadedAt = 0;

// Nearest-frequency fallback for arbitrary custom Hz values (e.g. the
// custom-frequency slider or an AI prescription that returns 517 Hz).
// Uses a band-based lookup so a custom 500 Hz maps to the 528 default.
function nearestBandKey(hz) {
  const merged = { ...BASELINE, ...overrides };
  const keys = Object.keys(merged).map(Number).sort((a, b) => a - b);
  if (!keys.length) return null;
  if (hz <= keys[0]) return keys[0];
  if (hz >= keys[keys.length - 1]) return keys[keys.length - 1];
  // Find nearest neighbour
  let best = keys[0];
  let bestD = Math.abs(hz - keys[0]);
  for (const k of keys) {
    const d = Math.abs(hz - k);
    if (d < bestD) { best = k; bestD = d; }
  }
  return best;
}

export function getIdealVolume(hz) {
  const merged = { ...BASELINE, ...overrides };
  // Exact match wins (handles both baseline and admin overrides)
  if (typeof merged[hz] === 'number') return merged[hz];
  // Also try rounded (custom slider granularity is 0.1 Hz)
  const rounded = Math.round(hz * 100) / 100;
  if (typeof merged[rounded] === 'number') return merged[rounded];
  const nearest = nearestBandKey(hz);
  if (nearest == null) return 0.35; // absolute safety fallback
  return merged[nearest];
}

// True when the current toneVolume is materially different from the
// recommended value for the given hz. Used to gate the "Reset to
// recommended" chip so it only appears when meaningful.
export function volumeDivergesFromIdeal(currentVol, hz, epsilon = 0.02) {
  const ideal = getIdealVolume(hz);
  return Math.abs((Number(currentVol) || 0) - ideal) > epsilon;
}

// Apply the ideal volume to the audio engine. The engine already ramps
// via setTargetAtTime(0.05s) so this is click/pop-free. Returns the
// applied value so callers can log / display it.
export function applyIdealVolume(engine, hz) {
  if (!engine || typeof engine.setToneVolume !== 'function') return null;
  const v = getIdealVolume(hz);
  try { engine.setToneVolume(v); } catch (_) { /* graceful */ }
  return v;
}

// Fetch admin-configured overrides once per session. Silent no-op on
// network failure — the baseline map is a safe fallback.
export async function loadAdminOverrides() {
  // Cache for 5 minutes to avoid hammering the endpoint on remounts.
  if (Date.now() - overridesLoadedAt < 5 * 60 * 1000) return;
  try {
    const { data } = await api.get('/frequency-defaults');
    if (data && data.overrides && typeof data.overrides === 'object') {
      // Coerce keys to numbers and clamp values 0..1 for safety.
      const cleaned = {};
      Object.entries(data.overrides).forEach(([k, v]) => {
        const hz = Number(k);
        const vol = Math.max(0, Math.min(1, Number(v)));
        if (Number.isFinite(hz) && Number.isFinite(vol)) cleaned[hz] = vol;
      });
      overrides = cleaned;
      overridesLoadedAt = Date.now();
    }
  } catch (_) { /* graceful — baseline still works */ }
}

// Expose the current merged map (baseline ⊕ overrides). Used by the
// admin editor to render the current effective table.
export function getMergedDefaults() {
  return { ...BASELINE, ...overrides };
}

export function getBaselineDefaults() {
  return { ...BASELINE };
}

// Force re-fetch (called by admin panel after a successful save so the
// UI reflects new overrides without waiting for the 5 min cache).
export function invalidateOverridesCache() {
  overridesLoadedAt = 0;
}
