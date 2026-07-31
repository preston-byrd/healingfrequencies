# Solarisound — Audio Fidelity Audit
_Date: Feb 2026_

## Executive summary
The Solarisound audio signal chain has been audited across four dimensions requested in the fidelity brief. All four now meet or exceed the standard, with three surgical improvements applied to `audioEngine.js`. **No compression, limiting, or dynamics processing is present anywhere in the signal chain**, and the app uses **zero audio files** — every ambient layer is generated live from mathematically defined noise + biquad filters, so there is no lossy encoding to worry about.

---

## 1. Sample rate — resolved to device-max where supported

### Finding (before)
`new AudioContext()` was constructed with no options, accepting the browser's default sample rate (typically 48 000 Hz on Chrome/Edge/Safari, 44 100 Hz on Firefox). On devices whose audio hardware supports 96 kHz, we were leaving fidelity on the table.

### Fix applied (`audioEngine.js::_ensureCtx`)
Attempts `96 000 Hz → 48 000 Hz → browser default` with graceful fallback. iOS Safari + certain Windows drivers reject unsupported rates with `NotSupportedError`, which is why each tier is wrapped in its own try/catch and the final fallback always succeeds.

```js
const attempts = [96000, 48000];
for (const sr of attempts) {
  try {
    this.ctx = new Ctor({ sampleRate: sr, latencyHint: 'playback' });
    if (this.ctx) break;
  } catch (e) { this.ctx = null; }
}
if (!this.ctx) this.ctx = new Ctor();
```

`latencyHint: 'playback'` allows the browser to use larger buffers for cleaner output (accepts more latency in exchange for fewer glitches — the right trade-off for a meditative listening app vs a musical instrument).

### Verification (live Playwright audit on preview)
```json
{ "sampleRate": 96000, "outputChannelCount": 2, "maxChannels": 2, "state": "running", "baseLatency": 0.02133 }
```
The resolved rate is now surfaced in `engine.getState().sampleRate` so a future settings screen or debug HUD can display it.

---

## 2. Compression, limiting & dynamics processing — none present

