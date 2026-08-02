import React, { useEffect, useState } from 'react';

// 4-4-6 breathing cycle synced to a smooth circle.
const PHASES = [
  { name: 'Inhale', dur: 4 },
  { name: 'Hold', dur: 4 },
  { name: 'Exhale', dur: 6 },
];

export default function Breathwork({ active }) {
  const [phaseIdx, setPhaseIdx] = useState(0);
  const [progress, setProgress] = useState(0); // 0..1 within phase

  useEffect(() => {
    if (!active) { setPhaseIdx(0); setProgress(0); return; }
    let start = performance.now();
    let phase = 0;
    let raf;
    const tick = (now) => {
      const elapsed = (now - start) / 1000;
      const dur = PHASES[phase].dur;
      const p = Math.min(elapsed / dur, 1);
      setPhaseIdx(phase);
      setProgress(p);
      if (p >= 1) {
        phase = (phase + 1) % PHASES.length;
        start = now;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active]);

  if (!active) return null;

  // Scale: inhale 0.7 -> 1.0, hold 1.0, exhale 1.0 -> 0.7
  let scale = 0.7;
  if (phaseIdx === 0) scale = 0.7 + 0.3 * progress;
  else if (phaseIdx === 1) scale = 1.0;
  else scale = 1.0 - 0.3 * progress;

  return (
    <div
      className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none pt-32 pb-64 sm:pt-20 sm:pb-40"
      data-testid="breathwork-overlay"
    >
      {/* Breathing orb — sits in the middle band between the "Now Tuning"
          frequency label (top ≈120 px tall) and the bottom transport
          (timer + play + chips ≈240 px tall on mobile). The asymmetric
          padding (pt-32 pb-64 on mobile) is the whole trick: flex-
          centering inside that reserved zone lands the 112 px orb
          exactly halfway between the frequency label's bottom edge and
          the timer's top edge — no overlap with either. Tablet+ (sm) has
          more room so the reserve shrinks. */}
      <div
        className="relative w-28 h-28 sm:w-40 sm:h-40 md:w-48 md:h-48 rounded-full border border-[#72C2AC]/50 flex items-center justify-center"
        style={{
          transform: `scale(${scale})`,
          transition: 'transform 0.1s linear',
          boxShadow: '0 0 60px rgba(114,194,172,0.25), inset 0 0 60px rgba(114,194,172,0.15)',
        }}
      >
        <div
          data-testid="breath-phase"
          className="text-[10px] sm:text-[11px] tracking-[0.24em] uppercase font-semibold text-[#C9DED6] drop-shadow-[0_1px_6px_rgba(0,0,0,0.55)]"
          style={{ transform: `scale(${1 / scale})`, transition: 'transform 0.1s linear' }}
        >
          {PHASES[phaseIdx].name}
        </div>
      </div>
    </div>
  );
}
