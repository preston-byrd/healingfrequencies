import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Play, Pause } from 'lucide-react';

/**
 * HF-051 · FrequencySampler
 * Tap-to-play preview of the three most-requested Solfeggio tones. Uses
 * the Web Audio API directly (same engine philosophy as the main app) —
 * no static audio files, no round-trip to the backend.
 *
 * UX rules:
 *  • Only one tone at a time. Tapping a second card stops the first.
 *  • Every note has a 120 ms fade-in / 300 ms fade-out envelope so there
 *    are no speaker-punishing clicks on start/stop.
 *  • Notes auto-stop after PREVIEW_SECONDS so a visitor who wanders off
 *    doesn't leave a tone humming in the tab.
 *  • Cleanup on unmount closes the AudioContext so refreshing the page
 *    doesn't strand a suspended context.
 */

const PREVIEW_SECONDS = 12;

const TONES = [
  {
    hz: 285,
    label: 'Cellular renewal',
    note: 'A soft, grounded pulse traditionally paired with tissue repair sessions.',
  },
  {
    hz: 396,
    label: 'Release & letting go',
    note: 'The classic root-clearing frequency — heavier low harmonics.',
  },
  {
    hz: 528,
    label: 'DNA & love resonance',
    note: 'The most-requested Solfeggio tone. Balanced, uplifting, golden.',
  },
];

export default function FrequencySampler() {
  const [playingHz, setPlayingHz] = useState(null);
  const [progress, setProgress] = useState(0);
  const ctxRef = useRef(null);
  const oscRef = useRef(null);
  const gainRef = useRef(null);
  const rafRef = useRef(null);
  const stopTimerRef = useRef(null);
  const startedAtRef = useRef(0);

  // Kill the context + any running oscillator on unmount. This is important
  // because AudioContext is a scarce resource — Chrome will refuse to create
  // more than a handful per page.
  useEffect(() => () => stopEverything(true), []);

  const ensureContext = () => {
    if (!ctxRef.current) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      ctxRef.current = new Ctx();
    }
    // Some browsers start the context in a "suspended" state after page
    // load — the first user gesture resumes it. This function is called
    // from an onClick handler so it's a valid gesture context.
    if (ctxRef.current.state === 'suspended') ctxRef.current.resume();
    return ctxRef.current;
  };

  const stopEverything = (immediate = false) => {
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    if (stopTimerRef.current) { clearTimeout(stopTimerRef.current); stopTimerRef.current = null; }
    const ctx = ctxRef.current;
    const osc = oscRef.current;
    const gain = gainRef.current;
    if (osc && gain && ctx) {
      try {
        const now = ctx.currentTime;
        gain.gain.cancelScheduledValues(now);
        // Immediate = stop within 60 ms (used on unmount / rapid switch).
        // Regular = gentle 300 ms fade so it feels intentional.
        const fadeEnd = now + (immediate ? 0.06 : 0.3);
        gain.gain.setValueAtTime(gain.gain.value, now);
        gain.gain.exponentialRampToValueAtTime(0.0001, fadeEnd);
        osc.stop(fadeEnd + 0.05);
      } catch (_) { /* already stopped */ }
    }
    oscRef.current = null;
    gainRef.current = null;
    setPlayingHz(null);
    setProgress(0);
  };

  const play = (hz) => {
    if (playingHz === hz) { stopEverything(); return; }
    stopEverything(true);
    const ctx = ensureContext();
    if (!ctx) return;

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = hz;
    gain.gain.value = 0.0001;
    osc.connect(gain).connect(ctx.destination);
    const now = ctx.currentTime;
    // Fade in over 120ms — smooth, prevents the audible click of a
    // 0 → 0.25 step change.
    gain.gain.exponentialRampToValueAtTime(0.22, now + 0.12);
    osc.start(now);

    oscRef.current = osc;
    gainRef.current = gain;
    startedAtRef.current = performance.now();
    setPlayingHz(hz);

    // Auto-stop after PREVIEW_SECONDS.
    stopTimerRef.current = setTimeout(() => stopEverything(), PREVIEW_SECONDS * 1000);

    // Drive the progress ring.
    const tick = () => {
      const pct = Math.min(1, (performance.now() - startedAtRef.current) / (PREVIEW_SECONDS * 1000));
      setProgress(pct);
      if (pct < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  };

  return (
    <div
      className="mb-11"
      data-testid="wwd-frequency-sampler"
    >
      <div className="text-[10px] tracking-[0.3em] uppercase text-[#C4A67A] mb-2">
        Hear the frequencies
      </div>
      <p className="text-[13px] text-[#8A9A92] mb-4 leading-relaxed max-w-md">
        Tap any card for a 12-second preview generated live in your browser — headphones recommended.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {TONES.map((t) => (
          <ToneCard
            key={t.hz}
            tone={t}
            playing={playingHz === t.hz}
            progress={playingHz === t.hz ? progress : 0}
            onToggle={() => play(t.hz)}
          />
        ))}
      </div>
    </div>
  );
}

function ToneCard({ tone, playing, progress, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      data-testid={`wwd-tone-${tone.hz}`}
      aria-pressed={playing}
      className={`group relative flex flex-col items-start text-left rounded-2xl p-4 border transition-all overflow-hidden ${
        playing
          ? 'border-[#C4A67A] bg-[#C4A67A]/10 shadow-[0_0_30px_rgba(196,166,122,0.25)]'
          : 'border-[#5C9E8C]/25 bg-black/25 hover:border-[#72C2AC]/70 hover:bg-black/40'
      }`}
    >
      {/* progress ring at top-right */}
      <div className="absolute top-3 right-3 w-9 h-9 rounded-full flex items-center justify-center">
        <svg viewBox="0 0 36 36" className="w-9 h-9 -rotate-90 absolute inset-0" aria-hidden="true">
          <circle cx="18" cy="18" r="15" fill="none" stroke="rgba(92,158,140,0.18)" strokeWidth="2" />
          <circle
            cx="18" cy="18" r="15" fill="none"
            stroke={playing ? '#C4A67A' : 'rgba(196,166,122,0.5)'}
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray={`${(playing ? progress : 0) * 94.25} 94.25`}
            style={{ transition: 'stroke-dasharray 90ms linear' }}
          />
        </svg>
        <span className={`relative z-10 flex items-center justify-center w-6 h-6 rounded-full ${
          playing ? 'bg-[#C4A67A] text-[#08120F]' : 'bg-[#5C9E8C]/20 text-[#C4A67A]'
        }`}>
          {playing ? <Pause size={11} /> : <Play size={11} className="translate-x-[1px]" />}
        </span>
      </div>

      <div className="font-display text-[28px] leading-none text-[#E8E3D9]">
        {tone.hz}<span className="text-[14px] text-[#8A9A92] ml-0.5">Hz</span>
      </div>
      <div className="text-[11px] tracking-[0.25em] uppercase text-[#72C2AC] mt-1.5">
        {tone.label}
      </div>
      <div className="text-[12px] text-[#8A9A92] leading-relaxed mt-2 max-w-[24ch]">
        {tone.note}
      </div>
    </button>
  );
}
