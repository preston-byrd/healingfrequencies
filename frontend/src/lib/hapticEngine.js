/**
 * hapticEngine — singleton that drives the device's vibration motor in
 * patterns synchronised with the audio session. Designed to be paired with
 * `audioEngine` so it auto-starts/stops with playback and reacts to changes
 * in binaural / isochronic / sleep / fade state.
 *
 * Browser reality check:
 *   • Chrome / Edge / Firefox on Android: full support via navigator.vibrate.
 *   • iOS Safari (including standalone PWA): NOT supported — Apple has
 *     refused to implement the Vibration API. We detect this at boot and
 *     all start/stop calls become silent no-ops. Audio remains unaffected.
 *   • Desktop: most browsers expose `vibrate` but the OS has no motor.
 *     Calls succeed silently — no breakage.
 *
 * The Chrome Vibration API caps individual pulse durations at 5,000 ms and
 * pattern arrays at 1,000 entries. We stay well below both limits.
 *
 * Patterns:
 *   • auto       — picks the best pattern from the live audio state.
 *   • heartbeat  — calm steady 60 bpm (lub-dub), slows in sleep mode.
 *   • breath478  — 4-7-8 breathing cadence (inhale long pulse · hold
 *                  silence · exhale short taps).
 *   • frequency  — pulses on the binaural/isochronic rate (alpha-theta
 *                  brain-wave entrainment by touch).
 *
 * Power / battery considerations:
 *   • Pulses are short (8–400 ms). Average duty cycle <10%.
 *   • A single setTimeout drives the loop — no rAF, no busy-wait.
 *   • OS-level silent mode + DND + low-battery vibration suppression are
 *     respected automatically by navigator.vibrate.
 *   • Smart Fade taper: when audioEngine.sessionFadeActive is true the
 *     fadeFactor scales pulse durations toward 0 so the haptic experience
 *     winds down in lock-step with the audio.
 */

import audioEngine from './audioEngine';

const LS_KEY = 'sf_haptic_prefs_v1';
const VALID_PATTERNS = ['auto', 'heartbeat', 'breath478', 'frequency'];

function _loadPrefs() {
  if (typeof localStorage === 'undefined') return { enabled: false, pattern: 'auto' };
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return { enabled: false, pattern: 'auto' };
    const p = JSON.parse(raw);
    return {
      enabled: !!p.enabled,
      pattern: VALID_PATTERNS.includes(p.pattern) ? p.pattern : 'auto',
    };
  } catch {
    return { enabled: false, pattern: 'auto' };
  }
}

class HapticEngine {
  constructor() {
    this.supported = typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function';
    const prefs = _loadPrefs();
    this.enabled = prefs.enabled;
    this.pattern = prefs.pattern;
    this.running = false;
    this._timer = null;
    this._audioUnsub = null;
    this._lastAudioState = null;
    this._breathPhaseIdx = 0; // 0=inhale, 1=hold, 2=exhale
    this._listeners = new Set();
    // ---- Screen-lock / background handling ---------------------------------
    // navigator.vibrate cannot be started from a hidden/locked document on
    // most Android browsers, and any in-flight vibration continues silently
    // rattling the chassis when the phone is set down or pocketed — which
    // acoustically bleeds into the speaker as a "pulsating vibration-like
    // noise" (bug reported by users). We solve this the correct way: pause
    // the haptic loop while the page is hidden, cancel any tail with
    // vibrate(0), and resume the loop when the page becomes visible again
    // (only if audio is still playing). Audio playback itself is
    // uninterrupted because it lives on a separate MediaSession thread.
    this._suspended = false;      // suspended-by-visibility (distinct from user disable)
    this._visHandler = null;
    if (typeof document !== 'undefined') {
      this._visHandler = () => this._onVisibilityChange();
      document.addEventListener('visibilitychange', this._visHandler);
      // pagehide fires reliably on iOS when the tab / PWA goes to background
      // and is a safer signal than visibilitychange on some Android WebViews.
      window.addEventListener?.('pagehide', this._visHandler);
    }
  }

  _onVisibilityChange() {
    const hidden = typeof document !== 'undefined' && document.hidden;
    if (hidden) {
      // Screen just locked / tab went background. Silence the motor
      // immediately so no in-flight pulse tail keeps rattling. Preserve
      // `running` so the loop resumes cleanly when the user returns.
      if (this.running) {
        this._suspended = true;
        this._cancelLoop();
        try { if (this.supported) navigator.vibrate(0); }
        catch (e) { console.warn('[hapticEngine] visibility-hide vibrate(0) failed', e); }
      }
    } else if (this._suspended) {
      this._suspended = false;
      // Only re-arm the loop if the user still has haptics on AND audio is
      // still playing. Otherwise leave things quiet.
      if (this.enabled && this.running && this._lastAudioState?.playing) {
        this._tick();
      } else {
        this.running = false;
      }
    }
  }

