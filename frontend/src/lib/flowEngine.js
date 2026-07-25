/**
 * flowEngine.js — orchestrates 3-stage guided frequency journeys.
 *
 * A "flow" is an ordered list of 3 frequencies (each with a display name) and
 * a total session duration (15 / 30 / 60 minutes). Time is split evenly across
 * the three stages. Transitions between stages use a 10-second amplitude dip
 * to mask the frequency switch — the outgoing frequency ramps from full to
 * 0.3 over 5 s, the frequency is swapped at the trough, then the incoming
 * frequency ramps from 0.3 back to full over the next 5 s. This preserves
 * continuous audio (no silence) while musically softening the change.
 *
 * The engine broadcasts snapshot updates via a simple subscribe/notify list
 * so the UI can render stage progress + live-updating frequency labels.
 *
 * Design notes:
 *   • Uses the shared audioEngine (single oscillator) so the flow inherits
 *     the user's tone volume, EQ calibration, ambient layers, etc.
 *   • Bath / soundscape / breathwork should be manually stopped by the caller
 *     before starting a flow — flow-mode is a stand-alone experience.
 *   • Idempotent stop(): safe to call multiple times, even mid-transition.
 *   • Uses wall-clock scheduling (setTimeout) for stage transitions; the
 *     amplitude / frequency ramps themselves ride on the AudioParam curves
 *     so they remain sample-accurate.
 */
const FADE_HALF_MS = 5000;    // each half of the 10 s crossfade window
const DIP_LEVEL    = 0.3;     // trough amplitude — never drops to true silence
const ENTRY_FADE_MS = 10000;  // first-stage fade-in from silence
const EXIT_FADE_MS  = 10000;  // last-stage fade-out to silence

class FlowEngine {
  constructor(audioEngine) {
    this.audio = audioEngine;
    this._listeners = new Set();
    this._timers = [];
    this._resetSnapshot();
  }

  _resetSnapshot() {
    this.active = false;
    this.journey = null;
    this.totalMs = 0;
    this.stageMs = 0;
    this.stageIdx = 0;      // 0..2
    this.startedAt = 0;
  }

  on(fn) { this._listeners.add(fn); return () => this._listeners.delete(fn); }

  snapshot() {
    return {
      active: this.active,
      journey: this.journey,
      stageIdx: this.stageIdx,
      totalMs: this.totalMs,
      stageMs: this.stageMs,
    };
  }

  _notify() {
    const s = this.snapshot();
    this._listeners.forEach((fn) => { try { fn(s); } catch (_) { /* graceful */ } });
  }

  async start(journey, durationMin) {
    if (!journey || !Array.isArray(journey.stages) || journey.stages.length !== 3) {
      throw new Error('flowEngine.start: journey must have exactly 3 stages');
    }
    // Clear any prior flow before starting a new one.
    this.stop({ silent: true });

    this.journey = journey;
    this.totalMs = Math.max(1, durationMin) * 60 * 1000;
    this.stageMs = this.totalMs / 3;
    this.stageIdx = 0;
    this.startedAt = performance.now();
    this.active = true;

    // Apply the initial stage. Zero out entrainment/golden-stack so the
    // journey stays pure — the user can layer ambient sounds separately.
    const first = journey.stages[0];
    try {
      this.audio.setBinaural(0);
      this.audio.setIsochronic(0);
      this.audio.setGoldenStack(false);
      this.audio.setFrequency(first.hz);
    } catch (e) { /* graceful */ }

    // Snap tone volume to 0, start engine, then linear-ramp toneGain back
    // to the user's saved tone volume over 10 s for a soft entry.
    try {
      if (this.audio.ctx && this.audio.toneGain) {
        const t = this.audio.ctx.currentTime;
        this.audio.toneGain.gain.cancelScheduledValues(t);
        this.audio.toneGain.gain.setValueAtTime(0, t);
      }
      if (!this.audio.playing) await this.audio.start();
      this._rampTone(this.audio.toneVolume ?? 0.6, ENTRY_FADE_MS);
    } catch (e) { console.warn('[flowEngine] start audio failed', e); }

    this._scheduleTransitions();
    this._notify();
  }

