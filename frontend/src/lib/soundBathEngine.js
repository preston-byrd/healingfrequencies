/**
 * soundBathEngine — algorithmic sound-bath generator.
 *
 * A "sound bath" here is a procedurally-scheduled series of note events across
 * three procedural instruments (crystal bowl, chime, gong), all synthesised
 * via the same Web Audio graph as the main audio engine. Every start seeds a
 * fresh random arrangement so no two sessions are identical, while still
 * feeling coherent because we pull note choices from a scale + octave range
 * that suits the preset's mood.
 *
 * Design goals:
 *   • Extremely low CPU footprint — pure sine partials + basic envelopes,
 *     no impulse-response reverb (Web Audio's `ConvolverNode` is expensive
 *     on mobile and would need bundled IRs).
 *   • Fully compatible with the existing `audioEngine` — routes into
 *     `audioEngine.master` so Smart Fade + hearing-profile EQ + hearing
 *     stream-destination + haptic sync all apply for free.
 *   • Presets are DATA — new baths added by editing `BATH_PRESETS` only.
 */

// ---------- Scales (semitone offsets from root) --------------------------
const MAJOR_PENT = [0, 2, 4, 7, 9];
const MINOR_PENT = [0, 3, 5, 7, 10];
const RESONANT_TRIAD = [0, 3, 7];       // suspended feel, safe for gongs
const HEART_SIXTH = [0, 5, 7, 12];       // stacked fifths for grounding
// Uneven "just intonation" scale tied to the Solfeggio grid (root=396).
const SOLFEGGIO_STEPS = [0, 4, 7, 9, 12];

// Preset registry — pure data so new baths can be added without touching
// engine code. Voice weights are integer buckets (roll a die, pick a voice).
export const BATH_PRESETS = {
  crystal_bowl_bath: {
    label: 'Crystal Bowl Bath',
    description: 'Pure sustained bowls in a 432 Hz major pentatonic wash.',
    voice_weights: { bowl: 1 },
    scale: MAJOR_PENT,
    root_hz: 216,           // A3 (432 Hz ÷ 2)
    octave_range: [0, 1, 2],
    gain_range: [0.09, 0.18],
    interval_ms_range: [4000, 8000],
  },
  chime_meditation: {
    label: 'Chime Meditation',
    description: 'Bright bells drifting through a minor pentatonic.',
    voice_weights: { chime: 1 },
    scale: MINOR_PENT,
    root_hz: 432,
    octave_range: [0, 1],
    gain_range: [0.06, 0.13],
    interval_ms_range: [1600, 4000],
  },
  gong_bath: {
    label: 'Deep Gong Bath',
    description: 'Low resonant gongs with sparse bowl overtones.',
    voice_weights: { gong: 3, bowl: 1 },
    scale: RESONANT_TRIAD,
    root_hz: 128,
    octave_range: [-1, 0],
    gain_range: [0.08, 0.16],
    interval_ms_range: [5000, 11000],
  },
  full_ensemble: {
    label: 'Full Ensemble',
    description: 'Bowls, chimes and gongs woven together.',
    voice_weights: { bowl: 2, chime: 2, gong: 1 },
    scale: MAJOR_PENT,
    root_hz: 216,
    octave_range: [-1, 0, 1, 2],
    gain_range: [0.06, 0.13],
    interval_ms_range: [2500, 6000],
  },
  aurora_bath: {
    label: 'Aurora Bath',
    description: 'Shimmering high chimes over weightless bowl pads.',
    voice_weights: { chime: 3, bowl: 1 },
    scale: MAJOR_PENT,
    root_hz: 432,
    octave_range: [1, 2, 3],
    gain_range: [0.05, 0.11],
    interval_ms_range: [1200, 3500],
  },
  grounding_bath: {
    label: 'Grounding Bath',
    description: 'Slow deep gongs with earthy sustained bowls.',
    voice_weights: { gong: 2, bowl: 2 },
    scale: HEART_SIXTH,
    root_hz: 96,
    octave_range: [0, 1],
    gain_range: [0.08, 0.16],
    interval_ms_range: [6000, 12000],
  },
  solfeggio_wash: {
    label: 'Solfeggio Wash',
    description: 'Bowls tuned to the classic Solfeggio grid (396 → 852 Hz).',
    voice_weights: { bowl: 3, chime: 1 },
    scale: SOLFEGGIO_STEPS,
    root_hz: 396,
    octave_range: [-1, 0, 1],
    gain_range: [0.07, 0.14],
    interval_ms_range: [3500, 7500],
  },
};

