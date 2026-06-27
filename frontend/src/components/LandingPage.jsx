import React from 'react';
import { ArrowRight } from 'lucide-react';

const LOGO_URL = 'https://customer-assets.emergentagent.com/job_frequency-healer-31/artifacts/py9qq8mo_healing_frequencies_logo_512kb.jpg';

/**
 * Solarisound / Healing Frequencies landing page.
 *
 * The brand logo (lotus figure + 7 chakra dots + sound waves) replaces the
 * generic orb; rendered with mix-blend-mode:screen so the JPEG's white
 * background dissolves into the dark cosmic theme while the violet/blue/teal
 * line-art remains crisp. A soft halo behind it sells the "radiating" feel.
 */
export function LandingPage({ onStart }) {
  // 24 vertical bars — slight per-bar timing variance keeps the wave from
  // ever lining up perfectly. Seeded once on mount.
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
      className="relative min-h-screen overflow-hidden flex flex-col items-center justify-between px-6 py-8 sm:py-10 text-center"
    >
      <div className="aurora-bg" />
      <div className="grain" aria-hidden="true" />

      {/* Tiny top-of-page brand tag — kept minimal because the logo dominates
          the hero. The full brandmark lives in the centre. */}
      <div
        data-testid="landing-wordmark"
        className="relative z-10 inline-flex items-center gap-2"
      >
        <span className="font-display italic text-2xl text-[#C4A67A] leading-none">φ</span>
        <span className="font-display tracking-[0.4em] text-[10px] uppercase text-[#8A9A92]">
          Solarisound
        </span>
      </div>

      {/* Hero */}
      <div className="relative z-10 flex-1 flex flex-col items-center justify-center w-full max-w-2xl py-6">
        {/* Logo with halo + concentric pulse rings */}
        <div className="relative w-64 h-64 sm:w-80 sm:h-80 mb-6 sm:mb-8 flex items-center justify-center">
          {/* Soft radial halo so the logo feels grounded, not floating on void */}
          <span
            className="absolute inset-0 rounded-full pointer-events-none"
            style={{
              background:
                'radial-gradient(circle at 50% 45%, rgba(196,166,122,0.18) 0%, rgba(114,194,172,0.12) 35%, transparent 65%)',
            }}
          />
          {/* Concentric pulse rings — same as before, scoped to the logo box */}
          <span className="landing-ring landing-ring-1" />
          <span className="landing-ring landing-ring-2" />
          <span className="landing-ring landing-ring-3" />
          <span className="landing-ring landing-ring-4" />
          {/* The logo itself — mix-blend-mode:screen dissolves the white JPEG bg */}
          <img
            data-testid="landing-logo"
            src={LOGO_URL}
            alt="Healing Frequencies"
            className="relative w-full h-full object-contain landing-logo-img"
            draggable={false}
          />
        </div>

        {/* Bar visualizer */}
        <div
          data-testid="landing-visualizer"
          className="flex items-end gap-[3px] h-10 sm:h-12 mb-6 sm:mb-8"
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
          className="font-display font-light text-[#E8E3D9] text-3xl sm:text-4xl lg:text-5xl leading-tight mb-3"
        >
          Tune in. Settle down. <span className="italic text-[#72C2AC]">Resonate.</span>
        </h1>

        <p className="text-sm text-[#8A9A92] max-w-md mx-auto mb-7 sm:mb-9 leading-relaxed">
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

        <p className="text-[10px] tracking-[0.3em] uppercase text-[#5C9E8C]/80 mt-5">
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
