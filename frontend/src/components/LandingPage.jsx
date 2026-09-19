import React, { useMemo } from 'react';
import { ArrowRight } from 'lucide-react';
import PublicFooter from '@/components/PublicFooter';

/**
 * Solarisound / Healing Frequencies landing page.
 *
 * Centerpiece is a procedural "breathing orb" (radial gradient + slow scale
 * animation) surrounded by 4 concentric pulse rings — calm, cosmic, and
 * lightweight (no image assets required).
 *
 * HF-044 additions:
 *   - Contact Us block with support@solarisounds.com mailto
 *   - Legal footer links to /privacy and /terms
 *
 * HF-046 (Feb 2026): removed the "Weekly Alignment via text" opt-in card.
 * The Weekly Alignment SMS toggle now lives exclusively inside the user's
 * Account page, so signed-in users control it there. The public
 * /api/public/sms-signup endpoint remains in place for future marketing
 * surfaces but is no longer exposed on the landing page.
 */
export function LandingPage({ onStart }) {
  const bars = useMemo(
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
      className="relative min-h-screen overflow-hidden flex flex-col items-center px-6 py-8 sm:py-10 text-center"
    >
      <div className="aurora-bg" />
      <div className="grain" aria-hidden="true" />

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
        <div className="relative w-56 h-56 sm:w-72 sm:h-72 mb-6 sm:mb-8 flex items-center justify-center">
          <span className="landing-ring landing-ring-1" />
          <span className="landing-ring landing-ring-2" />
          <span className="landing-ring landing-ring-3" />
          <span className="landing-ring landing-ring-4" />
          <span data-testid="landing-orb" className="landing-orb relative w-full h-full rounded-full" aria-hidden="true" />
        </div>

        <div data-testid="landing-visualizer" className="flex items-end gap-[3px] h-10 sm:h-12 mb-6 sm:mb-8" aria-hidden="true">
          {bars.map((b) => (
            <span
              key={b.id}
              className="landing-bar"
              style={{ animationDelay: `${b.delay}s`, animationDuration: `${b.duration}s` }}
            />
          ))}
        </div>

        <h1 data-testid="landing-headline" className="font-display font-light text-[#E8E3D9] text-3xl sm:text-4xl lg:text-5xl leading-tight mb-3">
          Tune in. Settle down. <span className="italic text-[#72C2AC]">Resonate.</span>
        </h1>

        <p className="text-sm text-[#8A9A92] max-w-md mx-auto mb-7 sm:mb-9 leading-relaxed">
          Solfeggio frequencies, brainwave entrainment, and ambient soundscapes —
          designed for the still moments in a noisy world.
        </p>

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

      {/* HF-044 → HF-055: unified pre-login footer shared across the
          landing page, legal pages, "What We Do", and the auth screen. */}
      <PublicFooter />
    </div>
  );
}

export default LandingPage;
