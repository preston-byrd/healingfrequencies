import React from 'react';
import { Heart, X } from 'lucide-react';

/**
 * WellnessCheckinCard — soft post-session prompt that appears once the
 * 5-minute Wellness-Assistant-triggered session has fully faded to silence.
 * It gives the user a graceful choice: extend the session by 10 minutes, or
 * close the assistant flow and go on with their day.
 *
 * The card is intentionally minimal (no shadow drops, no jarring animation)
 * so it mirrors the calm tone of the rest of the app. It slides up from
 * behind the visualiser and dims the bottom transport so the user's focus
 * is on the question, not on the timer widget.
 *
 * Props:
 *   open       — bool; render only when true.
 *   onContinue — () => void, adds 10 minutes to the session and restarts
 *                playback with the same tone the user was hearing.
 *   onDone     — () => void, closes the card gracefully. No audio change.
 */
export default function WellnessCheckinCard({ open, onContinue, onDone }) {
  if (!open) return null;
  return (
    <div
      className="absolute inset-0 z-30 flex items-center justify-center p-6 pointer-events-none"
      data-testid="wellness-checkin"
    >
      <div className="glass-soft border border-[#5C9E8C]/30 rounded-2xl px-6 py-6 max-w-sm w-full text-center pointer-events-auto relative"
        style={{ backdropFilter: 'blur(16px)' }}
      >
        <button
          data-testid="wellness-checkin-dismiss"
          onClick={onDone}
          aria-label="Close"
          className="absolute top-3 right-3 text-[#5A6B65] hover:text-[#C9DED6] transition-colors"
        >
          <X size={14} />
        </button>
        <div className="flex justify-center mb-3">
          <div className="w-10 h-10 rounded-full bg-[#5C9E8C]/15 flex items-center justify-center">
            <Heart size={16} className="text-[#72C2AC]" />
          </div>
        </div>
        <div className="text-[15px] text-[#E8E3D9] font-medium mb-1 leading-relaxed">
          How are you feeling now?
        </div>
        <div className="text-[11px] text-[#8A9A92] mb-5 leading-relaxed">
          Your session just wrapped. Take a breath and check in with yourself.
        </div>
        <div className="flex flex-col gap-2">
          <button
            data-testid="wellness-checkin-continue"
            onClick={onContinue}
            className="w-full py-2.5 rounded-lg bg-[#5C9E8C]/25 hover:bg-[#5C9E8C]/40 border border-[#72C2AC]/50 hover:border-[#72C2AC] text-[#72C2AC] text-sm font-medium tracking-wide transition-colors"
          >
            Continue · +10 min
          </button>
          <button
            data-testid="wellness-checkin-done"
            onClick={onDone}
            className="w-full py-2.5 rounded-lg bg-black/25 hover:bg-black/40 border border-[#5C9E8C]/20 hover:border-[#5C9E8C]/40 text-[#C9DED6] text-sm tracking-wide transition-colors"
          >
            I&rsquo;m good, thank you
          </button>
        </div>
      </div>
    </div>
  );
}
