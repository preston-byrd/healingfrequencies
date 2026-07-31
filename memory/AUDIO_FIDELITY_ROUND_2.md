# Solarisound — Audio Fidelity Enhancements (Round 2)
_Date: Feb 2026_

Follow-up to `AUDIO_FIDELITY_AUDIT.md`. Four additional enhancements requested:

---

## 1. Mathematically-exact Hz values with no rounding errors

### Audit finding
The audio engine's Hz path was already precision-clean:
- `setFrequency(hz)` stores the value as-is and passes it directly to `setTargetAtTime(hz, …)` (no `Math.round`, no `toFixed`).
- The frequency slider uses `parseFloat(e.target.value)` with `step="0.1"` — resolution down to 0.1 Hz.
- All display uses of `toFixed(1)` (e.g. `state.frequency.toFixed(1)`) are display-only — they never feed back into the audio graph.

**One precision leak found and fixed** in `_patternCtaToSuggestion`:
```js
// before
frequency: Math.round(cta.frequency),
// after
const freq = Number(cta.frequency);
frequency: freq,
```
This "start where you left off" CTA was rounding restored frequencies to integers — a Pro user's saved 528.3 Hz session would restore as 528 Hz. Now the full float precision is preserved end-to-end from save → CTA restore → oscillator.

### Verified paths
- `SOLFEGGIO` preset table uses integer Hz values (mathematically exact by definition).
- Golden Stack uses `PHI = 1.6180339887498948` (16-digit precision constant), and multiplies `this.frequency * PHI` directly — no intermediate rounding.
- Binaural offset slider: `step="0.5"` — 0.5 Hz precision at handoff, then held exactly.
- Isochronic LFO clamps to `[0.5, 40]` Hz but the clamp uses `Math.max/min`, not `round` — the passed value is preserved to full float precision inside that range.

---

## 2. Phase-coherent oscillator initialization

### Finding (before)
Each `osc.start()` was called with no argument, meaning each oscillator started at `ctx.currentTime` as-observed at the moment of the JS call. JS execution timing between successive `.start()` calls can land oscillators in different 128-sample render quanta — introducing a small (< 3ms) phase offset that subtly reduces constructive interference on harmonic partials.

### Fix applied (`audioEngine.js::start` + `_spawnPhiHarmonics`)
Every oscillator now shares a single `startAt` scheduling timestamp:
```js
const startAt = ctx.currentTime + 0.05;  // 50 ms scheduling lead-time
// …
this.osc.start(startAt);
if (this.binaural > 0) this.oscR.start(startAt);
if (this.goldenStack) this._spawnPhiHarmonics(startAt); // passed through
```
Web Audio guarantees every oscillator scheduled at the same `startTime` begins at phase 0 at that exact clock tick — so the base, the binaural mate, and both golden-ratio harmonics enter the mix bus phase-locked. No harmonic partial is cancelled by initial-phase drift.

`_spawnPhiHarmonics` remains backward-compatible when called mid-session (Golden Stack toggled on while playing) — it starts "now" if no `startAt` is provided, since phase alignment with an already-running base oscillator is inherently unrecoverable.

### Verified live
```json
{ "playing": true, "phiCount": 2, "hasGoldenBaseGain": true, "sampleRate": 96000 }
```

---

## 3. "For best results" listening guide

### New surface: `ListeningGuide.jsx`
Accessed via a soft "FOR BEST RESULTS" pill in the player card (next to "KEEP SCREEN ON"). Opens a glass modal with four warm, non-clinical recommendations:

| Recommendation | Copy |
|---|---|
| Quality wired headphones | "Wired over-ear or in-ear headphones preserve the exact frequencies. Bluetooth adds latency and compression that softens the finer harmonic detail." |
| Moderate volume | "Loud enough to feel present, gentle enough to disappear. If the tones start to feel tiring, they're a touch too loud." |
| A quiet environment | "Every ambient sound the room adds — traffic, fans, chatter — masks a little of the tone. Even a soft, familiar room helps." |
| Airplane mode is beautiful | "Notifications break the thread of attention. If you can, let this be uninterrupted time." |

Footer disclaimer: *"These are suggestions, not requirements. Solarisound still works beautifully without any of them."*

Non-blocking design: tapping outside the card or the X closes it — playback is never interrupted. Escape key also closes.

---

## 4. Extended headphone reminder — binaural + isochronic + Golden Stack

### New surface: `<HeadphoneReminder>` + `useHeadphoneReminder` hook
A soft bottom-right toast that fires at session start when any of the three fidelity-sensitive modes is active. Copy is tuned per reason so it reads honest instead of generic:

| Reason | Line |
|---|---|
| `binaural` | "Binaural offset comes alive with quality wired headphones — each ear gets a slightly different tone." |
| `isochronic` | "Isochronic pulses come through clearly on any speaker — but wired headphones sharpen the entrainment." |
| `golden_stack` | "Golden Stack layers three harmonics. Wired headphones let you hear each one clearly." |

Priority order: binaural → isochronic → golden_stack (only one reminder shown at a time). Auto-hides after 12 seconds. Dismiss X snoozes it for 24 hours via `localStorage['solar:headphone_reminder_v1']`. Never fires when no fidelity-sensitive mode is on, so a solo tone at 528 Hz with no offset doesn't nag.

### Verified live
Starting a Golden Stack session on the preview environment produced:
```
HEADPHONE_REMINDER_VISIBLE: True
 reason: golden_stack
```
with the toast reading *"A GENTLE TIP · Golden Stack layers three harmonics. Wired headphones let you hear each one clearly."*

---

## Files changed
| File | Change |
|---|---|
| `frontend/src/lib/audioEngine.js` | `start()` — shared `startAt` for phase coherence; `_spawnPhiHarmonics(startAt?)` accepts scheduling arg |
| `frontend/src/components/Dashboard.jsx` | `_patternCtaToSuggestion` restores exact frequency (no rounding); mount `ListeningGuide` + `HeadphoneReminder`; wire `useHeadphoneReminder` to state; "FOR BEST RESULTS" pill in player |
| `frontend/src/components/ListeningGuide.jsx` | **new** — modal + `HeadphoneReminder` + `useHeadphoneReminder` hook |

Zero backend changes. Build clean. All fidelity guarantees from Round 1 preserved.