// ---------- Helpers ------------------------------------------------------
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

function weightedPick(weights) {
  const entries = Object.entries(weights);
  const total = entries.reduce((s, [, w]) => s + w, 0);
  let r = Math.random() * total;
  for (const [k, w] of entries) {
    r -= w;
    if (r <= 0) return k;
  }
  return entries[0][0];
}

// ---------- Engine -------------------------------------------------------
class SoundBathEngine {
  constructor(audioEngine) {
    this.audio = audioEngine;
    this.active = false;
    this.currentPresetKey = null;
    this._timer = null;
    this._listeners = new Set();
    // For clean stop — we keep refs to the top-level gain of every currently
    // sounding note so we can ramp them down instead of hard-stopping mid-tail.
    this._activeNoteGains = new Set();
  }

  on(fn) { this._listeners.add(fn); return () => this._listeners.delete(fn); }
  _emit() { this._listeners.forEach((f) => { try { f(this.snapshot()); } catch (e) { console.warn('[soundBath] listener', e); } }); }
  snapshot() { return { active: this.active, preset: this.currentPresetKey }; }

  async start(presetKey) {
    if (!BATH_PRESETS[presetKey]) return;
    // Re-starting = new arrangement. Stop the current bath cleanly first so
    // scheduled notes finish naturally rather than piling on top of the new
    // preset.
    if (this.active) this.stop({ hardCancel: true });
    await this.audio._ensureCtx();
    this.active = true;
    this.currentPresetKey = presetKey;
    this._emit();
    // Kick off the schedule immediately with a very short initial delay so
    // the user hears a note within ~1s of tapping.
    this._timer = setTimeout(() => this._tick(), 400);
  }

  stop({ hardCancel = false } = {}) {
    this.active = false;
    this.currentPresetKey = null;
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
    // Fade every note that's still sounding — 1.5s tail so the bath doesn't
    // end with a click. hardCancel is used on restart to keep the tail
    // shorter (300ms) so the new preset takes over quickly.
    const ctx = this.audio.ctx;
    if (ctx) {
      const now = ctx.currentTime;
      const fade = hardCancel ? 0.3 : 1.5;
      this._activeNoteGains.forEach((g) => {
        try {
          g.gain.cancelScheduledValues(now);
          g.gain.setValueAtTime(g.gain.value, now);
          g.gain.linearRampToValueAtTime(0, now + fade);
        } catch (e) { /* graceful — node may have already stopped */ }
      });
    }
    this._activeNoteGains.clear();
    this._emit();
  }

  _tick() {
    if (!this.active) return;
    const preset = BATH_PRESETS[this.currentPresetKey];
    if (!preset) return;
    // Choose voice, pitch, timing.
    const voice = weightedPick(preset.voice_weights);
    const semitone = pick(preset.scale);
    const octaveShift = pick(preset.octave_range) * 12;
    const hz = preset.root_hz * Math.pow(2, (semitone + octaveShift) / 12);
    const gain = rand(preset.gain_range[0], preset.gain_range[1]);
    const pan = rand(-0.7, 0.7);
    try {
      if (voice === 'bowl') this._bowl(hz, gain, pan);
      else if (voice === 'chime') this._chime(hz, gain, pan);
      else if (voice === 'gong') this._gong(hz, gain, pan);
    } catch (e) { console.warn('[soundBath] note failed', e); }
    // Schedule next event.
    const delayMs = rand(preset.interval_ms_range[0], preset.interval_ms_range[1]);
    this._timer = setTimeout(() => this._tick(), delayMs);
  }

  // Utility — track a note's top-level gain so stop() can fade it and
  // auto-remove it from the tracking set when its natural tail ends.
  _trackNote(g, endInSeconds) {
    this._activeNoteGains.add(g);
    setTimeout(() => this._activeNoteGains.delete(g), endInSeconds * 1000 + 200);
  }

  _connectHead(pan) {
    const ctx = this.audio.ctx;
    const g = ctx.createGain();
    g.gain.value = 0;
    // StereoPannerNode is universally available in modern browsers.
    const panner = ctx.createStereoPanner();
    panner.pan.value = pan;
    g.connect(panner).connect(this.audio.master);
    return { g, panner };
  }

