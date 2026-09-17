import React, { useMemo } from 'react';

/**
 * HF-051 · WhatWeDoHero
 * Procedural "hero video" for the /what-we-do page. Instead of shipping
 * a heavy .mp4, this reuses the landing page's breathing-orb language:
 * four concentric ring pulses, a soft radial-gradient core, and 24 audio
 * bars that oscillate on staggered delays. The result reads like a
 * running visualization loop but weighs ~0kb of assets.
 *
 * The animations are defined in App.css (aurora-bg, landing-ring,
 * landing-orb, landing-bar) so this component is pure layout + a small
 * bit of tasteful copy.
 */
export default function WhatWeDoHero() {
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
      className="relative w-full flex flex-col items-center mb-14"
      data-testid="wwd-hero"
    >
      <div className="relative w-56 h-56 sm:w-72 sm:h-72 mb-6 flex items-center justify-center">
        <span className="landing-ring landing-ring-1" />
        <span className="landing-ring landing-ring-2" />
        <span className="landing-ring landing-ring-3" />
        <span className="landing-ring landing-ring-4" />
        <span
          data-testid="wwd-hero-orb"
          className="landing-orb relative w-full h-full rounded-full"
          aria-hidden="true"
        />
      </div>

      <div
        className="flex items-end gap-[3px] h-10 sm:h-12 mb-1"
        aria-hidden="true"
        data-testid="wwd-hero-visualizer"
      >
        {bars.map((b) => (
          <span
            key={b.id}
            className="landing-bar"
            style={{ animationDelay: `${b.delay}s`, animationDuration: `${b.duration}s` }}
          />
        ))}
      </div>
    </div>
  );
}
