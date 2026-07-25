import React from 'react';
import { Lock, X } from 'lucide-react';

/**
 * SessionUnlockCard — soft upgrade prompt that surfaces when a non-Pro user
 * taps one of their saved sessions. Tone is reassuring, never alarming:
 * their data is safe and always was — the upgrade is framed as regaining
 * access, not recovering anything at risk of loss.
 *
 * Styling deliberately mirrors WellnessCheckinCard (dark glass card, sage
 * accent stroke, drop shadow, 5C9E8C palette) so free users experience the
 * same calm design language on both post-session and re-access flows.
 *
 * Props:
 *   open        — bool; render only when true.
 *   onUpgrade   — () => void, opens the paywall / Account subscription tile.
 *   onDismiss   — () => void, closes the card. No mutation.
 */
export default function SessionUnlockCard({ open, onUpgrade, onDismiss }) {
  if (!open) return null;
  return (
    <div
      className="absolute inset-0 z-40 flex items-center justify-center p-6 pointer-events-none"
      data-testid="session-unlock"
    >
      <div
        className="glass-soft border border-[#5C9E8C]/30 rounded-2xl px-6 py-6 max-w-sm w-full text-center pointer-events-auto relative"
        style={{ backdropFilter: 'blur(16px)' }}
      >
        <button
          data-testid="session-unlock-dismiss"
          onClick={onDismiss}
          aria-label="Close"
          className="absolute top-3 right-3 text-[#5A6B65] hover:text-[#C9DED6] transition-colors"
        >
          <X size={14} />
        </button>
        <div className="flex justify-center mb-3">
          <div className="w-10 h-10 rounded-full bg-[#5C9E8C]/15 flex items-center justify-center">
            <Lock size={16} className="text-[#72C2AC]" />
          </div>
        </div>
        <div className="text-[15px] text-[#E8E3D9] font-medium mb-1 leading-relaxed">
          Your sessions are safely stored.
        </div>
        <div className="text-[13px] text-[#C9DED6] mb-5 leading-relaxed">
          Unlock Pro to access them.
        </div>
        <div className="flex flex-col gap-2">
          <button
            data-testid="session-unlock-upgrade"
            onClick={onUpgrade}
            className="w-full py-2.5 rounded-lg bg-[#5C9E8C]/25 hover:bg-[#5C9E8C]/40 border border-[#72C2AC]/50 hover:border-[#72C2AC] text-[#72C2AC] text-sm font-medium tracking-wide transition-colors"
          >
            Start Pro
          </button>
          <button
            data-testid="session-unlock-dismiss-btn"
            onClick={onDismiss}
            className="w-full py-2.5 rounded-lg bg-black/25 hover:bg-black/40 border border-[#5C9E8C]/20 hover:border-[#5C9E8C]/40 text-[#C9DED6] text-sm tracking-wide transition-colors"
          >
            Not now
          </button>
        </div>
      </div>
    </div>
  );
}
