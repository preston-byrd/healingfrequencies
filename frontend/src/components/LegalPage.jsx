import React, { useEffect } from 'react';
import { ArrowLeft, Mail } from 'lucide-react';

/**
 * HF-044: static legal pages — Privacy Policy + Terms of Service. Reuses the
 * dark theme + Cormorant Garamond typography via the top-level `font-display`
 * classes already in index.css. Rendered outside the auth gate so search
 * engines and unauthenticated visitors can link straight in.
 *
 * IMPORTANT — the copy below is a plain-language draft that reflects the
 * app's actual behaviours (Twilio SMS, Stripe, Emergent LLM, etc.). It is
 * NOT legal advice. Have counsel review before publishing to solarisound.com
 * production.
 */
export default function LegalPage({ variant = 'privacy', onBack }) {
  useEffect(() => {
    // Scroll to top when the page mounts so mid-scroll navigations don't
    // leave the reader in the middle of a long document.
    try { window.scrollTo(0, 0); } catch (_) { /* SSR safety */ }
  }, [variant]);

  const isPrivacy = variant === 'privacy';
  const title = isPrivacy ? 'Privacy Policy' : 'Terms of Service';

  return (
    <div className="relative min-h-screen overflow-hidden px-6 py-10 sm:py-14" data-testid={`legal-page-${variant}`}>
      <div className="aurora-bg" />
      <div className="grain" aria-hidden="true" />

      <div className="relative z-10 max-w-2xl mx-auto">
        <button
          type="button"
          onClick={onBack}
          data-testid="legal-back-button"
          className="inline-flex items-center gap-2 text-[11px] tracking-[0.2em] uppercase text-[#8A9A92] hover:text-[#C4A67A] transition-colors mb-8"
        >
          <ArrowLeft size={12} /> Back
        </button>

        <div className="mb-2 flex items-baseline gap-2">
          <span className="font-display italic text-xl text-[#C4A67A] leading-none">φ</span>
          <span className="font-display tracking-[0.4em] text-[10px] uppercase text-[#8A9A92]">Solarisound</span>
        </div>
        <h1 className="font-display font-light text-[#E8E3D9] text-3xl sm:text-5xl leading-tight mb-4">
          {title}
        </h1>
        <p className="text-[11px] tracking-widest uppercase text-[#5C9E8C]/80 mb-10">
          Effective February 2026
        </p>

        {isPrivacy ? <PrivacyBody /> : <TermsBody />}

        <div className="mt-14 pt-6 border-t border-[rgba(92,158,140,0.15)] text-[13px] text-[#8A9A92] leading-relaxed">
          Questions about this document? Email{' '}
          <a
            href="mailto:support@solarisounds.com"
            data-testid="legal-support-email"
            className="text-[#C4A67A] hover:text-[#72C2AC] transition-colors inline-flex items-center gap-1"
          >
            <Mail size={12} /> support@solarisounds.com
          </a>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section className="mb-9">
      <h2 className="font-display font-light text-[#E8E3D9] text-2xl sm:text-3xl mb-3 leading-tight">
        {title}
      </h2>
      <div className="text-[14px] text-[#C9DED6] leading-relaxed space-y-3">
        {children}
      </div>
    </section>
  );
}

