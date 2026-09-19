import React, { useState } from 'react';
import { LifeBuoy } from 'lucide-react';
import PublicSupportModal from '@/components/PublicSupportModal';

/**
 * HF-055 — shared footer for every pre-login surface (landing page,
 * legal pages, "What We Do", auth screen, password reset). Ownership
 * of the Support modal lives here so each host just mounts <PublicFooter />
 * and gets the full experience without needing to wire modal state itself.
 *
 * On the LandingPage we still render `<PublicFooter />` inline where the
 * old inline `FooterBlock` used to sit; the two are equivalent.
 */
export default function PublicFooter({ className = '' }) {
  const [supportOpen, setSupportOpen] = useState(false);

  // Deep-link nav for the two legal routes + What We Do. The pre-login
  // router in App.js reads window.location.pathname on mount, so we push
  // history first, then hard-navigate as a Safari-safe fallback.
  const goRoute = (path) => (e) => {
    e.preventDefault();
    try {
      window.history.pushState({}, '', path);
      window.dispatchEvent(new PopStateEvent('popstate'));
    } catch (_) {
      /* noop */
    }
    // Fallback for pages that don't listen for popstate — a full nav
    // still resolves to the right static page because App.js reads
    // pathname on mount.
    window.location.href = path;
  };

  return (
    <>
      <footer
        data-testid="landing-footer"
        className={`relative z-10 w-full max-w-2xl mx-auto pt-6 pb-2 border-t border-[rgba(92,158,140,0.12)] ${className}`}
      >
        <div className="flex flex-col sm:flex-row items-center justify-center gap-2 sm:gap-6 mb-4 text-[12px] text-[#8A9A92]">
          <button
            type="button"
            onClick={() => setSupportOpen(true)}
            data-testid="landing-support-link"
            className="inline-flex items-center gap-1.5 hover:text-[#C4A67A] transition-colors cursor-pointer"
          >
            <LifeBuoy size={12} /> Support
          </button>
          <span className="hidden sm:inline text-[#5A6B65]">·</span>
          <a
            href="/what-we-do"
            data-testid="landing-what-we-do-link"
            onClick={goRoute('/what-we-do')}
            className="hover:text-[#C4A67A] transition-colors"
          >
            What We Do
          </a>
          <span className="hidden sm:inline text-[#5A6B65]">·</span>
          <a
            href="/privacy"
            data-testid="landing-privacy-link"
            onClick={goRoute('/privacy')}
            className="hover:text-[#C4A67A] transition-colors"
          >
            Privacy Policy
          </a>
          <span className="hidden sm:inline text-[#5A6B65]">·</span>
          <a
            href="/terms"
            data-testid="landing-terms-link"
            onClick={goRoute('/terms')}
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

      <PublicSupportModal open={supportOpen} onClose={() => setSupportOpen(false)} />
    </>
  );
}