  // ---------- Voices --------------------------------------------------
  _bowl(hz, gain, pan) {
    const ctx = this.audio.ctx;
    const now = ctx.currentTime;
    const attackS = rand(3.0, 5.0);
    const sustainS = rand(4.0, 8.0);
    const releaseS = rand(6.0, 10.0);
    const totalS = attackS + sustainS + releaseS;
    const { g } = this._connectHead(pan);
    // Fundamental + octave + fifth partials for that pure crystal-bowl ring.
    const partials = [
      { f: hz,       gain: 1.00 },
      { f: hz * 2,   gain: 0.30 },
      { f: hz * 3,   gain: 0.14 },
      { f: hz * 4,   gain: 0.07 },
    ];
    partials.forEach((p) => {
      const osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.value = p.f;
      const pg = ctx.createGain();
      pg.gain.value = p.gain;
      osc.connect(pg).connect(g);
      osc.start(now);
      osc.stop(now + totalS + 0.3);
    });
    g.gain.setValueAtTime(0, now);
    g.gain.linearRampToValueAtTime(gain, now + attackS);
    g.gain.setValueAtTime(gain, now + attackS + sustainS);
    g.gain.exponentialRampToValueAtTime(0.0001, now + attackS + sustainS + releaseS);
    this._trackNote(g, totalS);
  }

  _chime(hz, gain, pan) {
    const ctx = this.audio.ctx;
    const now = ctx.currentTime;
    const decayS = rand(2.5, 5.0);
    const { g } = this._connectHead(pan);
    // Bell-like: fundamental + odd/inharmonic partials with independent
    // decays. Ratio 3× / 5.4× gives the ping without going metallic-harsh.
    const partials = [
      { f: hz,       gain: 1.00, decay: decayS },
      { f: hz * 3,   gain: 0.55, decay: decayS * 0.55 },
      { f: hz * 5.4, gain: 0.30, decay: decayS * 0.40 },
      { f: hz * 7.7, gain: 0.15, decay: decayS * 0.30 },
    ];
    partials.forEach((p) => {
      const osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.value = p.f;
      const pg = ctx.createGain();
      pg.gain.value = 0;
      pg.gain.setValueAtTime(0, now);
      pg.gain.linearRampToValueAtTime(p.gain, now + 0.008);
      pg.gain.exponentialRampToValueAtTime(0.0001, now + p.decay);
      osc.connect(pg).connect(g);
      osc.start(now);
      osc.stop(now + p.decay + 0.15);
    });
    g.gain.setValueAtTime(gain, now);
    this._trackNote(g, decayS);
  }

  _gong(hz, gain, pan) {
    const ctx = this.audio.ctx;
    const now = ctx.currentTime;
    const attackS = rand(1.8, 2.8);
    const totalS = rand(14.0, 22.0);
    const { g } = this._connectHead(pan);
    // Gongs live an octave below the note request so a "C" pick still
    // lands in bass territory. Inharmonic partials give the shimmering
    // "spread" gongs are famous for.
    const baseHz = hz * 0.5;
    const partials = [
      { f: baseHz,        type: 'sine',     gain: 1.00 },
      { f: baseHz * 1.5,  type: 'sine',     gain: 0.52 },
      { f: baseHz * 2.4,  type: 'sine',     gain: 0.30 },
      { f: baseHz * 3.7,  type: 'triangle', gain: 0.14 },
    ];
    partials.forEach((p) => {
      const osc = ctx.createOscillator();
      osc.type = p.type;
      osc.frequency.value = p.f;
      const pg = ctx.createGain();
      pg.gain.value = p.gain;
      osc.connect(pg).connect(g);
      osc.start(now);
      osc.stop(now + totalS + 0.3);
    });
    // Slow tremolo LFO for the metallic "beating" pulse.
    const lfo = ctx.createOscillator();
    lfo.frequency.value = rand(0.35, 0.85);
    const lfoGain = ctx.createGain();
    lfoGain.gain.value = gain * 0.18;
    lfo.connect(lfoGain).connect(g.gain);
    lfo.start(now);
    lfo.stop(now + totalS + 0.3);
    g.gain.setValueAtTime(0, now);
    g.gain.linearRampToValueAtTime(gain, now + attackS);
    g.gain.exponentialRampToValueAtTime(0.0001, now + totalS);
    this._trackNote(g, totalS);
  }
}

// Singleton bound to the audio engine — we build it lazily so the ctx can
// be created after the user's first gesture (mobile autoplay policies).
let _singleton = null;
export function getSoundBath(audioEngine) {
  if (!_singleton) _singleton = new SoundBathEngine(audioEngine);
  return _singleton;
}

export default SoundBathEngine;
