import React, { useMemo, useState } from 'react';
import { ArrowRight, Mail, MessageSquare, Loader2, CheckCircle2 } from 'lucide-react';
import PhoneInput, { isValidPhoneNumber } from 'react-phone-number-input';
import api from '@/lib/api';

/**
 * Solarisound / Healing Frequencies landing page.
 *
 * Centerpiece is a procedural "breathing orb" (radial gradient + slow scale
 * animation) surrounded by 4 concentric pulse rings — calm, cosmic, and
 * lightweight (no image assets required).
 *
 * HF-044 additions:
 *   - Contact Us block with support@solarisounds.com mailto
 *   - SMS opt-in form (phone input + explicit consent checkbox + TCPA
 *     disclosure directly beneath the submit)
 *   - Legal footer links to /privacy and /terms
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

      {/* HF-044 — SMS opt-in form. Sits below the hero but above the
          contact / legal footer so it feels like an invitation, not a
          hard sell. */}
      <SmsOptInSection />

      {/* HF-044 — Contact Us + legal links */}
      <FooterBlock />
    </div>
  );
}

function SmsOptInSection() {
  const [phone, setPhone] = useState('');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const canSubmit = phone && isValidPhoneNumber(phone) && consent && !busy;

  const submit = async (e) => {
    e.preventDefault();
    if (!canSubmit) {
      if (!phone || !isValidPhoneNumber(phone)) setErr('Please enter a valid mobile number including country code.');
      else if (!consent) setErr('Please check the box to consent.');
      return;
    }
    setBusy(true);
    setErr('');
    setMsg('');
    try {
      await api.post('/public/sms-signup', {
        phone_number: phone,
        consent: true,
        source: 'landing_page',
      });
      setMsg("Thanks — we'll be in touch.");
      setPhone('');
      setConsent(false);
    } catch (e2) {
      const detail = e2?.response?.data?.detail;
      setErr(typeof detail === 'string' ? detail : 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      data-testid="landing-sms-optin"
      className="relative z-10 w-full max-w-md mx-auto mt-10 mb-8 px-2"
    >
      <div className="glass p-5 sm:p-6 border border-[#5C9E8C]/25 text-left">
        <div className="flex items-center gap-2 mb-3">
          <MessageSquare size={13} className="text-[#72C2AC]" />
          <span className="label-tiny text-[#72C2AC]">Weekly Alignment via text</span>
        </div>
        <p className="text-[13px] text-[#C9DED6] leading-relaxed mb-4">
          Prefer a nudge over your phone? Drop your number and we'll send you the Weekly Alignment Check-in on Monday mornings.
        </p>

        <form onSubmit={submit} className="space-y-3" data-testid="landing-sms-optin-form">
          <PhoneInput
            data-testid="landing-sms-phone"
            international
            defaultCountry="US"
            value={phone}
            onChange={setPhone}
            className="admin-input w-full"
            placeholder="+1 (555) 555-5555"
            disabled={busy}
          />

          <label className="flex items-start gap-2 text-[12px] text-[#C9DED6] leading-relaxed cursor-pointer">
            <input
              data-testid="landing-sms-consent"
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              disabled={busy}
              className="mt-0.5 flex-shrink-0"
            />
            <span>
              I agree to receive SMS messages from Solarisound.
            </span>
          </label>

          <button
            type="submit"
            data-testid="landing-sms-submit"
            disabled={!canSubmit}
            className="w-full py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
          >
            {busy ? <><Loader2 size={14} className="animate-spin" /> Submitting…</> : 'Sign up for SMS'}
          </button>

          {/* Required TCPA disclosure — placed directly beneath the
              submit button per HF-044 spec so consent evidence sits
              flush with the action that grants it. */}
          <p
            data-testid="landing-sms-disclosure"
            className="text-[10px] text-[#8A9A92] leading-relaxed pt-1"
          >
            By providing your phone number and checking this box, you consent to receive recurring automated account notifications and wellness reminders from Solarisound. Consent is not a condition of purchase. Msg &amp; data rates may apply. Reply STOP to cancel at any time.
          </p>

          {msg && (
            <div
              data-testid="landing-sms-success"
              className="text-[12px] text-[#72C2AC] inline-flex items-center gap-1.5"
            >
              <CheckCircle2 size={12} /> {msg}
            </div>
          )}
          {err && (
            <div data-testid="landing-sms-error" className="text-[12px] text-[#D96C6C]">
              {err}
            </div>
          )}
        </form>
      </div>
    </section>
  );
}

function FooterBlock() {
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
        <a
          href="mailto:support@solarisounds.com"
          data-testid="landing-contact-email"
          className="inline-flex items-center gap-1.5 hover:text-[#C4A67A] transition-colors"
        >
          <Mail size={12} /> support@solarisounds.com
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
    </footer>
  );
}

export default LandingPage;
