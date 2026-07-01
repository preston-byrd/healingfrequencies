import React, { useEffect, useMemo } from 'react';
import { Sparkles } from 'lucide-react';

/**
 * Cosmic celebration overlay shown after a successful first Pro upgrade.
 * - CSS-only confetti (no extra library) — particles drift down with phi-ratio delays.
 * - The φ glyph pulses at the centre.
 * - One CTA: "Start your first Pro session" returns the user to the player.
 */
export function ThankYouCelebration({ planLabel, onStart }) {
  // 36 confetti particles, each with a random hue from the app palette and a
  // staggered animation-delay so the burst feels organic rather than mechanical.
  const particles = useMemo(() => {
    const palette = ['#C4A67A', '#72C2AC', '#5C9E8C', '#E8E3D9', '#8FB3A8'];
    return Array.from({ length: 36 }).map((_, i) => ({
      id: i,
      left: Math.random() * 100,
      delay: (i * 0.07) % 2.4,
      duration: 3.2 + Math.random() * 2.2,
      size: 6 + Math.random() * 8,
      color: palette[i % palette.length],
      drift: (Math.random() - 0.5) * 80,
    }));
  }, []);

  // ESC closes (treat as "start session" since the modal is celebratory, not a decision).
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onStart(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onStart]);

  return (
    <div
      data-testid="thank-you-celebration"
      className="fixed inset-0 z-[60] flex items-center justify-center px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="thankyou-title"
    >
      {/* Backdrop with soft cosmic glow */}
      <div className="absolute inset-0 bg-[#06100E]/85 backdrop-blur-md" />
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            'radial-gradient(ellipse at center, rgba(196,166,122,0.18) 0%, rgba(114,194,172,0.08) 35%, transparent 65%)',
        }}
      />

      {/* Confetti layer */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none" aria-hidden="true">
        {particles.map((p) => (
          <span
            key={p.id}
            className="thankyou-confetti"
            style={{
              left: `${p.left}%`,
              width: `${p.size}px`,
              height: `${p.size}px`,
              background: p.color,
              animationDelay: `${p.delay}s`,
              animationDuration: `${p.duration}s`,
              '--drift': `${p.drift}px`,
            }}
          />
        ))}
      </div>

      {/* Card */}
      <div className="relative z-10 max-w-md w-full text-center">
        {/* φ glyph with pulsing rings */}
        <div className="relative inline-flex items-center justify-center mb-8">
          <div className="thankyou-ring thankyou-ring-1" />
          <div className="thankyou-ring thankyou-ring-2" />
          <div className="thankyou-ring thankyou-ring-3" />
          <div
            className="thankyou-phi relative flex items-center justify-center w-24 h-24 rounded-full"
            style={{
              background: 'radial-gradient(circle at 30% 30%, #C4A67A 0%, #8a6f47 60%, #5c4a30 100%)',
              boxShadow: '0 0 60px rgba(196,166,122,0.55), inset 0 0 20px rgba(255,255,255,0.15)',
            }}
          >
            <span className="font-display text-6xl text-[#08120F] leading-none" style={{ fontStyle: 'italic' }}>
              φ
            </span>
          </div>
        </div>

        <div className="label-tiny text-[#C4A67A] mb-3 inline-flex items-center justify-center gap-1.5">
          <Sparkles size={12} /> Welcome to Pro
        </div>
        <h2
          id="thankyou-title"
          data-testid="thankyou-title"
          className="font-display text-4xl sm:text-5xl font-light text-[#E8E3D9] mb-3 leading-tight"
        >
          Thank you for tuning&nbsp;in.
        </h2>
        <p className="text-sm text-[#8A9A92] max-w-sm mx-auto mb-8 leading-relaxed">
          Your <span className="text-[#72C2AC]">{planLabel}</span> plan is active. Sound baths, brainwave tones,
          φ Golden Stack, EQ calibration, pulsing haptics and Sleep Mode are now yours to wander through.
        </p>

        <button
          data-testid="thankyou-start-button"
          onClick={onStart}
          className="group relative inline-flex items-center gap-2.5 px-7 py-3.5 rounded-full bg-[#C4A67A] hover:bg-[#d6b88c] text-[#08120F] font-medium tracking-wide text-sm transition-all hover:shadow-[0_0_30px_rgba(196,166,122,0.5)] active:scale-95"
        >
          <Sparkles size={14} />
          Start your first Pro session
        </button>
      </div>
    </div>
  );
}

export default ThankYouCelebration;
