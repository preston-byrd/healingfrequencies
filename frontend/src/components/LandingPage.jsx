import React from 'react';
import { ArrowRight } from 'lucide-react';

/**
 * Solarisound landing page.
 *
 * Minimal, calm, single-purpose. The orb breathes + the bar visualizer
 * undulates continuously, so the page never feels static — we don't need a
 * one-shot reveal cascade on top of that. Tap "Start tuning" → enters auth.
 */
export function LandingPage({ onStart }) {
  // 24 vertical bars, slight randomness so the wave never lines up perfectly.
  const bars = React.useMemo(
    () => Array.from({ length: 24 }).map((_, i) => ({
      id: i,
      delay: (i * 0.18) % 4.2,
      duration: 6 + (i % 5) * 0.6,
    })),
    [],
  );

  return (
    <div
      data-testid="landing-page"
      className="relative min-h-screen overflow-hidden flex flex-col items-center justify-between px-6 py-10 sm:py-14 text-center"
    >
      <div className="aurora-bg" />
      <div className="grain" aria-hidden="true" />

      {/* Wordmark */}
      <div
        data-testid="landing-wordmark"
        className="relative z-10 inline-flex items-center gap-3"
      >
        <span className="font-display italic text-3xl text-[#C4A67A] leading-none">φ</span>
        <span className="font-display tracking-[0.4em] text-xs uppercase text-[#8A9A92]">
          Solarisound
        </span>
      </div>

      {/* Hero */}
      <div className="relative z-10 flex-1 flex flex-col items-center justify-center w-full max-w-2xl py-10">
        {/* Concentric pulse rings around the central orb */}
        <div className="relative w-44 h-44 sm:w-56 sm:h-56 mb-8 sm:mb-10 flex items-center justify-center">
          <span className="landing-ring landing-ring-1" />
          <span className="landing-ring landing-ring-2" />
          <span className="landing-ring landing-ring-3" />
          <span className="landing-ring landing-ring-4" />
          <span className="landing-orb relative w-20 h-20 sm:w-24 sm:h-24 rounded-full" />
        </div>

        {/* Bar visualizer */}
        <div
          data-testid="landing-visualizer"
          className="flex items-end gap-[3px] h-12 sm:h-14 mb-8 sm:mb-10"
          aria-hidden="true"
        >
          {bars.map((b) => (
            <span
              key={b.id}
              className="landing-bar"
              style={{
                animationDelay: `${b.delay}s`,
                animationDuration: `${b.duration}s`,
              }}
            />
          ))}
        </div>

        {/* 1-line value prop */}
        <h1
          data-testid="landing-headline"
          className="font-display font-light text-[#E8E3D9] text-4xl sm:text-5xl lg:text-6xl leading-tight mb-4"
        >
          Tune in. Settle down. <span className="italic text-[#72C2AC]">Resonate.</span>
        </h1>

        <p className="text-sm text-[#8A9A92] max-w-md mx-auto mb-8 sm:mb-10 leading-relaxed">
          Solfeggio frequencies, brainwave entrainment, and ambient soundscapes —
          designed for the still moments in a noisy world.
        </p>

        {/* CTA */}
        <button
          data-testid="landing-start-button"
          onClick={onStart}
          className="group landing-cta-breath relative inline-flex items-center gap-3 px-9 py-4 rounded-full bg-[#C4A67A] hover:bg-[#d6b88c] text-[#08120F] font-medium tracking-wide text-sm transition-all hover:shadow-[0_0_40px_rgba(196,166,122,0.5)] active:scale-95"
        >
          <span>Start tuning</span>
          <ArrowRight size={16} className="transition-transform group-hover:translate-x-0.5" />
        </button>

        <p className="text-[10px] tracking-[0.3em] uppercase text-[#5C9E8C]/80 mt-6">
          7-day free trial · cancel anytime
        </p>
      </div>

      {/* Footer signature */}
      <div data-testid="landing-footer" className="relative z-10">
        <p className="text-[10px] tracking-[0.4em] uppercase text-[#8A9A92]/60">
          Powered by silence
        </p>
      </div>
    </div>
  );
}

export default LandingPage;
