// Harmonic Blueprint — client-side voice-spectrum engine.
//
// Extracts an averaged frequency spectrum from an AudioBuffer using
// a Cooley-Tukey radix-2 FFT + Hann window + 50 % overlap, then
// distills the profile the UI + backend care about:
//   - dominant frequencies (top energy peaks)
//   - notable dips (local minima below neighbour energy)
//   - band summary (bass / low-mid / mid / upper-mid / presence / air)
//   - underrepresented bands (bottom-quartile relative to full-signal median)
//
// The audio itself is NEVER uploaded — this module returns a JSON-safe
// profile object; the UI POSTs only that to /api/harmonic-blueprint/profile.

const FFT_SIZE = 4096; // 4096 @ 22050Hz => ~5.4Hz resolution
const HOP = FFT_SIZE / 2; // 50 % overlap

// --- Radix-2 iterative FFT (in-place bit-reversal + butterflies) -----------
function fftInPlace(real, imag) {
  const n = real.length;
  // Bit-reversal permutation
  let j = 0;
  for (let i = 1; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let tr = real[i]; real[i] = real[j]; real[j] = tr;
      let ti = imag[i]; imag[i] = imag[j]; imag[j] = ti;
    }
  }
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1;
    const theta = (-2 * Math.PI) / size;
    const wRealStep = Math.cos(theta);
    const wImagStep = Math.sin(theta);
    for (let i = 0; i < n; i += size) {
      let wReal = 1, wImag = 0;
      for (let k = 0; k < half; k++) {
        const ir = i + k, jr = i + k + half;
        const tr = wReal * real[jr] - wImag * imag[jr];
        const ti = wReal * imag[jr] + wImag * real[jr];
        real[jr] = real[ir] - tr;
        imag[jr] = imag[ir] - ti;
        real[ir] += tr;
        imag[ir] += ti;
        const nwr = wReal * wRealStep - wImag * wImagStep;
        wImag = wReal * wImagStep + wImag * wRealStep;
        wReal = nwr;
      }
    }
  }
}

// --- Averaged spectrum via Hann-windowed overlapping FFTs -----------------
function averagedSpectrum(samples, fftSize = FFT_SIZE, hop = HOP) {
  const half = fftSize / 2;
  const mag = new Float32Array(half);
  let frames = 0;
  const hann = new Float32Array(fftSize);
  for (let i = 0; i < fftSize; i++) {
    hann[i] = 0.5 * (1 - Math.cos((2 * Math.PI * i) / (fftSize - 1)));
  }
  const real = new Float32Array(fftSize);
  const imag = new Float32Array(fftSize);
  for (let start = 0; start + fftSize <= samples.length; start += hop) {
    for (let i = 0; i < fftSize; i++) {
      real[i] = samples[start + i] * hann[i];
      imag[i] = 0;
    }
    fftInPlace(real, imag);
    for (let k = 0; k < half; k++) {
      mag[k] += Math.sqrt(real[k] * real[k] + imag[k] * imag[k]);
    }
    frames++;
  }
  if (frames > 0) for (let k = 0; k < half; k++) mag[k] /= frames;
  return mag;
}

// --- Signal-quality checks ------------------------------------------------
export function validateBuffer(audioBuffer, { minSeconds = 10, maxSeconds = 30 } = {}) {
  const duration = audioBuffer.duration;
  const ch = audioBuffer.getChannelData(0);
  let peak = 0, sumSq = 0, clipped = 0;
  for (let i = 0; i < ch.length; i++) {
    const v = Math.abs(ch[i]);
    if (v > peak) peak = v;
    if (v >= 0.985) clipped++;
    sumSq += ch[i] * ch[i];
  }
  const rms = Math.sqrt(sumSq / ch.length);
  const peakDb = 20 * Math.log10(peak || 1e-9);
  const rmsDb = 20 * Math.log10(rms || 1e-9);
  const clippedPct = (clipped / ch.length) * 100;

  if (duration < minSeconds) {
    return { ok: false, code: 'too_short',
      message: `Sample is ${duration.toFixed(1)}s — please record at least ${minSeconds} seconds.` };
  }
  if (rmsDb < -45) {
    return { ok: false, code: 'too_quiet',
      message: 'Not enough signal detected. Move closer to the microphone and try again.' };
  }
  if (clippedPct > 0.5) {
    return { ok: false, code: 'clipped',
      message: 'Your recording is clipped — please move further from the mic or lower the input volume.' };
  }
  // Crude SNR proxy: crest factor (peak vs RMS). A steady vocal tone yields
  // ~10-15 dB; harsh noise / room clatter is much lower.
  const crest = peakDb - rmsDb;
  if (crest < 4) {
    return { ok: false, code: 'noisy',
      message: 'Signal looks noisy — try recording in a quieter space.' };
  }
  return { ok: true, duration, peakDb, rmsDb, clippedPct };
}