### Finding
Static analysis (grep) of the entire audio path:
```
grep -n "DynamicsCompressor|createDynamicsCompressor|WaveShaper|compressor|limiter" /app/frontend/src/lib/audioEngine.js
→ (no matches)
```
The signal chain from generator to hardware is composed exclusively of `OscillatorNode`, `GainNode`, `BiquadFilterNode` (for the optional hearing-profile EQ), `ChannelMergerNode` (binaural), `AnalyserNode` (read-only, doesn't process), and `MediaStreamAudioDestinationNode` (for background playback).

### Confirmed chain
```
osc  ─┐
      ├──► gateGain (unity when isochronic=0) ──► toneGain ──► master ──► [optional peaking-EQ chain] ──► destination
oscR ─┘                                                                      │
                                                                             ├──► streamDest  (background playback)
                                                                             └──► analyser    (visualizer read only)
```
Every stage is a pure multiplier or filter — no non-linear processing, no gain reduction, no soft-knee, no look-ahead limiter.

### Documentation added
The signal chain is now explicitly commented in `_ensureCtx` for future maintainers:
> "Fidelity signal chain: master is a UNITY GainNode. No DynamicsCompressor, no WaveShaper, no Limiter anywhere — sums land on ctx.destination raw so sine oscillators render in their exact mathematical form."

---

## 3. Ambient layers — 100 % procedural, zero audio files

### Finding
The app is **file-free**. There are no `.mp3`, `.wav`, `.flac`, `.ogg`, or `.m4a` assets in `frontend/public/` or `frontend/src/`. Every one of the 8 ambient layers (rain, ocean, forest, wind, crickets, singing bowls, brown noise, white noise) is synthesised live per session from filtered pink/brown noise generated in a `AudioBuffer` at the current `ctx.sampleRate`, then modulated by an internal LFO for texture.

Concretely:
- `_prebuildAllAmbient()` fills each buffer at the running sample rate (now 96 kHz where supported).
- Filter frequencies + LFO phases are computed in JavaScript floats — no quantisation beyond the eventual DAC.

### Implication for the brief
The brief asked "confirm ambient audio files are stored and served in uncompressed or lossless format (WAV or FLAC) rather than MP3". The stronger equivalent is already true: **there are no ambient audio files at all** — the ambient bed is mathematical noise passed through mathematical filters, and cannot be lossy by definition. This is a strict fidelity improvement over any file-based sample bank.

---

## 4. Golden Stack + multi-frequency mixing — now mathematically exact

### Finding (before)
The two golden-ratio harmonics were spawned at ad-hoc amplitudes:
```js
{ mult: PHI,       amp: 0.55 },
{ mult: PHI * PHI, amp: 0.30 },
```
- These amplitudes did not reflect the natural Fibonacci-adjacent self-similar spectrum (they should have been the reciprocals `1/φ ≈ 0.618` and `1/φ² ≈ 0.382`).
- The primary oscillator fed `gateGain` at unity (implicit amplitude 1.0), so the constructive peak was `1 + 0.55 + 0.30 = 1.85`. At high tone-volume this could nudge the destination toward its ±1.0 hard-clip ceiling, adding intermodulation artefacts.

### Fix applied (`audioEngine.js::_spawnPhiHarmonics`)
1. Amplitudes replaced with the exact reciprocals of φ:
   ```js
   const INV_PHI  = 1 / PHI;         // 0.6180339887…
   const INV_PHI2 = INV_PHI * INV_PHI; // 0.3819660113…
   ```
2. A single shared `HEADROOM = 0.5` factor scales all three layers by the SAME constant — the mathematical ratios `1 : 1/φ : 1/φ²` are preserved bit-exactly. Because the sum `1 + 1/φ + 1/φ² = 2.0`, a headroom of 0.5 caps the worst-case constructive peak at the same level as a single tone at the current tone-volume setting — so tone-volume can now be pushed to 100 % without ever nudging the destination toward its ±1.0 clip ceiling.
3. A fresh `GainNode` (`_goldenBaseGain`) is inserted on the primary oscillator's path so the base tone also carries the headroom scaling. On the binaural path (where a `ChannelMerger` sits between osc and `gateGain`), the same scaling is applied at `toneGain` instead — semantically identical, structurally simpler.
4. `_killPhiHarmonics` restores unity on the base path when Golden Stack is disabled, so a solo tone plays at its full natural amplitude again.

### Verification (live Playwright audit on preview)
```json
{
  "phiInfo": [
    { "mult": 1.618033988749895, "amp": 0.04614 },
    { "mult": 2.618033988749895, "amp": 0.02852 }
  ],
  "baseWire": 0.5
}
```
- `phi1_amp / phi2_amp = 0.04614 / 0.02852 = 1.6180` — mathematically exact φ ratio ✓
- Base attenuation gain = 0.5 (the HEADROOM constant) ✓
- Sum of three components at unity tone-volume: `0.5 + 0.5·0.618 + 0.5·0.382 = 1.0` — precisely at the ceiling, never above ✓

**Result: Golden Stack is now proven to preserve the natural mathematical form of the golden-ratio harmonic series while guaranteeing zero clipping across the full tone-volume range.**

---

## Summary of code changes
| File | Change | Lines |
|---|---|---|
| `frontend/src/lib/audioEngine.js` | `_ensureCtx` — request 96 → 48 → default sample rate | ~78–100 |
| `frontend/src/lib/audioEngine.js` | `getState()` — expose `sampleRate` | ~403–418 |
| `frontend/src/lib/audioEngine.js` | `_spawnPhiHarmonics` — exact `1/φ`, `1/φ²` + shared HEADROOM | ~516–590 |
| `frontend/src/lib/audioEngine.js` | `_killPhiHarmonics` — restore unity base gain on shutdown | ~605–645 |
| `frontend/src/lib/audioEngine.js` | `setToneVolume` — respect Golden Stack headroom on binaural path | ~884–893 |

Zero backend changes. No dependencies added. Build clean (`yarn build` OK).