  _scheduleTransitions() {
    // Schedule transitions AT the stage boundary (i * stageMs). The 10 s
    // crossfade window is centred on the boundary: 5 s before and 5 s after.
    // Kick off the first-half fade 5 s before the boundary so the trough
    // lands exactly on the boundary line.
    for (let i = 1; i < 3; i++) {
      const kickoffAt = i * this.stageMs - FADE_HALF_MS;
      if (kickoffAt <= 0) continue;
      this._timers.push(setTimeout(() => this._crossfadeTo(i), kickoffAt));
    }
    // Exit fade + auto-stop 10 s before total ends so the session finishes
    // in silence at exactly totalMs.
    this._timers.push(setTimeout(() => this._exitFade(), Math.max(0, this.totalMs - EXIT_FADE_MS)));
    this._timers.push(setTimeout(() => this._finish(), this.totalMs));
  }

  _rampTone(target, ms) {
    if (!this.audio.ctx || !this.audio.toneGain) return;
    const t = this.audio.ctx.currentTime;
    const now = this.audio.toneGain.gain.value;
    this.audio.toneGain.gain.cancelScheduledValues(t);
    this.audio.toneGain.gain.setValueAtTime(now, t);
    this.audio.toneGain.gain.linearRampToValueAtTime(Math.max(0, target), t + ms / 1000);
  }

  _crossfadeTo(nextIdx) {
    if (!this.active || !this.journey) return;
    const next = this.journey.stages[nextIdx];
    if (!next) return;
    const target = this.audio.toneVolume ?? 0.6;
    // Half 1 — dip amplitude to DIP_LEVEL over 5 s
    this._rampTone(target * DIP_LEVEL, FADE_HALF_MS);
    // At the trough, swap the frequency and ramp back up.
    this._timers.push(setTimeout(() => {
      if (!this.active) return;
      try { this.audio.setFrequency(next.hz); } catch (e) { /* graceful */ }
      this._rampTone(target, FADE_HALF_MS);
      this.stageIdx = nextIdx;
      this._notify();
    }, FADE_HALF_MS));
  }

  _exitFade() {
    if (!this.active) return;
    this._rampTone(0, EXIT_FADE_MS);
  }

  _finish() {
    // Session complete — hard-stop the engine, clear state, notify.
    try { this.audio.stop(); } catch (e) { /* graceful */ }
    this._clearTimers();
    this._resetSnapshot();
    this._notify();
  }

  _clearTimers() {
    this._timers.forEach((t) => clearTimeout(t));
    this._timers = [];
  }

  stop(opts = {}) {
    if (!this.active && !opts.force) {
      this._clearTimers();
      return;
    }
    this._clearTimers();
    // Graceful audio stop with a brief exit ramp unless the caller wants a
    // silent immediate cleanup (used when start() rebuilds state).
    if (!opts.silent) {
      try {
        this._rampTone(0, 1200);
        setTimeout(() => { try { this.audio.stop(); } catch (e) { /* graceful */ } }, 1200);
      } catch (e) { /* graceful */ }
    }
    this._resetSnapshot();
    this._notify();
  }
}

let _singleton = null;
export function getFlowEngine(audioEngine) {
  if (!_singleton) _singleton = new FlowEngine(audioEngine);
  return _singleton;
}

// ---------------------------------------------------------------------------
// Pre-built journeys. Each stage carries the frequency and a display name so
// the visualiser can label the tone as it plays. Solfeggio labels are pulled
// from the same conventions used across the Dashboard.
// ---------------------------------------------------------------------------

export const JOURNEYS = {
  morning_rise: {
    key: 'morning_rise',
    label: 'Morning Rise',
    description: 'Energising uplift from gamma focus into unity consciousness.',
    stages: [
      { hz:  40, name: 'Gamma',   sub: 'Focus & clarity'      },
      { hz: 528, name: 'Miracle', sub: 'DNA repair · love'    },
      { hz: 963, name: 'Unity',   sub: 'Pure being · crown'   },
    ],
  },
  deep_restore: {
    key: 'deep_restore',
    label: 'Deep Restore',
    description: 'Gentle release, renewal, and grounding to Earth\'s frequency.',
    stages: [
      { hz: 396, name: 'Liberation', sub: 'Release fear'      },
      { hz: 417, name: 'Renewal',    sub: 'Undo change'       },
      { hz: 432, name: 'Earth',      sub: 'Natural tuning'    },
    ],
  },
  night_drift: {
    key: 'night_drift',
    label: 'Night Drift',
    description: 'Sleep-descending progression from foundation into deep delta.',
    stages: [
      { hz: 174, name: 'Foundation', sub: 'Pain relief'       },
      { hz:   2, name: 'Delta',      sub: 'Deep sleep waves'  },
      { hz:   4, name: 'Theta',      sub: 'Meditation waves'  },
    ],
  },
};

export const DURATION_OPTIONS = [15, 30, 60]; // minutes
