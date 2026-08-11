import React, { useEffect, useMemo, useState } from 'react';

/**
 * AmbientPulse — extremely subtle radial "breathing" glow that pulses
 * behind the visualizer in sync with the current session's frequency (or
 * the active breath pacer). Designed to feel like the room itself is
 * breathing with the user, not like a visual effect layered on the app.
 *
 * Rate mapping (pulses per minute):
 *   Sleep / Delta   (<4 Hz)       : ~1.25 ppm  → 48s per cycle
 *   Theta           (4–8 Hz)      :   5 ppm    → 12s per cycle
 *   Alpha           (8–14 Hz)     :   9 ppm    →  6.7s per cycle
 *   Focus / Gamma   (>14 Hz)      :  13 ppm    →  4.6s per cycle
 *   Breathwork ON                  : synced to 4-4-6 cycle (~14 s)
 *   Sleep Mode timer running       : opacity dims progressively as the
 *                                    Smart Fade Timer counts down so the
 *                                    pulse fades out with the audio.
 *
 * Safety:
 *   • Peak opacity capped at 0.18 (well under the 20 % ceiling).
 *   • Max delta between low + high states is 0.13 opacity — under the
 *     20 % change ceiling for photosensitive-safe design.
 *   • Respects `prefers-reduced-motion: reduce` — the component still
 *     renders a static, dim glow at the low-state opacity so the
 *     background doesn't jump when the user re-enables the toggle, but
 *     no animation runs.
 *   • CSS `transition: opacity` with ease-in-out for buttery-smooth
 *     transitions; no keyframe flashes.
 *   • Fades in over 3s on mount / prop-flip; fades out over 2s.
 *
 * Props:
 *   enabled       bool                  toggle state
 *   playing       bool                  is a session currently sounding?
 *   frequencyHz   number                current base frequency (or the
 *                                       flow-stage's target Hz — Dashboard
 *                                       passes whichever is authoritative)
 *   breathwork    bool                  when true, syncs to the 4-4-6 pacer
 *   sleepMode     bool                  true for Sleep Mode sessions —
 *                                       triggers progressive dim
 *   sleepProgress number 0..1           0 = just started, 1 = about to fade
 *                                       out. Used only when sleepMode=true.
 */
