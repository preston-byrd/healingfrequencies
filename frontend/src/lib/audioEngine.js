// Web Audio engine for healing frequencies + ambient layers
// Singleton-ish so multiple components share state cleanly.

class AudioEngine {
  constructor() {
    this.ctx = null;
    this.master = null;

    // Tone (oscillator) state
    this.osc = null;
    this.oscR = null; // for binaural
    this.phiOscs = []; // [{osc, gain}, ...] for golden-stack harmonics
    this.toneGain = null;
    this.frequency = 432;
    this.waveform = 'sine';
    this.binaural = 0; // hz offset for right ear
    this.toneVolume = 0.35;
    this.goldenStack = false; // when true, layer tones at f×φ and f×φ²

    // Ambient layers: { rain, ocean, forest } -> { node, gain }
    this.ambient = {};

    this.playing = false;
    this.listeners = new Set();
  }

  _ensureCtx() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.master = this.ctx.createGain();
      this.master.gain.value = 1;
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state === 'suspended') this.ctx.resume();
  }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  _emit() { this.listeners.forEach((f) => f(this.getState())); }

  getState() {
    return {
      playing: this.playing,
      frequency: this.frequency,
      waveform: this.waveform,
      binaural: this.binaural,
      toneVolume: this.toneVolume,
      goldenStack: this.goldenStack,
      ambient: Object.fromEntries(Object.entries(this.ambient).map(([k, v]) => [k, v.volume])),
    };
  }

  // ---- Tone ------------------------------------------------------------------
  start() {
    this._ensureCtx();
    if (this.playing) return;
    const ctx = this.ctx;

    this.toneGain = ctx.createGain();
    this.toneGain.gain.value = 0;
    this.toneGain.connect(this.master);

    this.osc = ctx.createOscillator();
    this.osc.type = this.waveform;
    this.osc.frequency.value = this.frequency;

    if (this.binaural > 0) {
      // Stereo split: left = freq, right = freq + binaural
      const merger = ctx.createChannelMerger(2);
      const gL = ctx.createGain();
      const gR = ctx.createGain();
      this.osc.connect(gL);
      this.oscR = ctx.createOscillator();
      this.oscR.type = this.waveform;
      this.oscR.frequency.value = this.frequency + this.binaural;
      this.oscR.connect(gR);
      gL.connect(merger, 0, 0);
      gR.connect(merger, 0, 1);
      merger.connect(this.toneGain);
      this.oscR.start();
    } else {
      this.osc.connect(this.toneGain);
    }

    this.osc.start();
    // Fade in
    this.toneGain.gain.linearRampToValueAtTime(this.toneVolume, ctx.currentTime + 0.8);

    if (this.goldenStack) this._spawnPhiHarmonics();

    this.playing = true;
    this._emit();
  }

  // Spawn the golden-ratio harmonic tones (φ¹ and φ²) at decreasing amplitude.
  _spawnPhiHarmonics() {
    const ctx = this.ctx;
    const PHI = 1.6180339887;
    const levels = [
      { mult: PHI, amp: 0.55 },     // ~1618 Hz at base 1000
      { mult: PHI * PHI, amp: 0.30 }, // φ² ≈ 2.618
    ];
    levels.forEach(({ mult, amp }) => {
      const osc = ctx.createOscillator();
      osc.type = this.waveform;
      const f = this.frequency * mult;
      // Keep audible; if above 4kHz, fold down an octave for comfort.
      osc.frequency.value = f > 4000 ? f / 2 : f;
      const g = ctx.createGain();
      g.gain.value = 0;
      osc.connect(g).connect(this.toneGain);
      osc.start();
      g.gain.linearRampToValueAtTime(amp, ctx.currentTime + 1.0);
      this.phiOscs.push({ osc, gain: g, mult });
    });
  }

  _killPhiHarmonics(fade = 0.4) {
    if (!this.ctx || this.phiOscs.length === 0) return;
    const ctx = this.ctx;
    const local = this.phiOscs;
    this.phiOscs = [];
    local.forEach(({ gain }) => {
      gain.gain.cancelScheduledValues(ctx.currentTime);
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + fade);
    });
    setTimeout(() => {
      local.forEach(({ osc, gain }) => {
        try { osc.stop(); osc.disconnect(); gain.disconnect(); } catch (e) { /* noop */ }
      });
    }, (fade + 0.05) * 1000);
  }

  stop() {
    if (!this.playing) return;
    const ctx = this.ctx;
    if (this.toneGain) {
      this.toneGain.gain.cancelScheduledValues(ctx.currentTime);
      this.toneGain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.4);
    }
    const oscs = [this.osc, this.oscR].filter(Boolean);
    this._killPhiHarmonics(0.4);
    setTimeout(() => {
      oscs.forEach((o) => { try { o.stop(); o.disconnect(); } catch (e) { /* noop */ } });
      this.osc = null; this.oscR = null;
    }, 450);
    this.playing = false;
    this._emit();
  }

  toggle() { this.playing ? this.stop() : this.start(); }

  setFrequency(hz) {
    this.frequency = hz;
    if (this.osc) this.osc.frequency.setTargetAtTime(hz, this.ctx.currentTime, 0.05);
    if (this.oscR) this.oscR.frequency.setTargetAtTime(hz + this.binaural, this.ctx.currentTime, 0.05);
    this.phiOscs.forEach(({ osc, mult }) => {
      const f = hz * mult;
      const safe = f > 4000 ? f / 2 : f;
      osc.frequency.setTargetAtTime(safe, this.ctx.currentTime, 0.05);
    });
    this._emit();
  }

  setWaveform(w) {
    this.waveform = w;
    if (this.osc) this.osc.type = w;
    if (this.oscR) this.oscR.type = w;
    this.phiOscs.forEach(({ osc }) => { osc.type = w; });
    this._emit();
  }

  setBinaural(offset) {
    const wasPlaying = this.playing;
    this.binaural = offset;
    if (wasPlaying) { this.stop(); setTimeout(() => this.start(), 460); }
    this._emit();
  }

  setToneVolume(v) {
    this.toneVolume = v;
    if (this.toneGain) this.toneGain.gain.setTargetAtTime(v, this.ctx.currentTime, 0.05);
    this._emit();
  }

  // Gracefully ramp tone + all ambient gains down to 0 over `seconds`.
  // Used by Sleep Mode for a gentle fade-to-silence at the end of a session.
  fadeOutAll(seconds = 60) {
    if (!this.ctx) return;
    const ctx = this.ctx;
    const t = ctx.currentTime;
    const ramp = (param) => {
      const cur = param.value;
      param.cancelScheduledValues(t);
      param.setValueAtTime(cur, t);
      param.linearRampToValueAtTime(0, t + seconds);
    };
    if (this.toneGain) ramp(this.toneGain.gain);
    Object.values(this.ambient).forEach((a) => { if (a.gain) ramp(a.gain.gain); });
  }

  setGoldenStack(on) {
    this.goldenStack = !!on;
    if (this.playing) {
      if (this.goldenStack && this.phiOscs.length === 0) this._spawnPhiHarmonics();
      if (!this.goldenStack && this.phiOscs.length > 0) this._killPhiHarmonics(0.4);
    }
    this._emit();
  }

  // ---- Ambient ---------------------------------------------------------------
  _makeNoise() {
    const ctx = this.ctx;
    const bufferSize = 2 * ctx.sampleRate;
    const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    let lastOut = 0;
    // Pink-ish noise for warmth
    for (let i = 0; i < bufferSize; i++) {
      const white = Math.random() * 2 - 1;
      data[i] = (lastOut + 0.02 * white) / 1.02;
      lastOut = data[i];
      data[i] *= 3.5;
    }
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.loop = true;
    return src;
  }

  _buildAmbient(kind) {
    const ctx = this.ctx;
    const src = this._makeNoise();
    const filter = ctx.createBiquadFilter();
    // User-controlled gain — always the FINAL stage. When set to 0, output is truly 0.
    const userGain = ctx.createGain();
    userGain.gain.value = 0;
    // Modulation gain — only used by ocean for wave swells; chained BEFORE userGain
    // so the LFO never touches the user gain (fixes the "0% still plays" bug).
    let modGain = null;
    let lfo = null;

    if (kind === 'rain') {
      filter.type = 'highpass'; filter.frequency.value = 1400; filter.Q.value = 0.5;
      src.connect(filter).connect(userGain);
    } else if (kind === 'ocean') {
      filter.type = 'lowpass'; filter.frequency.value = 600; filter.Q.value = 0.8;
      modGain = ctx.createGain(); modGain.gain.value = 0.55; // center of wave swell
      lfo = ctx.createOscillator(); lfo.frequency.value = 0.12;
      const lfoAmp = ctx.createGain(); lfoAmp.gain.value = 0.45;
      lfo.connect(lfoAmp).connect(modGain.gain);
      lfo.start();
      src.connect(filter).connect(modGain).connect(userGain);
    } else if (kind === 'forest') {
      filter.type = 'bandpass'; filter.frequency.value = 2200; filter.Q.value = 0.7;
      src.connect(filter).connect(userGain);
    } else if (kind === 'white') {
      // Full-spectrum noise — no filter
      src.connect(userGain);
    } else if (kind === 'brown') {
      filter.type = 'lowpass'; filter.frequency.value = 220; filter.Q.value = 0.7;
      src.connect(filter).connect(userGain);
    } else if (kind === 'wind') {
      filter.type = 'bandpass'; filter.frequency.value = 900; filter.Q.value = 1.4;
      modGain = ctx.createGain(); modGain.gain.value = 0.6;
      lfo = ctx.createOscillator(); lfo.frequency.value = 0.07;
      const lfoAmp = ctx.createGain(); lfoAmp.gain.value = 0.4;
      lfo.connect(lfoAmp).connect(modGain.gain);
      lfo.start();
      src.connect(filter).connect(modGain).connect(userGain);
    } else if (kind === 'crickets') {
      filter.type = 'bandpass'; filter.frequency.value = 4800; filter.Q.value = 6;
      modGain = ctx.createGain(); modGain.gain.value = 0.5;
      lfo = ctx.createOscillator(); lfo.frequency.value = 7; // chirp-rate
      const lfoAmp = ctx.createGain(); lfoAmp.gain.value = 0.45;
      lfo.connect(lfoAmp).connect(modGain.gain);
      lfo.start();
      src.connect(filter).connect(modGain).connect(userGain);
    } else if (kind === 'bowls') {
      // Singing-bowl-ish: low resonant band
      filter.type = 'bandpass'; filter.frequency.value = 380; filter.Q.value = 8;
      modGain = ctx.createGain(); modGain.gain.value = 0.7;
      lfo = ctx.createOscillator(); lfo.frequency.value = 0.05;
      const lfoAmp = ctx.createGain(); lfoAmp.gain.value = 0.3;
      lfo.connect(lfoAmp).connect(modGain.gain);
      lfo.start();
      src.connect(filter).connect(modGain).connect(userGain);
    } else {
      src.connect(filter).connect(userGain);
    }

    userGain.connect(this.master);
    src.start();
    return { src, filter, gain: userGain, modGain, lfo, volume: 0 };
  }

  setAmbient(kind, volume) {
    this._ensureCtx();
    if (!this.ambient[kind]) this.ambient[kind] = this._buildAmbient(kind);
    const a = this.ambient[kind];
    const v = Math.max(0, Math.min(1, Number(volume) || 0));
    a.volume = v;
    const ctx = this.ctx;
    if (v === 0) {
      // Hard mute: cancel any scheduled values + set both gain ramp target and current value to 0
      a.gain.gain.cancelScheduledValues(ctx.currentTime);
      a.gain.gain.setValueAtTime(0, ctx.currentTime);
    } else {
      a.gain.gain.cancelScheduledValues(ctx.currentTime);
      a.gain.gain.setTargetAtTime(v, ctx.currentTime, 0.2);
    }
    this._emit();
  }

  stopAllAmbient() {
    Object.values(this.ambient).forEach((a) => {
      try {
        if (this.ctx) a.gain.gain.setValueAtTime(0, this.ctx.currentTime);
        if (a.lfo) { try { a.lfo.stop(); a.lfo.disconnect(); } catch (e) { /* noop */ } }
        a.src.stop(); a.src.disconnect();
      } catch (e) { /* noop */ }
    });
    this.ambient = {};
    this._emit();
  }
}

const audioEngine = new AudioEngine();
export default audioEngine;
