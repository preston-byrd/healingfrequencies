import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Waves, Brain, Moon, Sparkles } from 'lucide-react';
import WhatWeDoHero from '@/components/WhatWeDoHero';
import FrequencySampler from '@/components/FrequencySampler';

/**
 * HF-050 static "What We Do" page. Rendered outside the auth gate so
 * anonymous visitors + search engines can deep-link into it from the
 * landing-page footer.
 *
 * Design mirrors LegalPage: aurora background, φ wordmark, Cormorant
 * Garamond headings, teal/gold accents. Four content sections describe
 * the core product surfaces exactly as agreed in the product spec.
 */
export default function WhatWeDoPage({ onBack }) {
  useEffect(() => {
    try { window.scrollTo(0, 0); } catch (_) { /* SSR safety */ }
  }, []);

  // HF-052 — bridge the sampler's active tone up to the hero so the orb
  // can pulse in sync. `useCallback` keeps the reference stable across
  // renders (FrequencySampler's effect uses this in its dep list).
  const [activeHz, setActiveHz] = useState(null);
  const handleActiveHz = useCallback((hz) => setActiveHz(hz), []);

  return (
    <div
      className="relative min-h-screen overflow-hidden px-6 py-10 sm:py-14"
      data-testid="what-we-do-page"
    >
      <div className="aurora-bg" />
      <div className="grain" aria-hidden="true" />

      <div className="relative z-10 max-w-2xl mx-auto">
        <button
          type="button"
          onClick={onBack}
          data-testid="what-we-do-back-top"
          className="inline-flex items-center gap-2 text-[11px] tracking-[0.2em] uppercase text-[#8A9A92] hover:text-[#C4A67A] transition-colors mb-8"
        >
          <ArrowLeft size={12} /> Back to Home
        </button>

        <div className="mb-2 flex items-baseline gap-2">
          <span className="font-display italic text-xl text-[#C4A67A] leading-none">φ</span>
          <span className="font-display tracking-[0.4em] text-[10px] uppercase text-[#8A9A92]">
            Solarisound
          </span>
        </div>
        <h1 className="font-display font-light text-[#E8E3D9] text-3xl sm:text-5xl leading-tight mb-4">
          What We Do
        </h1>
        <p className="text-[13px] text-[#C9DED6] leading-relaxed mb-10 max-w-xl">
          Solarisound is a sound-based wellness studio in your pocket — designed to help you slow
          down, come back to yourself, and hear the resonance you already carry.
        </p>

        {/* HF-051 procedural hero + Solfeggio audio sampler.
            HF-052: activeHz bridges the sampler → hero so the orb pulses
            in sync with whichever tone is currently playing. */}
        <WhatWeDoHero activeHz={activeHz} />
        <FrequencySampler onActiveHzChange={handleActiveHz} />

        <Section
          icon={<Waves size={16} className="text-[#72C2AC]" strokeWidth={1.75} />}
          eyebrow="The Core Experience"
          title="Frequencies you can feel."
          testid="section-core"
        >
          <p>
            At the heart of Solarisound is a live Web Audio engine that generates every tone in
            real time — no pre-recorded loops. Choose from the full <strong>Solfeggio preset
            grid</strong> (174 Hz through 963 Hz) or dial in any custom frequency between 20 Hz and
            1200 Hz. Layer in binaural offsets, ambient soundscapes, and isochronic pulses to
            shape the exact texture you need.
          </p>
          <p>
            Three <strong>cymatics-inspired visualizers</strong> — <em>Rings</em>, <em>Chladni</em>,
            and <em>Ripples</em> — respond to the audio in real time so you can watch your session
            take shape as it plays. It&apos;s less &quot;background music&quot; and more of an
            experience you tune in to.
          </p>
        </Section>

        <Section
          icon={<Brain size={16} className="text-[#72C2AC]" strokeWidth={1.75} />}
          eyebrow="Harmonic Blueprint"
          title="A tuning fork for you."
          testid="section-blueprint"
        >
          <p>
            Your <strong>Harmonic Blueprint</strong> is a personal frequency signature captured
            from a short voice recording — analysed entirely on your device using FFT (Fast Fourier
            Transform) and stored as a mathematical <em>Eigenmode Profile</em>. The actual audio
            never leaves your phone; only the resulting frequency map is saved.
          </p>
          <p>
            Once your baseline is set, Solarisound uses it to detect when your resonance drifts and
            gently guides you back to your natural tuning through personalised sessions. You can
            reset or delete the profile from Account Settings any time.
          </p>
        </Section>

        <Section
          icon={<Moon size={16} className="text-[#72C2AC]" strokeWidth={1.75} />}
          eyebrow="Immersive Tools"
          title="Sessions that meet you where you are."
          testid="section-immersive"
        >
          <ul className="list-none pl-0 space-y-3">
            <li>
              <strong className="text-[#E8E3D9]">Flow Mode</strong> — curated journeys that
              progress through complementary frequencies (Morning Rise, Deep Restore, Night Drift,
              and Custom Flow) so you can focus, decompress, or drift off without touching a slider.
            </li>
            <li>
              <strong className="text-[#E8E3D9]">Sleep Mode with Smart Fade</strong> — a graceful,
              brown-noise-layered descent that automatically softens the tone over a 30- to
              480-minute window so nothing jars you awake in the middle of the night.
            </li>
            <li>
              <strong className="text-[#E8E3D9]">Ambient Layer Mixing</strong> — blend forest rain,
              ocean drift, cosmic breath, and other natural textures under any frequency with
              independent volume for each layer.
            </li>
          </ul>
        </Section>

        <Section
          icon={<Sparkles size={16} className="text-[#72C2AC]" strokeWidth={1.75} />}
          eyebrow="Personalization"
          title="An assistant that learns you."
          testid="section-personalization"
        >
          <p>
            The <strong>Wellness Assistant</strong> quietly learns your listening patterns — which
            frequencies you return to, when you tend to settle in, and which sessions you keep
            open the longest. Over time it starts offering the right session for the moment
            instead of a generic recommendation list.
          </p>
          <p>
            Paired with your Harmonic Blueprint, this powers <strong>Eigenmode Journeys</strong>:
            personal soundscapes composed specifically for your frequency signature, updated as
            your baseline evolves so every session pulls you a little closer to alignment.
          </p>
        </Section>

        <div className="mt-14 pt-6 border-t border-[rgba(92,158,140,0.15)]">
          <button
            type="button"
            onClick={onBack}
            data-testid="what-we-do-back-bottom"
            className="inline-flex items-center gap-2 px-5 py-3 rounded-full bg-[#C4A67A] hover:bg-[#d6b88c] text-[#08120F] font-medium text-sm tracking-wide transition-all"
          >
            <ArrowLeft size={14} /> Back to Home
          </button>
        </div>
      </div>
    </div>
  );
}

function Section({ icon, eyebrow, title, children, testid }) {
  return (
    <section className="mb-11" data-testid={testid}>
      <div className="flex items-center gap-2 mb-3">
        <div className="w-7 h-7 rounded-full bg-[#72C2AC]/12 border border-[#72C2AC]/25 flex items-center justify-center">
          {icon}
        </div>
        <span className="text-[10px] tracking-[0.3em] uppercase text-[#72C2AC]/90">
          {eyebrow}
        </span>
      </div>
      <h2 className="font-display font-light text-[#E8E3D9] text-2xl sm:text-3xl leading-tight mb-3">
        {title}
      </h2>
      <div className="text-[14px] text-[#C9DED6] leading-relaxed space-y-3">
        {children}
      </div>
    </section>
  );
}
