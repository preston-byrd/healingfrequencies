import React from 'react';
import { Sparkles, Lock, Check } from 'lucide-react';

/**
 * Compact Dashboard entry point for Harmonic Blueprint. Sits in the left
 * column between Flow Mode and Ambient Layers.
 *
 * Props:
 *   isPro       — bool. When false, the card is Pro-locked (click routes to
 *                 the paywall via onUnlock).
 *   hasProfile  — bool. When true, shows a subtle "Signature captured" chip.
 *   onOpen      — () => void, opens the full-screen sheet.
 *   onUnlock    — () => void, opens the paywall.
 */
export default function HarmonicBlueprintCard({ isPro = false, hasProfile = false, onOpen, onUnlock }) {
  // Phase 3 funnel: free users still click through to the sheet where they
  // can preview a 2-track demo journey. The Pro badge stays as a soft cue.
  const handleClick = () => {
    onOpen && onOpen();
  };
  const locked = !isPro;
  return (
    <button
      data-testid="harmonic-blueprint-card"
      type="button"
      onClick={handleClick}
      className="glass p-5 relative text-left w-full transition-transform duration-200 hover:-translate-y-0.5 hover:shadow-[0_8px_32px_rgba(114,194,172,0.15)]"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Harmonic Blueprint</div>
          {locked && <Lock size={11} className="text-[#C4A67A]" />}
        </div>
        {locked ? (
          <span
            data-testid="harmonic-blueprint-pro-badge"
            className="text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-0.5 rounded-full"
          >
            PRO
          </span>
        ) : hasProfile ? (
          <span
            data-testid="harmonic-blueprint-captured-badge"
            className="text-[9px] tracking-widest text-[#72C2AC] bg-[#72C2AC]/10 px-2 py-0.5 rounded-full inline-flex items-center gap-1"
          >
            <Check size={9} /> Captured
          </span>
        ) : null}
      </div>
      <div className="font-display text-xl text-[#E8E3D9] mt-3 leading-tight">
        {hasProfile ? 'View your resonance profile' : 'Discover your voice signature'}
      </div>
      <div className="text-[#8A9A92] text-sm mt-2 leading-relaxed">
        {hasProfile
          ? 'Review your frequency map, or record a fresh sample to update your baseline.'
          : 'Record a short vocal sample — we\'ll map your natural resonances and reveal where you\'ve drifted.'}
      </div>
      <div className="mt-4 flex items-center justify-end">
        <span className="text-[#72C2AC] text-xs tracking-widest uppercase">
          {locked ? 'Preview' : hasProfile ? 'Open' : 'Begin'} →
        </span>
      </div>
    </button>
  );
}
