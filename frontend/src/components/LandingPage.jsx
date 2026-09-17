import React, { useMemo, useState } from 'react';
import { ArrowRight, LifeBuoy } from 'lucide-react';
import PublicSupportModal from '@/components/PublicSupportModal';

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
  const [supportOpen, setSupportOpen] = useState(false);
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

      {/* HF-044 — Contact Us + legal links */}
      <FooterBlock onSupport={() => setSupportOpen(true)} />

      {/* HF-048 — public support modal for logged-out visitors */}
      <PublicSupportModal open={supportOpen} onClose={() => setSupportOpen(false)} />
    </div>
  );
}

function FooterBlock({ onSupport }) {
  const goLegal = (path) => (e) => {
    e.preventDefault();
    // Push the URL so the browser bar reflects the deep-link, then reload
    // once — this triggers App.js's `legalVariant` router. A silent
    // history push alone wouldn't re-render because App.js only reads
    // pathname on mount.
    try {
      window.history.pushState({}, '', path);
      window.dispatchEvent(new PopStateEvent('popstate'));
      // Fallback if PopStateEvent isn't observed (older Safari): reload.
      setTimeout(() => {
        if (window.location.pathname !== path) window.location.href = path;
      }, 30);
    } catch (_) {
      window.location.href = path;
    }
    // Simplest reliable path — just navigate. The legal pages set scroll
    // to top on mount and share the same theme, so the visual continuity
    // is preserved.
    window.location.href = path;
  };

  return (
    <footer
      data-testid="landing-footer"
      className="relative z-10 w-full max-w-2xl mx-auto pt-6 pb-2 border-t border-[rgba(92,158,140,0.12)]"
    >
      <div className="flex flex-col sm:flex-row items-center justify-center gap-2 sm:gap-6 mb-4 text-[12px] text-[#8A9A92]">
        <button
          type="button"
          onClick={onSupport}
          data-testid="landing-support-link"
          className="inline-flex items-center gap-1.5 hover:text-[#C4A67A] transition-colors cursor-pointer"
        >
          <LifeBuoy size={12} /> Support
        </button>
        <span className="hidden sm:inline text-[#5A6B65]">·</span>
        <a
          href="/what-we-do"
          data-testid="landing-what-we-do-link"
          onClick={goLegal('/what-we-do')}
          className="hover:text-[#C4A67A] transition-colors"
        >
          What We Do
        </a>
        <span className="hidden sm:inline text-[#5A6B65]">·</span>
        <a
          href="/privacy"
          data-testid="landing-privacy-link"
          onClick={goLegal('/privacy')}
          className="hover:text-[#C4A67A] transition-colors"
        >
          Privacy Policy
        </a>
        <span className="hidden sm:inline text-[#5A6B65]">·</span>
        <a
          href="/terms"
          data-testid="landing-terms-link"
          onClick={goLegal('/terms')}
          className="hover:text-[#C4A67A] transition-colors"
        >
          Terms of Service
        </a>
      </div>
      <p className="text-[10px] tracking-[0.4em] uppercase text-[#8A9A92]/60 text-center">
        Powered by silence
      </p>
      <p
        data-testid="landing-copyright"
        className="mt-2 text-[10px] tracking-[0.15em] text-[#8A9A92]/50 text-center"
      >
        © 2026 Solarisound. All Rights Reserved
      </p>
    </footer>
  );
}

export default LandingPage;