// --- Full analysis pipeline ------------------------------------------------
export function analyseBuffer(audioBuffer, { maxSeconds = 30 } = {}) {
  // Downmix to mono, cap at maxSeconds so a chatty upload doesn't explode.
  const sr = audioBuffer.sampleRate;
  const total = Math.min(audioBuffer.length, Math.floor(maxSeconds * sr));
  const mono = new Float32Array(total);
  const chCount = audioBuffer.numberOfChannels;
  for (let c = 0; c < chCount; c++) {
    const data = audioBuffer.getChannelData(c);
    for (let i = 0; i < total; i++) mono[i] += data[i] / chCount;
  }
  const mag = averagedSpectrum(mono, FFT_SIZE, HOP);
  const binHz = sr / FFT_SIZE;
  // Only care about 60-4000 Hz for vocal analysis — higher bins dominated by
  // sibilance/mic self-noise for casual recordings.
  const lo = Math.max(1, Math.floor(60 / binHz));
  const hi = Math.min(mag.length - 1, Math.ceil(4000 / binHz));

  // Convert to dB relative to peak so the UI can plot a normalised curve.
  let peak = 0;
  for (let i = lo; i <= hi; i++) if (mag[i] > peak) peak = mag[i];
  const spectrum = [];
  for (let i = lo; i <= hi; i++) {
    const hz = i * binHz;
    const linear = mag[i];
    const db = 20 * Math.log10(Math.max(linear, 1e-9) / Math.max(peak, 1e-9));
    spectrum.push({ hz: +hz.toFixed(1), db: +db.toFixed(2) });
  }

  // Peak detection: any bin louder than both neighbours AND > median+6dB,
  // with a minimum spacing of ~30 Hz between accepted peaks.
  const dbs = spectrum.map((s) => s.db);
  const sorted = [...dbs].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const rawPeaks = [];
  for (let i = 2; i < spectrum.length - 2; i++) {
    const d = spectrum[i].db;
    if (
      d > spectrum[i - 1].db && d > spectrum[i + 1].db &&
      d > spectrum[i - 2].db && d > spectrum[i + 2].db &&
      d > median + 6
    ) {
      rawPeaks.push({ ...spectrum[i], idx: i });
    }
  }
  rawPeaks.sort((a, b) => b.db - a.db);
  const dominant = [];
  for (const p of rawPeaks) {
    if (dominant.every((q) => Math.abs(q.hz - p.hz) > 30)) dominant.push(p);
    if (dominant.length >= 5) break;
  }

  // Dip detection: local minima where neighbours are ≥ 8 dB louder — genuine
  // notches, not just floor noise. Cap at 4.
  const rawDips = [];
  for (let i = 3; i < spectrum.length - 3; i++) {
    const d = spectrum[i].db;
    if (
      d < spectrum[i - 1].db && d < spectrum[i + 1].db &&
      d < spectrum[i - 3].db - 8 && d < spectrum[i + 3].db - 8 &&
      d < median - 4
    ) {
      rawDips.push({ ...spectrum[i], idx: i });
    }
  }
  rawDips.sort((a, b) => a.db - b.db);
  const dips = [];
  for (const p of rawDips) {
    if (dips.every((q) => Math.abs(q.hz - p.hz) > 60)) dips.push(p);
    if (dips.length >= 4) break;
  }

  // Vocal-relevant band summary (band → average dB, 0-normalised to peak bin).
  const BANDS = [
    { key: 'sub',       label: 'Sub / Root',    lo: 60,   hi: 160  },
    { key: 'low',       label: 'Low',           lo: 160,  hi: 320  },
    { key: 'lowmid',    label: 'Low-Mid',       lo: 320,  hi: 600  },
    { key: 'mid',       label: 'Mid',           lo: 600,  hi: 1200 },
    { key: 'uppermid',  label: 'Upper-Mid',     lo: 1200, hi: 2400 },
    { key: 'presence',  label: 'Presence',      lo: 2400, hi: 4000 },
  ];
  const bands = BANDS.map((b) => {
    let sum = 0, n = 0;
    for (const s of spectrum) {
      if (s.hz >= b.lo && s.hz < b.hi) { sum += s.db; n++; }
    }
    return { ...b, db: n ? +(sum / n).toFixed(2) : -60, bins: n };
  });
  const bandDbs = bands.map((b) => b.db);
  const bandMedian = [...bandDbs].sort((a, b) => a - b)[Math.floor(bandDbs.length / 2)];
  const underrepresented = bands
    .filter((b) => b.db < bandMedian - 4)
    .map((b) => ({ key: b.key, label: b.label, lo: b.lo, hi: b.hi, db: b.db }));

  return {
    version: 1,
    sample_rate: sr,
    duration: +audioBuffer.duration.toFixed(2),
    fft_size: FFT_SIZE,
    // spectrum can be big — decimate to ≤ 256 points for UI + storage.
    spectrum: decimate(spectrum, 256),
    dominant: dominant.map(({ hz, db }) => ({ hz, db })),
    dips: dips.map(({ hz, db }) => ({ hz, db })),
    bands,
    underrepresented,
    generated_at: new Date().toISOString(),
  };
}

