// Web Audio engine for healing frequencies + ambient layers
// Singleton-ish so multiple components share state cleanly.

const ARTWORK_BASE = (typeof window !== 'undefined' && window.location)
  ? window.location.origin
  : '';

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
    this.isochronic = 0; // Hz pulse rate (0 = off). True isochronic: carrier
                         // amplitude is square-wave gated between 0 and 1 at
                         // this rate, no phase relationship with binaural.

    // Isochronic gate plumbing: a Gain node sitting between the oscillator(s)
    // and toneGain. Its .gain is driven by a square LFO scaled to 0..1 when
    // isochronic > 0, or held at constant 1 when off.
    this.gateGain = null;
    this.isoLfo = null;
    this.isoOffset = null;
    this.isoScale = null;

    // Analyser for real-time visualizers (Chladni, ripples). Created lazily
    // and tapped off the master bus so it sees everything (tone + ambient).
    this.analyser = null;
    this._analyserData = null;

    // Ambient layers: { rain, ocean, forest } -> { node, gain }
    this.ambient = {};

    // Background-playback plumbing:
    //  _streamDest = MediaStreamAudioDestinationNode — re-routes the master mix
    //                into a MediaStream that an <audio> element can consume.
    //  _sinkEl     = hidden HTMLAudioElement playing that stream. Because the
    //                browser sees it as "media playback", iOS Safari + Android
    //                Chrome keep the audio thread alive when the screen locks
    //                or the tab is backgrounded. Without this, procedural
    //                Web Audio is suspended on screen-lock and the user has to
    //                tap play again on resume.
    this._streamDest = null;
    this._sinkEl = null;
    this._wakeLock = null;        // Screen Wake Lock sentinel (opt-in only)
    this._wakeLockWanted = false; // user's "keep screen awake" preference
    this._mediaTitle = 'Healing Frequencies';
    this._mediaSubtitle = '';
    this._mediaSessionBound = false;

    this.playing = false;
    this.listeners = new Set();
  }

  async _ensureCtx() {
    let created = false;
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.master = this.ctx.createGain();
      this.master.gain.value = 1;
      this.master.connect(this.ctx.destination);
      created = true;
    }
    if (this.ctx.state !== 'running') {
      try { await this.ctx.resume(); } catch (e) { /* noop */ }
    }
    // iOS Safari + Chrome-on-iOS need a 1-sample silent BufferSource to fully unlock
    // WebAudio. resume() alone is not always sufficient. Idempotent via this._unlocked.
    if (!this._unlocked) {
      try {
        const buf = this.ctx.createBuffer(1, 1, 22050);
        const src = this.ctx.createBufferSource();
        src.buffer = buf;
        src.connect(this.ctx.destination);
        if (typeof src.start === 'function') src.start(0);
        else if (typeof src.noteOn === 'function') src.noteOn(0);
        this._unlocked = true;
      } catch (e) { /* noop */ }
    }
    // Build the background-playback sink ONCE per ctx. Routes the master mix
    // through an <audio> element so the OS treats the page as media playback.
    // Critical for lock-screen / backgrounded continuation on iOS + Android.
    if (created) {
      this._buildBackgroundSink();
      this._buildAnalyser();
      this._prebuildAllAmbient();
    }
    return this.ctx;
  }

  // Real-time AnalyserNode tapped off master gain. Drives the cymatics
  // visualizer. ~1024 fft = good resolution without blowing CPU on mobile.
  _buildAnalyser() {
    if (this.analyser || !this.ctx) return;
    try {
      const a = this.ctx.createAnalyser();
      a.fftSize = 1024;
      a.smoothingTimeConstant = 0.82;
      this.master.connect(a);
      this.analyser = a;
      this._analyserData = new Uint8Array(a.frequencyBinCount);
    } catch (e) { /* noop */ }
  }

  // Public: per-frame snapshot of audio amplitude (0..1) for visualizers.
  // Cheap — reuses the same Uint8Array buffer.
  getAmplitude() {
    if (!this.analyser) return 0;
    try {
      this.analyser.getByteTimeDomainData(this._analyserData);
      // RMS around 128 (DC bias for unsigned-byte time-domain data).
      let sum = 0;
      const N = this._analyserData.length;
      for (let i = 0; i < N; i++) {
        const v = (this._analyserData[i] - 128) / 128;
        sum += v * v;
      }
      return Math.sqrt(sum / N);
    } catch (e) { return 0; }
  }

  // Pipe master gain into a MediaStream → <audio> element. Some browsers
  // (notably older iOS) don't support MediaStreamAudioDestinationNode; in that
  // case we silently fall back to ctx.destination only.
  _buildBackgroundSink() {
    if (this._streamDest || !this.ctx) return;
    try {
      if (typeof this.ctx.createMediaStreamDestination !== 'function') return;
      const dest = this.ctx.createMediaStreamDestination();
      this.master.connect(dest);
      const el = document.createElement('audio');
      el.setAttribute('data-role', 'audio-engine-sink');
      el.setAttribute('playsinline', '');
      el.setAttribute('x-webkit-playsinline', '');
      el.setAttribute('webkit-playsinline', '');
      el.preload = 'auto';
      el.crossOrigin = 'anonymous';
      el.muted = false;
      el.autoplay = false;
      // Keep it visually hidden but DON'T use display:none — some browsers
      // optimise display:none media elements out of the background-audio
      // scheduler. A 1×1 transparent off-screen element is the safe choice.
      el.style.position = 'fixed';
      el.style.left = '-9999px';
      el.style.top = '0';
      el.style.width = '1px';
      el.style.height = '1px';
      el.style.opacity = '0';
      el.style.pointerEvents = 'none';
      el.srcObject = dest.stream;
      document.body.appendChild(el);
      this._streamDest = dest;
      this._sinkEl = el;
    } catch (e) { /* unsupported — fall back to ctx.destination */ }
  }

  // ---- Media Session (lock-screen metadata + transport controls) ------------
  _bindMediaSession() {
    if (this._mediaSessionBound) return;
    if (typeof navigator === 'undefined' || !('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.setActionHandler('play', () => { this.start(); });
      navigator.mediaSession.setActionHandler('pause', () => { this.stop(); });
      navigator.mediaSession.setActionHandler('stop', () => { this.stop(); });
      // 'previoustrack' / 'nexttrack' intentionally not bound — single-stream app.
      this._mediaSessionBound = true;
    } catch (e) { /* some action handlers may be unsupported */ }
  }

  _updateMediaSession() {
    if (typeof navigator === 'undefined' || !('mediaSession' in navigator)) return;
    try {
      const title = this._mediaSubtitle
        ? `${this._mediaTitle} · ${this._mediaSubtitle}`
        : this._mediaTitle;
      const MM = window.MediaMetadata;
      if (MM) {
        navigator.mediaSession.metadata = new MM({
          title,
          artist: 'Solarisound',
          album: 'Healing Frequencies',
          artwork: [
            { src: `${ARTWORK_BASE}/icon-192.png`, sizes: '192x192', type: 'image/png' },
            { src: `${ARTWORK_BASE}/icon-512.png`, sizes: '512x512', type: 'image/png' },
          ],
        });
      }
      navigator.mediaSession.playbackState = this.playing ? 'playing' : 'paused';
    } catch (e) { /* noop */ }
  }

  // Public: let the UI tell the engine what to display on the lock-screen.
  // `subtitle` is the human-friendly preset name (e.g. "528 Hz · Love").
  setMediaInfo({ title, subtitle } = {}) {
    if (typeof title === 'string') this._mediaTitle = title;
    if (typeof subtitle === 'string') this._mediaSubtitle = subtitle;
    if (this.playing) this._updateMediaSession();
  }

  // ---- Wake Lock (opt-in: keeps the screen on while playing) ----------------
  async requestWakeLock() {
    this._wakeLockWanted = true;
    if (typeof navigator === 'undefined' || !('wakeLock' in navigator)) return false;
    if (this._wakeLock) return true;
    try {
      const sentinel = await navigator.wakeLock.request('screen');
      this._wakeLock = sentinel;
      sentinel.addEventListener('release', () => {
        // OS released it (tab hidden, low battery, etc.). Clear ref so we can
        // re-acquire on next visibility change if the user still wants it.
        if (this._wakeLock === sentinel) this._wakeLock = null;
      });
      return true;
    } catch (e) {
      return false;
    }
  }

  async releaseWakeLock() {
    this._wakeLockWanted = false;
    if (!this._wakeLock) return;
    try { await this._wakeLock.release(); } catch (e) { /* noop */ }
    this._wakeLock = null;
  }

  // Called by Dashboard on visibilitychange so the lock can be re-acquired
  // after the OS auto-released it on tab switch / lock screen.
  async reacquireWakeLockIfWanted() {
    if (!this._wakeLockWanted || this._wakeLock) return;
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
    await this.requestWakeLock();
  }

  wakeLockSupported() {
    return typeof navigator !== 'undefined' && 'wakeLock' in navigator;
  }
  wakeLockActive() { return !!this._wakeLock; }
  wakeLockWanted() { return this._wakeLockWanted; }

  // Warm-up: call from the first user gesture on the page to unlock audio on iOS/Safari
  // BEFORE the user taps a specific frequency. Safe to call multiple times.
  async unlock() {
    try { await this._ensureCtx(); } catch (e) { /* noop */ }
  }

  // Pre-build every ambient layer at the SAME audio-context time so that all loops
  // are sample-accurately aligned for the entire session — no drift between layers.
  // Each source has gain=0 until the user opens a slider, so this is silent until used.
  _prebuildAllAmbient() {
    const kinds = ['rain', 'ocean', 'forest', 'wind', 'crickets', 'bowls', 'brown', 'white'];
    const startAt = this.ctx.currentTime + 0.05;
    kinds.forEach((kind) => {
      if (this.ambient[kind]) return;
      this.ambient[kind] = this._buildAmbient(kind, startAt);
    });
  }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  _emit() { this.listeners.forEach((f) => f(this.getState())); }

  getState() {
    return {
      playing: this.playing,
      frequency: this.frequency,
      waveform: this.waveform,
      binaural: this.binaural,
      isochronic: this.isochronic,
      toneVolume: this.toneVolume,
      goldenStack: this.goldenStack,
      ambient: Object.fromEntries(Object.entries(this.ambient).map(([k, v]) => [k, v.volume])),
    };
  }

  // ---- Tone ------------------------------------------------------------------
  async start() {
    if (this.playing || this._starting) return;
    this._starting = true;
    try {
      await this._ensureCtx();
      if (this.playing) return;
      const ctx = this.ctx;

      this.toneGain = ctx.createGain();
      this.toneGain.gain.value = 0;
      this.toneGain.connect(this.master);

      // Isochronic gate sits between the oscillator(s) and toneGain. When
      // isochronic === 0 it acts as a unity pass-through (gain = 1). When > 0
      // a square-wave LFO drives this.gain between 0 and 1 at the pulse rate
      // for true isochronic entrainment.
      this.gateGain = ctx.createGain();
      this.gateGain.gain.value = 1;
      this.gateGain.connect(this.toneGain);

      this.osc = ctx.createOscillator();
      this.osc.type = this.waveform;
      this.osc.frequency.value = this.frequency;

      if (this.binaural > 0) {
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
        merger.connect(this.gateGain);
        this.oscR.start();
      } else {
        this.osc.connect(this.gateGain);
      }

      this.osc.start();
      this.toneGain.gain.linearRampToValueAtTime(this.toneVolume, ctx.currentTime + 0.8);

      if (this.isochronic > 0) this._spawnIsochronicLFO(this.isochronic);
      if (this.goldenStack) this._spawnPhiHarmonics();

      // Pause→Resume: restore the ambient mix that was active when the user
      // last hit Stop / paused. Cleared after restore so subsequent stops
      // re-snapshot a fresh state.
      if (this._pendingAmbient) {
        Object.entries(this._pendingAmbient).forEach(([kind, vol]) => {
          this.setAmbient(kind, vol);
        });
        this._pendingAmbient = null;
      }

      this.playing = true;

      // Background-audio sink: start the hidden <audio> element NOW (we're
      // still inside the user-gesture call stack since start() is invoked
      // from a tap). This is what keeps audio alive when the screen locks.
      if (this._sinkEl) {
        try {
          const p = this._sinkEl.play();
          if (p && typeof p.catch === 'function') p.catch(() => { /* user-gesture missing — silent */ });
        } catch (e) { /* noop */ }
      }
      // Bind Media Session handlers + push current metadata so lock-screen
      // controls show "Now playing: 528 Hz · Love · Solarisound".
      this._bindMediaSession();
      this._updateMediaSession();
      // Re-acquire wake lock if user previously opted-in (e.g., after stop→start).
      if (this._wakeLockWanted) this.requestWakeLock();

      this._emit();
    } finally {
      this._starting = false;
    }
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
      osc.connect(g).connect(this.gateGain || this.toneGain);
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

  // Isochronic: drive gateGain.gain between 0 and 1 at `hz` via a square LFO
  // + DC offset (so the param sees a 0..1 signal, not -1..1). Cancels the
  // constant 1.0 baseline by setting gain.value=0 before connecting.
  _spawnIsochronicLFO(hz) {
    if (!this.ctx || !this.gateGain) return;
    this._killIsochronicLFO(0); // idempotent
    const ctx = this.ctx;
    const lfo = ctx.createOscillator();
    lfo.type = 'square';
    lfo.frequency.value = Math.max(0.5, Math.min(40, hz));
    const scale = ctx.createGain();
    scale.gain.value = 0.5; // square is -1..1 → scaled to -0.5..0.5
    const offset = ctx.createConstantSource();
    offset.offset.value = 0.5; // shift to 0..1
    // Clear the static baseline (was 1.0) so the LFO + offset are the only drivers.
    this.gateGain.gain.cancelScheduledValues(ctx.currentTime);
    this.gateGain.gain.setValueAtTime(0, ctx.currentTime);
    lfo.connect(scale).connect(this.gateGain.gain);
    offset.connect(this.gateGain.gain);
    lfo.start();
    offset.start();
    this.isoLfo = lfo;
    this.isoScale = scale;
    this.isoOffset = offset;
  }

  _killIsochronicLFO(restoreBaseline = 1) {
    if (!this.ctx) return;
    const ctx = this.ctx;
    [this.isoLfo, this.isoScale, this.isoOffset].forEach((n) => {
      if (!n) return;
      try { if (typeof n.stop === 'function') n.stop(); } catch (e) { /* noop */ }
      try { n.disconnect(); } catch (e) { /* noop */ }
    });
    this.isoLfo = null;
    this.isoScale = null;
    this.isoOffset = null;
    if (this.gateGain && restoreBaseline) {
      this.gateGain.gain.cancelScheduledValues(ctx.currentTime);
      this.gateGain.gain.setValueAtTime(restoreBaseline, ctx.currentTime);
    }
  }

  // Public: set isochronic pulse rate in Hz. 0 disables. Hot-swaps the LFO
  // without rebuilding the audio graph (no glitches mid-session).
  setIsochronic(hz) {
    const v = Math.max(0, Math.min(40, Number(hz) || 0));
    this.isochronic = v;
    if (this.playing && this.gateGain) {
      if (v <= 0) {
        this._killIsochronicLFO(1);
      } else if (this.isoLfo) {
        this.isoLfo.frequency.setTargetAtTime(v, this.ctx.currentTime, 0.05);
      } else {
        this._spawnIsochronicLFO(v);
      }
    }
    this._emit();
  }

  stop() {
    const ctx = this.ctx;
    // Snapshot the currently-playing ambient volumes BEFORE we fade them out — a
    // subsequent start() (i.e., Pause → Play) will restore exactly this mix so
    // soundscapes resume seamlessly. Cleared by start() after restore, or by
    // selectSoundscape / stopAllAmbient when the user explicitly resets.
    if (ctx) {
      const snap = {};
      Object.entries(this.ambient).forEach(([k, a]) => {
        if (a && a.volume > 0.001) snap[k] = a.volume;
      });
      this._pendingAmbient = Object.keys(snap).length > 0 ? snap : null;

      // Fade every active ambient layer to 0 smoothly so the volume "dials down"
      // before playback fully stops — fires whether or not the tone is playing
      // (e.g., user paused the tone earlier, ambient is still humming).
      Object.values(this.ambient).forEach((a) => {
        if (a && a.gain && a.volume > 0.001) {
          a.gain.gain.cancelScheduledValues(ctx.currentTime);
          a.gain.gain.setValueAtTime(a.gain.gain.value, ctx.currentTime);
          a.gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.8);
          a.volume = 0;
        }
      });
    }

    if (!this.playing) {
      this._emit();
      return;
    }

    if (this.toneGain) {
      this.toneGain.gain.cancelScheduledValues(ctx.currentTime);
      this.toneGain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.8);
    }
    // Capture the CURRENT oscillators locally and null the instance refs immediately
    // so that a subsequent start() (within the cleanup window) doesn't get its
    // brand-new oscillator clobbered when the cleanup timeout fires.
    const oscsLocal = [this.osc, this.oscR].filter(Boolean);
    this.osc = null;
    this.oscR = null;
    this._killPhiHarmonics(0.8);
    this._killIsochronicLFO(0); // tear down LFO; gateGain disconnected via timeout below
    setTimeout(() => {
      oscsLocal.forEach((o) => { try { o.stop(); o.disconnect(); } catch (e) { /* noop */ } });
      if (this.gateGain) {
        try { this.gateGain.disconnect(); } catch (e) { /* noop */ }
        this.gateGain = null;
      }
      // Pause the background-audio sink AFTER the fade completes so we don't
      // cut off the tail. Leaving it playing forever would also work, but it
      // would keep the OS media indicator lit even when the user stopped.
      if (this._sinkEl && !this.playing) {
        try { this._sinkEl.pause(); } catch (e) { /* noop */ }
      }
    }, 850);
    this.playing = false;
    // Reflect stopped state to the lock-screen / OS controls + drop wake lock.
    if (typeof navigator !== 'undefined' && 'mediaSession' in navigator) {
      try { navigator.mediaSession.playbackState = 'paused'; } catch (e) { /* noop */ }
    }
    if (this._wakeLock) {
      // Release the OS sentinel but PRESERVE the user's "wanted" preference
      // so the next start() re-acquires automatically.
      try { this._wakeLock.release(); } catch (e) { /* noop */ }
      this._wakeLock = null;
    }
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
    const oldOffset = this.binaural;
    this.binaural = offset;
    // Only rebuild the graph when the binaural TOPOLOGY changes (i.e., turning the
    // right-ear oscillator on or off). For pure offset tweaks while already binaural,
    // just retune the right oscillator live — no stop/start that would interrupt audio.
    const topologyChange = (oldOffset === 0) !== (offset === 0);
    if (this.playing && topologyChange) {
      this.stop();
      setTimeout(() => this.start(), 460);
    } else if (this.oscR) {
      this.oscR.frequency.setTargetAtTime(this.frequency + offset, this.ctx.currentTime, 0.05);
    }
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

  _buildAmbient(kind, startAt) {
    const ctx = this.ctx;
    const src = this._makeNoise();
    const filter = ctx.createBiquadFilter();
    const userGain = ctx.createGain();
    userGain.gain.value = 0;
    let modGain = null;
    let lfo = null;

    // All time-based starts use the unified `startAt` so every layer is sample-aligned.
    const t0 = startAt || ctx.currentTime;
    if (kind === 'rain') {
      filter.type = 'highpass'; filter.frequency.value = 1400; filter.Q.value = 0.5;
      src.connect(filter).connect(userGain);
    } else if (kind === 'ocean') {
      filter.type = 'lowpass'; filter.frequency.value = 600; filter.Q.value = 0.8;
      modGain = ctx.createGain(); modGain.gain.value = 0.55;
      lfo = ctx.createOscillator(); lfo.frequency.value = 0.12;
      const lfoAmp = ctx.createGain(); lfoAmp.gain.value = 0.45;
      lfo.connect(lfoAmp).connect(modGain.gain);
      lfo.start(t0);
      src.connect(filter).connect(modGain).connect(userGain);
    } else if (kind === 'forest') {
      filter.type = 'bandpass'; filter.frequency.value = 2200; filter.Q.value = 0.7;
      src.connect(filter).connect(userGain);
    } else if (kind === 'white') {
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
      lfo.start(t0);
      src.connect(filter).connect(modGain).connect(userGain);
    } else if (kind === 'crickets') {
      filter.type = 'bandpass'; filter.frequency.value = 4800; filter.Q.value = 6;
      modGain = ctx.createGain(); modGain.gain.value = 0.5;
      lfo = ctx.createOscillator(); lfo.frequency.value = 7;
      const lfoAmp = ctx.createGain(); lfoAmp.gain.value = 0.45;
      lfo.connect(lfoAmp).connect(modGain.gain);
      lfo.start(t0);
      src.connect(filter).connect(modGain).connect(userGain);
    } else if (kind === 'bowls') {
      filter.type = 'bandpass'; filter.frequency.value = 380; filter.Q.value = 8;
      modGain = ctx.createGain(); modGain.gain.value = 0.7;
      lfo = ctx.createOscillator(); lfo.frequency.value = 0.05;
      const lfoAmp = ctx.createGain(); lfoAmp.gain.value = 0.3;
      lfo.connect(lfoAmp).connect(modGain.gain);
      lfo.start(t0);
      src.connect(filter).connect(modGain).connect(userGain);
    } else {
      src.connect(filter).connect(userGain);
    }

    userGain.connect(this.master);
    src.start(t0);
    return { src, filter, gain: userGain, modGain, lfo, volume: 0 };
  }

  setAmbient(kind, volume) {
    // Ambient nodes are pre-built in _ensureCtx so all 8 layers are sample-aligned.
    // If ctx isn't ready yet (very first interaction), trigger unlock — we'll catch
    // the slider movement on the next tick.
    if (!this.ctx) { this.unlock(); return; }
    if (!this.ambient[kind]) {
      // Fallback: lazily build if somehow missing (e.g., after a hot reload),
      // aligned to the existing ambient layers via the same current time.
      this.ambient[kind] = this._buildAmbient(kind, this.ctx.currentTime);
    }
    const a = this.ambient[kind];
    const v = Math.max(0, Math.min(1, Number(volume) || 0));
    a.volume = v;
    const ctx = this.ctx;
    if (v === 0) {
      // Smooth fade-to-silent instead of instant cutoff — used both for slider
      // drags to 0 and for soundscape stop. The 0.8s ramp matches the tone fade
      // in stop() so multi-layer mixes wind down together.
      const current = a.gain.gain.value || 0;
      a.gain.gain.cancelScheduledValues(ctx.currentTime);
      if (current > 0.001) {
        a.gain.gain.setValueAtTime(current, ctx.currentTime);
        a.gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.8);
      } else {
        a.gain.gain.setValueAtTime(0, ctx.currentTime);
      }
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
    this._pendingAmbient = null;
    this._emit();
  }
}

const audioEngine = new AudioEngine();
export default audioEngine;