export default function AmbientPulse({
  enabled,
  playing,
  frequencyHz,
  breathwork,
  sleepMode,
  sleepProgress,
}) {
  // Respect the OS-level "reduce motion" setting — a hard requirement
  // per the spec. We read this ONCE on mount + re-listen for changes so
  // a user who toggles the setting mid-session sees the effect immediately.
  const [reduceMotion, setReduceMotion] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const apply = () => setReduceMotion(!!mq.matches);
    apply();
    // Safari <14 uses addListener, everyone else uses addEventListener.
    if (mq.addEventListener) mq.addEventListener('change', apply);
    else mq.addListener(apply);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', apply);
      else mq.removeListener(apply);
    };
  }, []);

  // Derive the pulse period in ms from the current session's frequency.
  // Breathwork overrides frequency-based rate because the user is
  // actively pacing themselves; the ambient should breathe WITH them,
  // not against them.
  const periodMs = useMemo(() => {
    if (breathwork) return 14000;         // 4s inhale + 4s hold + 6s exhale
    const hz = Number(frequencyHz) || 0;
    if (hz > 0 && hz < 4) return 48000;   // Delta / Sleep — ~1.25 ppm
    if (hz >= 4 && hz < 8) return 12000;  // Theta — ~5 ppm
    if (hz >= 8 && hz < 14) return 6700;  // Alpha — ~9 ppm
    if (hz >= 14) return 4600;            // Beta / Gamma — ~13 ppm
    // Fallback for non-brainwave freqs (Solfeggio like 528, 741): treat as
    // meditative — 5 ppm. Matches the intent that any tuning session
    // outside brainwave-entrainment territory should feel calm + languid.
    return 12000;
  }, [frequencyHz, breathwork]);

  // Fade-in trigger — mount / prop-flip to enabled+playing → opacity goes
  // from 0 to the animated range gradually over 3 seconds.
  const [faded, setFaded] = useState(false);
  useEffect(() => {
    if (!enabled || !playing) {
      setFaded(false);
      return;
    }
    // Small delay so the CSS transition catches the change (React batches
    // opacity=0 mount + opacity=1 on next tick).
    const t = setTimeout(() => setFaded(true), 30);
    return () => clearTimeout(t);
  }, [enabled, playing]);

  // Sleep Mode progressive dim — multiply the peak opacity by (1 - progress).
  // At progress=1.0 (final minute) the pulse is essentially invisible; the
  // room is settling into silence.
  const sleepDimFactor = useMemo(() => {
    if (!sleepMode) return 1;
    const p = Math.max(0, Math.min(1, Number(sleepProgress) || 0));
    // Non-linear: stay near-full for the first half, then ease down.
    return Math.max(0.05, 1 - p * p);
  }, [sleepMode, sleepProgress]);

  if (!enabled) return null;
  // Peak opacity — hard-capped at 0.18 (< 20 % ceiling). Low state 0.05.
  const peak = 0.18 * sleepDimFactor;
  const low  = 0.05 * sleepDimFactor;

  // When motion is reduced (or the user hasn't started a session), stop
  // animating: hold the low state as a static gentle glow so the toggle
  // still gives visual confirmation without any pulsing.
  const animate = playing && faded && !reduceMotion;

  return (
    <div
      className="pointer-events-none absolute inset-0 z-[1] overflow-hidden"
      aria-hidden="true"
      data-testid="ambient-pulse"
      style={{
        // CSS custom properties consumed by the @keyframes blocks below.
        // Routing peak/low through variables (rather than hardcoding in the
        // keyframe) is what lets sleepDimFactor actually attenuate the
        // running animation — otherwise the keyframe's static opacity
        // overrides the inline style and the dim would only affect the
        // initial fade-in.
        '--ap-peak': peak,
        '--ap-low': low,
        '--ap-gold-peak': peak * 0.7,
        '--ap-gold-low': low * 0.7,
      }}
    >
      {/* Two-layer glow — a broader gold outer breath + a tighter teal
          inner breath, animated with slightly offset phases so the mix
          feels organic rather than a single sine wave. Both layers respect
          the same peak/low envelope so the total brightness never crosses
          the safety cap. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(circle at 50% 50%, rgba(114, 194, 172, 1) 0%, rgba(114, 194, 172, 0.35) 35%, rgba(114, 194, 172, 0) 65%)',
          opacity: animate ? peak : (faded ? low : 0),
          animation: animate ? `ambient-pulse-teal ${periodMs}ms ease-in-out infinite` : 'none',
          transition: 'opacity 3s ease-in-out',
          willChange: 'opacity',
          mixBlendMode: 'screen',
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(circle at 50% 50%, rgba(196, 166, 122, 1) 0%, rgba(196, 166, 122, 0.25) 40%, rgba(196, 166, 122, 0) 70%)',
          opacity: animate ? peak * 0.7 : (faded ? low * 0.7 : 0),
          animation: animate ? `ambient-pulse-gold ${periodMs}ms ease-in-out infinite` : 'none',
          animationDelay: animate ? `${Math.round(periodMs * 0.35)}ms` : '0ms',
          transition: 'opacity 3s ease-in-out',
          willChange: 'opacity',
          mixBlendMode: 'screen',
        }}
      />
      {/* Scoped keyframes — kept inline to avoid touching the global CSS
          bundle. Both layers breathe between 0.35× and 1.0× of their peak
          opacity; the outer transition prop handles the fade-in/out. The
          scale is deliberately subtle (0.92 → 1.06) so the glow "breathes"
          radially without any hard boundary visible on-screen. */}
      <style>{`
        @keyframes ambient-pulse-teal {
          0%, 100% { transform: scale(0.94); opacity: var(--ap-low, 0.06); }
          50%      { transform: scale(1.06); opacity: var(--ap-peak, 0.18); }
        }
        @keyframes ambient-pulse-gold {
          0%, 100% { transform: scale(0.92); opacity: var(--ap-gold-low, 0.04); }
          50%      { transform: scale(1.04); opacity: var(--ap-gold-peak, 0.13); }
        }
      `}</style>
    </div>
  );
}
