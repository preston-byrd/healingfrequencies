// Web Audio engine for healing frequencies + ambient layers
// Singleton-ish so multiple components share state cleanly.

class AudioEngine {
  constructor() {
    this.ctx = null;
    this.master = null;

    // Tone (oscillator) state
    this.osc = null;
    this.oscR = null; // for binaural
    this.toneGain = null;
    this.frequency = 432;
    this.waveform = 'sine';
    this.binaural = 0; // hz offset for right ear
    this.toneVolume = 0.35;

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

    this.playing = true;
    this._emit();
  }

  stop() {
    if (!this.playing) return;
    const ctx = this.ctx;
    if (this.toneGain) {
      this.toneGain.gain.cancelScheduledValues(ctx.currentTime);
      this.toneGain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.4);
    }
    const oscs = [this.osc, this.oscR].filter(Boolean);
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
    this._emit();
  }

  setWaveform(w) {
    this.waveform = w;
    if (this.osc) this.osc.type = w;
    if (this.oscR) this.oscR.type = w;
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
    const gain = ctx.createGain();
    gain.gain.value = 0;

    if (kind === 'rain') {
      filter.type = 'highpass';
      filter.frequency.value = 1200;
      filter.Q.value = 0.5;
    } else if (kind === 'ocean') {
      filter.type = 'lowpass';
      filter.frequency.value = 600;
      filter.Q.value = 0.8;
      // slow LFO for waves
      const lfo = ctx.createOscillator();
      lfo.frequency.value = 0.12;
      const lfoGain = ctx.createGain();
      lfoGain.gain.value = 0.6;
      lfo.connect(lfoGain).connect(gain.gain);
      lfo.start();
    } else if (kind === 'forest') {
      filter.type = 'bandpass';
      filter.frequency.value = 2200;
      filter.Q.value = 0.7;
    }
    src.connect(filter).connect(gain).connect(this.master);
    src.start();
    return { src, filter, gain, volume: 0 };
  }

  setAmbient(kind, volume) {
    this._ensureCtx();
    if (!this.ambient[kind]) this.ambient[kind] = this._buildAmbient(kind);
    const a = this.ambient[kind];
    a.volume = volume;
    a.gain.gain.setTargetAtTime(volume, this.ctx.currentTime, 0.2);
    this._emit();
  }

  stopAllAmbient() {
    Object.values(this.ambient).forEach((a) => {
      try { a.gain.gain.value = 0; a.src.stop(); a.src.disconnect(); } catch (e) { /* noop */ }
    });
    this.ambient = {};
    this._emit();
  }
}

const audioEngine = new AudioEngine();
export default audioEngine;
