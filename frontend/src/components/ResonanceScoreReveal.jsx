import React, { useEffect, useState } from 'react';
import { Sparkles, ArrowRight, Waves } from 'lucide-react';

/**
 * Client-side Resonance Score computation — matches the backend cosine
 * similarity so users see the same number the server will persist. Both
 * spectra are log-frequency-binned so a small variance in exact peak
 * frequencies doesn't wreck the score.
 */
export function computeResonanceScore(currentSpectrum, eigenSpectrum) {
  if (!currentSpectrum?.length || !eigenSpectrum?.length) return 100;
  const NBINS = 48;
  const LO = 20;
  const HI = 20000;
  const logRange = Math.log(HI / LO);
  const toBins = (arr) => {
    const out = new Array(NBINS).fill(0);
    const counts = new Array(NBINS).fill(0);
    for (const point of arr) {
      let f; let m;
      if (Array.isArray(point) && point.length >= 2) { f = Number(point[0]); m = Number(point[1]); }
      else if (point && typeof point === 'object') {
        f = Number(point.freq ?? point.frequency ?? 0);
        m = Number(point.mag ?? point.magnitude ?? 0);
      } else continue;
      if (!isFinite(f) || !isFinite(m) || f < LO || f > HI) continue;
      const idx = Math.min(NBINS - 1, Math.max(0, Math.floor(NBINS * Math.log(f / LO) / logRange)));
      out[idx] += m;
      counts[idx] += 1;
    }
    for (let i = 0; i < NBINS; i += 1) if (counts[i]) out[i] /= counts[i];
    return out;
  };
  const a = toBins(currentSpectrum);
  const b = toBins(eigenSpectrum);
  let dot = 0; let na = 0; let nb = 0;
  for (let i = 0; i < NBINS; i += 1) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (na <= 0 || nb <= 0) return 100;
  const cos = Math.max(0, Math.min(1, dot / Math.sqrt(na * nb)));
  return Math.round(cos * 100);
}

// Contextual copy — deliberately non-clinical.
function messageForScore(score) {
  if (score >= 90) return 'You are beautifully aligned with your natural tuning today.';
  if (score >= 70) return "Minor drift detected — today's session will help restore your resonance.";
  if (score >= 50) return 'Moderate drift from your baseline. Your Eigenmode Journey has been personalised to help guide you back.';
  return 'Significant drift detected. This is a great day for a longer, restorative session.';
}

function accentForScore(score) {
  if (score >= 90) return { ring: '#72C2AC', text: '#E8B872', chip: 'BEAUTIFULLY ALIGNED' };
  if (score >= 70) return { ring: '#C4A67A', text: '#E8B872', chip: 'MINOR DRIFT' };
  if (score >= 50) return { ring: '#B79FE8', text: '#E8B872', chip: 'MODERATE DRIFT' };
  return { ring: '#F0B4A8', text: '#F0B4A8', chip: 'SIGNIFICANT DRIFT' };
}

/**
 * Full-panel reveal shown between the FFT analysis step and the Review-
 * Findings step. A circular SVG ring animates from 0 to the score value on
 * mount so users experience the score as a soft arrival, not a stamp.
 */
export default function ResonanceScoreReveal({ score, onContinue, hasBaseline = true }) {
  const target = Math.max(0, Math.min(100, Number.isFinite(score) ? score : 100));
  const [displayed, setDisplayed] = useState(0);
  const accent = accentForScore(target);
  const msg = messageForScore(target);

  // Animate 0 → target across ~1.8s with an easeOut curve.
  useEffect(() => {
    let cancelled = false;
    const start = performance.now();
    const duration = 1800;
    const tick = (now) => {
      if (cancelled) return;
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      setDisplayed(Math.round(eased * target));
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    return () => { cancelled = true; };
  }, [target]);

  // SVG geometry — 220px viewBox, 100px radius, 12px stroke.
  const R = 100;
  const CIRC = 2 * Math.PI * R;
  const dashOffset = CIRC * (1 - displayed / 100);

  return (
    <div
      data-testid="resonance-score-reveal"
      className="flex flex-col items-center justify-center py-6 px-4"
    >
      <div className="text-[10px] uppercase tracking-[0.2em] text-[#5A6B65] mb-2 flex items-center gap-1.5">
        <Waves size={11} /> Resonance Score
      </div>
      <div className="relative w-[220px] h-[220px]">
        <svg viewBox="0 0 220 220" className="w-full h-full">
          <defs>
            <linearGradient id="score-ring" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor={accent.ring} stopOpacity="0.95" />
              <stop offset="100%" stopColor="#E8B872" stopOpacity="0.75" />
            </linearGradient>
          </defs>
          {/* Background ring — sage at 25% opacity */}
          <circle cx="110" cy="110" r={R} fill="none" stroke="#5C9E8C" strokeOpacity="0.15" strokeWidth="12" />
          {/* Progress ring — dashOffset drives the fill */}
          <circle
            cx="110" cy="110" r={R} fill="none"
            stroke="url(#score-ring)" strokeWidth="12"
            strokeDasharray={CIRC}
            strokeDashoffset={dashOffset}
            strokeLinecap="round"
            transform="rotate(-90 110 110)"
            style={{ transition: 'stroke-dashoffset 40ms linear' }}
          />
        </svg>
        {/* Number + label overlay */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div
            data-testid="resonance-score-number"
            className="text-[64px] leading-none font-light tabular-nums"
            style={{ fontFamily: 'Cormorant Garamond, serif', color: accent.text }}
          >
            {displayed}
          </div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-[#8A9A92] mt-1">of 100</div>
        </div>
      </div>
      <div
        data-testid="resonance-score-chip"
        className="mt-4 inline-flex items-center gap-1.5 rounded-full border px-3 py-1"
        style={{ color: accent.ring, borderColor: `${accent.ring}55`, backgroundColor: `${accent.ring}12` }}
      >
        <Sparkles size={11} />
        <span className="text-[10px] uppercase tracking-[0.16em]">{accent.chip}</span>
      </div>
      <p
        data-testid="resonance-score-message"
        className="mt-5 text-center text-sm text-[#C9DED6] leading-relaxed max-w-md px-2"
      >
        {msg}
      </p>
      {!hasBaseline && (
        <p className="mt-2 text-center text-xs text-[#5A6B65] italic max-w-md px-2">
          This capture is now your Eigenmode baseline — the score will start comparing you against yourself from your next scan.
        </p>
      )}
      <button
        type="button"
        data-testid="resonance-score-continue"
        onClick={onContinue}
        className="mt-6 inline-flex items-center gap-2 px-5 py-2.5 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium text-sm transition-colors"
      >
        See your findings <ArrowRight size={13} />
      </button>
    </div>
  );
}