function decimate(points, target) {
  if (points.length <= target) return points;
  const step = points.length / target;
  const out = [];
  for (let i = 0; i < target; i++) {
    const s = Math.floor(i * step);
    const e = Math.floor((i + 1) * step);
    let sum = 0, n = 0, hz = 0;
    for (let j = s; j < e && j < points.length; j++) {
      sum += points[j].db; hz += points[j].hz; n++;
    }
    if (n > 0) out.push({ hz: +(hz / n).toFixed(1), db: +(sum / n).toFixed(2) });
  }
  return out;
}

// --- Phase 2: Eigenmode drift comparison ---------------------------------
// Given two profiles (current + eigenmode baseline), return supportive,
// non-diagnostic "resonance gap" findings ranked by drift magnitude. All
// copy here is intentionally non-medical — we describe drift toward or away
// from the user's natural tuning, never as pathology.
const BAND_MEANINGS = {
  sub: {
    label: 'Grounding & root',
    quieter:
      'Your lower grounding frequencies appear to have drifted quieter than your natural baseline — the deep resonance that anchors you.',
    louder:
      'Your lower grounding frequencies are currently more prominent than your natural baseline.',
  },
  low: {
    label: 'Warmth & depth',
    quieter:
      'The warm, embodied depth in your voice has softened from its natural tuning.',
    louder:
      'The warm, embodied depth in your voice is amplified beyond your natural baseline.',
  },
  lowmid: {
    label: 'Chest resonance',
    quieter:
      'Your chest-centred resonance has moved quieter than your natural baseline — an area inviting rebalancing.',
    louder:
      'Your chest-centred resonance is currently louder than your natural baseline.',
  },
  mid: {
    label: 'Expressive core',
    quieter:
      'Your expressive core has quieted from your natural tuning.',
    louder:
      'Your expressive core is more prominent than your natural baseline.',
  },
  uppermid: {
    label: 'Articulation & clarity',
    quieter:
      'Your articulation range has softened from its natural resonance — an area inviting rebalancing.',
    louder:
      'Your articulation range has moved louder than your natural baseline.',
  },
  presence: {
    label: 'Brightness & openness',
    quieter:
      'The brightness in your voice has drifted quieter than your natural tuning.',
    louder:
      'The brightness in your voice is currently more amplified than your natural baseline.',
  },
};

/**
 * Compare a fresh analysis to the user's eigenmode baseline and produce
 * ranked, non-diagnostic findings. Returns [] when either profile is
 * missing bands, or when nothing has drifted significantly (|Δ| ≥ 4 dB).
 *
 * @param {object} current  profile produced by analyseBuffer (or fetched)
 * @param {object} eigen    the user's saved eigenmode baseline
 * @param {number} minDeltaDb  significance threshold (default 4 dB)
 * @returns {Array<Finding>} ranked most-significant first, capped to 5
 */
export function compareToEigenmode(current, eigen, minDeltaDb = 4) {
  if (!current || !eigen) return [];
  const cur = new Map((current.bands || []).map((b) => [b.key, b]));
  const eig = new Map((eigen.bands || []).map((b) => [b.key, b]));
  const findings = [];
  for (const [key, meta] of Object.entries(BAND_MEANINGS)) {
    const c = cur.get(key);
    const e = eig.get(key);
    if (!c || !e) continue;
    const delta = c.db - e.db;                       // + means current louder
    const magnitude = Math.abs(delta);
    if (magnitude < minDeltaDb) continue;
    const direction = delta < 0 ? 'quieter' : 'louder';
    findings.push({
      key,
      label: meta.label,
      description: meta[direction],
      direction,
      delta_db: +delta.toFixed(2),
      magnitude: +magnitude.toFixed(2),
      lo: e.lo,
      hi: e.hi,
      current_db: c.db,
      eigen_db: e.db,
    });
  }
  // Rank by magnitude (most drifted first), then present at most 5.
  findings.sort((a, b) => b.magnitude - a.magnitude);
  return findings.slice(0, 5);
}

// --- Decode helpers --------------------------------------------------------
export async function decodeBlobToBuffer(blob, ctx) {
  const arrayBuf = await blob.arrayBuffer();
  return await new Promise((resolve, reject) => {
    ctx.decodeAudioData(arrayBuf.slice(0), resolve, reject);
  });
}

export async function decodeFileToBuffer(file, ctx) {
  const arrayBuf = await file.arrayBuffer();
  return await new Promise((resolve, reject) => {
    ctx.decodeAudioData(arrayBuf.slice(0), resolve, reject);
  });
}
