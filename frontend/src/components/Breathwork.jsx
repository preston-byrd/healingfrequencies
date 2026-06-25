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
    <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
      <div
        className="w-48 h-48 rounded-full border border-[#72C2AC]/50"
        style={{
          transform: `scale(${scale})`,
          transition: 'transform 0.1s linear',
          boxShadow: '0 0 60px rgba(114,194,172,0.25), inset 0 0 60px rgba(114,194,172,0.15)',
        }}
      />
      <div className="mt-6 label-tiny" data-testid="breath-phase">{PHASES[phaseIdx].name}</div>
    </div>
  );
}
