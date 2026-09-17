import React, { useMemo } from 'react';

/**
 * HF-051 · WhatWeDoHero
 * Procedural "hero video" for the /what-we-do page. Instead of shipping
 * a heavy .mp4, this reuses the landing page's breathing-orb language:
 * four concentric ring pulses, a soft radial-gradient core, and 24 audio
 * bars that oscillate on staggered delays.
 *
 * HF-052 — when the FrequencySampler below is playing a tone, the parent
 * page passes `activeHz` down. The orb + rings then switch to the
 * amplified `landing-orb-breath--active` keyframe, with duration and
 * glow color tuned per Solfeggio frequency (lower Hz = slower, cooler;
 * higher Hz = faster, warmer). Effect stays subtle — a visual echo of
 * what the ears are hearing, not a distraction.
 */

// Per-frequency pulse presets. Duration scales inversely (higher pitch
// pulses faster) and glow warms toward gold as pitch rises. Ring pulse
// duration is set slightly shorter than the orb's so the rings drift
// against the orb's breath instead of locking flat.
// `barMul` (HF-053) multiplies each bar's base 6-9s duration so the
// visualizer speeds up in lockstep with the orb.
const PULSE_MAP = {
  285: { duration: '3.6s', ring: '3.2s', glow: 'rgba(114, 194, 172, 0.55)', barMul: 0.75 },
  396: { duration: '2.8s', ring: '2.5s', glow: 'rgba(196, 166, 122, 0.65)', barMul: 0.55 },
  528: { duration: '2.1s', ring: '1.9s', glow: 'rgba(232, 184, 114, 0.75)', barMul: 0.4 },
};

export default function WhatWeDoHero({ activeHz = null }) {
  const bars = useMemo(
    () => Array.from({ length: 24 }).map((_, i) => ({
      id: i,
      delay: (i * 0.18) % 4.2,
      duration: 6 + (i % 5) * 0.6,
    })),
    [],
  );

  const preset = activeHz ? PULSE_MAP[activeHz] : null;
  const orbStyle = preset
    ? {
        '--pulse-duration': preset.duration,
        '--pulse-duration-ring': preset.ring,
        '--pulse-glow': preset.glow,
      }
    : {};
  // HF-053 — bar-band amplification. When a tone is playing, publish the
  // multiplier on the container so every bar's active animation duration
  // composes cleanly with its individual base duration.
  const barBandStyle = preset ? { '--bar-duration-mul': preset.barMul } : {};

  const active = !!preset;

  return (
    <div
      className="relative w-full flex flex-col items-center mb-14"
      data-testid="wwd-hero"
      data-active-hz={activeHz || ''}
    >
      <div
        className="relative w-56 h-56 sm:w-72 sm:h-72 mb-6 flex items-center justify-center"
        style={orbStyle}
      >
        <span className={`landing-ring landing-ring-1 ${active ? 'landing-ring--active' : ''}`} />
        <span className={`landing-ring landing-ring-2 ${active ? 'landing-ring--active' : ''}`} />
        <span className={`landing-ring landing-ring-3 ${active ? 'landing-ring--active' : ''}`} />
        <span className={`landing-ring landing-ring-4 ${active ? 'landing-ring--active' : ''}`} />
        <span
          data-testid="wwd-hero-orb"
          data-active={active ? 'true' : 'false'}
          className={`landing-orb relative w-full h-full rounded-full ${active ? 'landing-orb--active' : ''}`}
          aria-hidden="true"
        />
      </div>

      <div
        className="flex items-end gap-[3px] h-10 sm:h-12 mb-1"
        aria-hidden="true"
        data-testid="wwd-hero-visualizer"
        data-active={active ? 'true' : 'false'}
        style={barBandStyle}
      >
        {bars.map((b) => (
          <span
            key={b.id}
            className={`landing-bar ${active ? 'landing-bar--active' : ''}`}
            style={{
              animationDelay: `${b.delay}s`,
              // The active rule reads `--bar-base-duration` via calc(...)
              // to derive its final duration. The base rule ignores the
              // variable and uses this same value directly.
              '--bar-base-duration': `${b.duration}s`,
              animationDuration: active ? undefined : `${b.duration}s`,
            }}
          />
        ))}
      </div>
    </div>
  );
}
