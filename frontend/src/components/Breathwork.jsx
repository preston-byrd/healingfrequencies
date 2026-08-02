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
      className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none pb-64 sm:pb-40"
      data-testid="breathwork-overlay"
    >
      {/* Breathing orb — the phase word sits inside the circle so it never
          collides with the transport (Play/Pause + timer) that anchors at
          the bottom of the visualizer. The generous bottom padding above
          shifts the flex-centering up so on portrait mobile the orb
          always sits well above the timer + Fading pill rather than
          overlapping them at short viewport heights. */}
      <div
        className="relative w-40 h-40 sm:w-48 sm:h-48 rounded-full border border-[#72C2AC]/50 flex items-center justify-center"
        style={{
          transform: `scale(${scale})`,
          transition: 'transform 0.1s linear',
          boxShadow: '0 0 60px rgba(114,194,172,0.25), inset 0 0 60px rgba(114,194,172,0.15)',
        }}
      >
        <div
          data-testid="breath-phase"
          className="text-[13px] sm:text-[11px] tracking-[0.24em] uppercase font-semibold text-[#C9DED6] drop-shadow-[0_1px_6px_rgba(0,0,0,0.55)]"
          style={{ transform: `scale(${1 / scale})`, transition: 'transform 0.1s linear' }}
        >
          {PHASES[phaseIdx].name}
        </div>
      </div>
    </div>
  );
}