  // ---- Subscribers (React) -------------------------------------------------
  on(fn) { this._listeners.add(fn); return () => this._listeners.delete(fn); }
  _emit() {
    this._listeners.forEach((f) => {
      try { f(this.snapshot()); }
      catch (e) { console.warn('[hapticEngine] listener threw', e); }
    });
  }
  snapshot() { return { supported: this.supported, enabled: this.enabled, pattern: this.pattern, running: this.running }; }

  // ---- Prefs ---------------------------------------------------------------
  _savePrefs() {
    if (typeof localStorage === 'undefined') return;
    try { localStorage.setItem(LS_KEY, JSON.stringify({ enabled: this.enabled, pattern: this.pattern })); }
    catch (e) { console.warn('[hapticEngine] prefs write failed', e); }
  }

  setEnabled(v) {
    const next = !!v;
    if (this.enabled === next) return;
    this.enabled = next;
    this._savePrefs();
    if (!this.enabled) {
      this.stop();
    } else if (this._lastAudioState && this._lastAudioState.playing) {
      // Audio is already playing; start haptics in sync immediately.
      this.start();
    }
    this._emit();
  }

  setPattern(p) {
    if (!VALID_PATTERNS.includes(p)) return;
    this.pattern = p;
    this._breathPhaseIdx = 0;
    this._savePrefs();
    if (this.running) {
      // Restart the loop so the new pattern's timing kicks in immediately.
      this._cancelLoop();
      this._tick();
    }
    this._emit();
  }

  // ---- Audio engine sync ---------------------------------------------------
  // Call once at app boot. Subscribes to audioEngine state changes so the
  // haptic loop starts when playback begins and stops when it ends — fully
  // automatic for the user once enabled.
  attachToAudio() {
    if (this._audioUnsub) return;
    this._lastAudioState = audioEngine.getState();
    this._audioUnsub = audioEngine.on((state) => {
      const prev = this._lastAudioState || {};
      this._lastAudioState = state;
      if (state.playing && !prev.playing) {
        if (this.enabled) this.start();
      } else if (!state.playing && prev.playing) {
        this.stop();
      } else if (this.running) {
        // Mid-session change (frequency / binaural / isochronic / fade flag).
        // The next loop tick automatically picks up the new state — nothing
        // to do here, the recursive setTimeout already reads live state.
      }
    });
  }

  // ---- Manual lifecycle ----------------------------------------------------
  start() {
    if (!this.supported || !this.enabled) return;
    if (this.running) return;
    this.running = true;
    this._breathPhaseIdx = 0;
    this._tick();
    this._emit();
  }

  stop() {
    if (!this.running && !this._timer) {
      // Still cancel any in-flight pattern from a one-shot test.
      try { if (this.supported) navigator.vibrate(0); }
      catch (e) { console.warn('[hapticEngine] vibrate(0) failed', e); }
      return;
    }
    this.running = false;
    this._cancelLoop();
    try { if (this.supported) navigator.vibrate(0); }
    catch (e) { console.warn('[hapticEngine] vibrate(0) failed', e); }
    this._emit();
  }

  // One-shot test pulse — used by the UI's "Test" button. Always fires once
  // regardless of `enabled`, so the user can confirm device support before
  // committing to leaving the toggle on.
  test() {
    if (!this.supported) return false;
    try { return navigator.vibrate([90, 60, 90]); }
    catch (e) { console.warn('[hapticEngine] test vibrate failed', e); return false; }
  }

  _cancelLoop() {
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }

  _tick() {
    if (!this.running || !this.supported) return;
    // Bail early if screen is locked / tab hidden — see constructor for the
    // full explanation. The visibility listener re-invokes _tick on return.
    if (this._suspended || (typeof document !== 'undefined' && document.hidden)) {
      this._suspended = true;
      return;
    }
    const state = this._lastAudioState || audioEngine.getState();
    const fadeFactor = state.sessionFadeActive ? 0.45 : 1; // gentle taper in smart-fade window
    const pat = this._resolvePattern(state);
    const cycle = this._cycleFor(pat, state);
    if (!cycle) return;
    // Apply fade-factor to pulse durations (even indices in vibration array
    // are durations; odd indices are gaps — we leave gaps alone).
    let pattern = cycle.vibration;
    if (pattern && pattern.length) {
      if (fadeFactor !== 1) {
        pattern = pattern.map((v, i) => (i % 2 === 0 ? Math.max(8, Math.round(v * fadeFactor)) : v));
      }
      try { navigator.vibrate(pattern); }
      catch (e) { console.warn('[hapticEngine] vibrate(pattern) failed', e); }
    }
    this._timer = setTimeout(() => this._tick(), cycle.intervalMs);
  }

  _resolvePattern(state) {
    if (this.pattern !== 'auto') return this.pattern;
    // Auto: pick the best fit for the live session.
    // - binaural / isochronic active → entrainment pulse on that rate.
    // - sleep mode → slow heartbeat.
    // - default → calm heartbeat.
    if ((state.binaural && state.binaural > 0) || (state.isochronic && state.isochronic > 0)) {
      return 'frequency';
    }
    return 'heartbeat';
  }

  _cycleFor(pat, state) {
    if (pat === 'heartbeat') {
      // Calm resting heart rate (60 bpm) or slower in sleep mode (50 bpm).
      // Lub-dub: 60ms pulse, 80ms gap, 100ms pulse, then rest until next beat.
      const sleep = !!state._sleepMode; // set externally; falsy by default
      const bpm = sleep ? 50 : 60;
      const cycleMs = Math.round(60000 / bpm);
      return { vibration: [60, 80, 100], intervalMs: cycleMs };
    }
    if (pat === 'breath478') {
      // 4-7-8 cycle = 19 seconds total. Each tick handles one phase.
      const phase = this._breathPhaseIdx % 3;
      this._breathPhaseIdx = (this._breathPhaseIdx + 1) % 3;
      if (phase === 0) {
        // INHALE 4s: one long gentle 400ms pulse at the start of the phase.
        return { vibration: [400], intervalMs: 4000 };
      }
      if (phase === 1) {
        // HOLD 7s: complete silence — set vibration to zero (cancels any
        // tail) and just wait the duration.
        return { vibration: [0], intervalMs: 7000 };
      }
      // EXHALE 8s: 4 evenly-spaced gentle taps so the user feels the
      // release pacing. Sum of array (excluding final tail): 80*4 + 1920*3 = 6080ms.
      return { vibration: [80, 1920, 80, 1920, 80, 1920, 80], intervalMs: 8000 };
    }
    if (pat === 'frequency') {
      // Pulse on the binaural OR isochronic rate. IMPORTANT: the human
      // touch-perception limit for discrete pulses is ~4 Hz — anything
      // above that fuses into a continuous mechanical buzz on the
      // vibration motor, which (a) provides zero perceptual benefit and
      // (b) audibly rattles the phone chassis on hard surfaces, causing
      // the "pulsating vibration-like noise" bug reported after screen
      // lock. So we clamp the pulse rate to 0.5–4 Hz. When the audio
      // rate exceeds 4 Hz (typical for alpha / beta / gamma bands) we
      // divide it into a related integer sub-harmonic that stays under
      // 4 Hz — the user still feels a rhythm tied to the entrainment
      // rate, just at a comfortable cadence.
      const rawRate = state.binaural && state.binaural > 0
        ? state.binaural
        : (state.isochronic && state.isochronic > 0 ? state.isochronic : 7);
      let hz = Math.max(0.5, rawRate);
      while (hz > 4) hz = hz / 2;                     // sub-harmonic fold
      hz = Math.max(0.5, Math.min(4, hz));
      const periodMs = 1000 / hz;
      // Pulse duration ≈ 25% of period, capped at 120ms so it stays subtle.
      const pulse = Math.max(20, Math.min(120, Math.round(periodMs * 0.25)));
      return { vibration: [pulse], intervalMs: Math.max(250, Math.round(periodMs)) };
    }
    return null;
  }

  // Sleep-mode hint — wired by Dashboard's sleep mode state. The heartbeat
  // pattern uses this to drop to 50 bpm. We store it on a private state slot
  // (separate from audioEngine.getState) so we don't have to monkey-patch
  // audioEngine.
  setSleepHint(active) {
    if (!this._lastAudioState) this._lastAudioState = audioEngine.getState();
    this._lastAudioState._sleepMode = !!active;
  }
}

const haptic = new HapticEngine();
export default haptic;
