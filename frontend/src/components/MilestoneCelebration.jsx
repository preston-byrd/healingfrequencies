import React, { useEffect, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Sparkles, ArrowRight } from 'lucide-react';

/**
 * MilestoneCelebration — full-screen acknowledgement card shown when the
 * user earns any HB milestone for the first time. Deliberately styled to
 * feel like a genuine moment of recognition rather than a gamification
 * badge — soft cymatics-inspired backdrop, warm typography, single CTA.
 *
 * Props:
 *   milestone { key, title, message, achieved_at, meta }
 *   onDismiss()   — parent handles the POST /celebrate call
 */
export default function MilestoneCelebration({ milestone, onDismiss }) {
  const scrollRef = useRef(null);
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const raf = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = 0;
      });
    });
    return () => {
      cancelAnimationFrame(raf);
      document.body.style.overflow = prev;
    };
  }, []);

  const dateLabel = useMemo(() => _fmtDate(milestone?.achieved_at), [milestone]);

  if (!milestone) return null;

  return createPortal((
    <div
      ref={scrollRef}
      className="fixed inset-0 z-[85] overflow-y-auto"
      data-testid="milestone-celebration"
      style={{
        background: 'radial-gradient(circle at 50% 40%, rgba(196,166,122,0.14), rgba(8,18,15,0.96) 55%, #050d0b 100%)',
      }}
    >
      <div className="min-h-full flex items-start justify-center px-4 py-8">
        <div className="max-w-lg w-full relative">
        {/* Cymatics-inspired visual — layered concentric SVG rings that
            slowly breathe. Pure inline SVG so it works offline + inherits
            the theme without any dependency. */}
        <CymaticVisual milestoneKey={milestone.key} />

        {/* Message card */}
        <div className="relative mt-8 rounded-2xl bg-[rgba(8,18,15,0.7)] backdrop-blur-xl border border-[rgba(196,166,122,0.25)] p-8 sm:p-10 text-center">
          <div className="flex items-center justify-center gap-2 mb-3">
            <Sparkles size={13} className="text-[#C4A67A]" />
            <span className="label-tiny text-[#C4A67A]">Milestone</span>
          </div>
          <h2
            className="font-display text-2xl sm:text-3xl text-[#E8E3D9] leading-snug"
            data-testid="milestone-celebration-title"
          >
            {milestone.title}
          </h2>
          <p
            className="text-[#C6CDCA] text-sm sm:text-base leading-relaxed mt-5 max-w-md mx-auto"
            data-testid="milestone-celebration-message"
          >
            {milestone.message}
          </p>
          {dateLabel && (
            <div
              className="text-[10px] font-mono text-[#8A9A92] mt-6 tracking-wide uppercase"
              data-testid="milestone-celebration-date"
            >
              {dateLabel}
            </div>
          )}
          <button
            type="button"
            onClick={onDismiss}
            data-testid="milestone-celebration-continue"
            className="mt-8 inline-flex items-center gap-2 text-[#0d1a17] bg-[#C4A67A] hover:bg-[#d4b58a] transition text-sm px-6 py-3 rounded-full"
          >
            Continue my journey <ArrowRight size={13} />
          </button>
        </div>
        </div>
      </div>
    </div>
  ), document.body);
}


/**
 * CymaticVisual — one of six deterministic SVG patterns keyed to the
 * milestone type. Each renders concentric rings + petal geometry that
 * evokes a Chladni / cymatics figure without being visually noisy.
 * Rings pulse via CSS keyframes so it feels alive but never distracting.
 */
function CymaticVisual({ milestoneKey }) {
  // Petal counts map to the numerology of each milestone key — feels
  // deliberate without being explained. Keeps every celebration
  // distinctive.
  const petals = {
    first_eigenmode: 6,
    first_gap_closed: 8,
    streak_7: 7,
    streak_30: 12,
    resonance_90: 9,
    full_spectrum_improvement: 6,
  }[milestoneKey] || 6;

  const rings = 4;
  const size = 280;
  const cx = size / 2;
  const cy = size / 2;

  return (
    <div className="flex items-center justify-center relative">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="opacity-90"
        aria-hidden="true"
      >
        {/* Petal rosette */}
        <g style={{ transformOrigin: `${cx}px ${cy}px` }}>
          {Array.from({ length: petals }).map((_, i) => {
            const angle = (i * 360) / petals;
            const petalRy = size * 0.28;
            const petalRx = size * 0.09;
            return (
              <ellipse
                key={`p${i}`}
                cx={cx}
                cy={cy - petalRy * 0.4}
                rx={petalRx}
                ry={petalRy}
                fill="none"
                stroke="#C4A67A"
                strokeOpacity={0.25}
                strokeWidth="0.9"
                transform={`rotate(${angle} ${cx} ${cy})`}
              />
            );
          })}
        </g>
        {/* Concentric rings */}
        {Array.from({ length: rings }).map((_, i) => {
          const r = ((i + 1) / rings) * (size * 0.36);
          const opacity = 0.5 - i * 0.09;
          return (
            <circle
              key={`r${i}`}
              cx={cx}
              cy={cy}
              r={r}
              fill="none"
              stroke="#72C2AC"
              strokeOpacity={opacity}
              strokeWidth="0.8"
              style={{
                animation: `milestone-ring-pulse 4.5s ease-in-out ${i * 0.35}s infinite`,
                transformOrigin: `${cx}px ${cy}px`,
              }}
            />
          );
        })}
        {/* Centre glow */}
        <circle cx={cx} cy={cy} r="6" fill="#C4A67A" opacity="0.85" />
        <circle cx={cx} cy={cy} r="14" fill="none" stroke="#C4A67A" strokeOpacity="0.35" strokeWidth="0.6" />
      </svg>
      {/* Keyframes are declared inline once (safe — component is
          rendered only when celebrating a milestone). */}
      <style>{`
        @keyframes milestone-ring-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.06); opacity: 0.75; }
        }
      `}</style>
    </div>
  );
}


function _fmtDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' });
  } catch {
    return '';
  }
}