function PrivacyBody() {
  return (
    <div>
      <Section title="What we collect">
        <p>
          When you create an account we store your email address, name (if you provide one), a hashed password, and a verified phone number. Every time you tune into a frequency, save a Harmonic Blueprint, or interact with the Wellness Assistant we log the interaction so we can personalise your experience over time.
        </p>
        <p>
          We also automatically capture technical details — IP address, browser type, and audit events (sign-in, plan changes, admin actions) — so we can keep your account secure.
        </p>
      </Section>

      <Section title="How we use it">
        <p>
          Your information powers the product itself: audio personalisation, Harmonic Blueprint drift detection, Weekly Alignment reminders, and the assistant's context. We also use it to send transactional emails (receipts, password resets, phone verification codes) and — only when you opt in — Weekly Alignment SMS reminders and marketing announcements.
        </p>
      </Section>

      <Section title="Third-party services">
        <p>
          Solarisound relies on a small set of vetted vendors to operate: <strong>Stripe</strong> processes payments and stores card details on our behalf; <strong>Twilio</strong> sends verification codes and Weekly Alignment SMS messages; <strong>Resend</strong> delivers our email; and <strong>Anthropic</strong>, <strong>OpenAI</strong>, and <strong>Google</strong> power the Wellness Assistant's language models. We share only the minimum data each vendor needs to do its job.
        </p>
      </Section>

      <Section title="SMS specifics">
        <p>
          By providing your phone number and checking the consent box, you agree to receive recurring automated account notifications and wellness reminders from Solarisound. Consent is not a condition of purchase. Message and data rates may apply. Reply STOP to cancel at any time. Reply HELP for help.
        </p>
        <p>
          We record every SMS we send (category, delivery status, and the last four digits of your phone number) so the admin team can audit deliverability. Full phone numbers are never written to our audit log.
        </p>
      </Section>

      <Section title="Your controls">
        <p>
          You can update or delete your information at any time from your account dashboard. Closing your account cancels any active Pro subscription at the end of your current billing period and signs you out of every device — but keeps your data on file in case you decide to reactivate by signing in again.
        </p>
        <p>
          To stop receiving marketing emails, use the unsubscribe link at the bottom of any email. To stop SMS messages, reply STOP to any text.
        </p>
      </Section>

      <Section title="Data retention">
        <p>
          Account data is retained while your account is active and for a reasonable period after cancellation so you can reactivate. Audit logs are retained for a longer window to support security investigations.
        </p>
      </Section>
    </div>
  );
}

function TermsBody() {
  return (
    <div>
      <Section title="Acceptance">
        <p>
          By creating a Solarisound account or continuing to use the service, you agree to these Terms of Service and to our Privacy Policy. If you do not agree, please do not use Solarisound.
        </p>
      </Section>

      <Section title="What Solarisound is">
        <p>
          Solarisound is a wellness listening application offering Solfeggio frequencies, brainwave entrainment, ambient soundscapes, and a Wellness Assistant. It is <strong>not medical advice, not a medical device, and not a substitute for professional care</strong>. If you have a health concern, please speak with a qualified professional.
        </p>
        <p>
          Do not use Solarisound while operating heavy machinery, driving, or in any situation that requires your full attention. If you have a history of seizures, do not use brainwave entrainment features without consulting a physician first.
        </p>
      </Section>

      <Section title="Your account">
        <p>
          You are responsible for keeping your account credentials secure and for all activity that occurs under your account. You must be at least 18 years old (or the age of majority in your jurisdiction) to create an account.
        </p>
      </Section>

      <Section title="Subscription & billing">
        <p>
          Some Solarisound features require a Pro subscription. Pro is billed monthly or annually through Stripe. You may cancel at any time via the Manage Billing portal or the in-app "Cancel service" option — your access continues until the end of the current billing period.
        </p>
        <p>
          We may adjust pricing with advance notice. Continuing to use the service after a price change constitutes acceptance of the new price.
        </p>
      </Section>

      <Section title="SMS terms">
        <p>
          By opting in to SMS messages, you agree to receive recurring automated account notifications and wellness reminders from Solarisound. Consent is not a condition of purchase. Message frequency varies. Message and data rates may apply. Reply STOP to unsubscribe. Reply HELP for help. Carriers are not liable for delayed or undelivered messages.
        </p>
      </Section>

      <Section title="Prohibited uses">
        <p>
          You may not scrape or reverse-engineer the service; interfere with other users' access; use the service to distribute malware or unlawful content; or impersonate another person. We may suspend or close accounts that violate these terms.
        </p>
      </Section>

      <Section title="Content & IP">
        <p>
          The Solarisound name, brand, and content are the property of the Solarisound team. You retain ownership of anything you submit (support messages, Harmonic Blueprint captures) and grant us a limited license to store and process that content in order to operate the service.
        </p>
      </Section>

      <Section title="Disclaimer & liability">
        <p>
          Solarisound is provided "as is" without warranties of any kind. To the fullest extent permitted by law, Solarisound is not liable for indirect, incidental, or consequential damages arising out of your use of the service.
        </p>
      </Section>

      <Section title="Governing law">
        <p>
          These terms are governed by the laws of the jurisdiction in which Solarisound is operated. Any disputes will be resolved in the courts of that jurisdiction.
        </p>
      </Section>

      <Section title="Changes">
        <p>
          We may update these terms from time to time. Material changes will be announced via email or in-app notice. Continued use of the service after a change constitutes acceptance.
        </p>
      </Section>
    </div>
  );
}
