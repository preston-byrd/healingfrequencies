import React, { useCallback, useEffect, useState } from 'react';
import { X, Sparkles, Flame } from 'lucide-react';
import api from '@/lib/api';

/**
 * HF-042 Weekly Alignment Streak — milestone celebration card.
 *
 * Polls `/me/alignment-streak` on mount + on the `sf:alignment:capture`
 * event (fired by HarmonicBlueprintSheet after a successful save). When
 * the backend surfaces a `new_milestone`, we render the celebration
 * card until the user dismisses it — at which point we POST /ack so it
 * doesn't re-appear.
 *
 * Rendered as a compact glass card near the top of the dashboard so
 * it lands in-view immediately after the celebratory HB save flow.
 */

const COPY = {
  4: {
    title: 'One month of weekly alignment',
    body: 'Your system is finding its rhythm.',
  },
  8: {
    title: 'Two months of alignment',
    body: 'A steadier resonance is settling in.',
  },
};

export default function AlignmentStreakMilestone() {
  const [milestone, setMilestone] = useState(null);
  const [streak, setStreak] = useState(0);
  const [dismissing, setDismissing] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/me/alignment-streak');
      if (data?.new_milestone) {
        setMilestone(data.new_milestone);
        setStreak(data.streak || 0);
      }
    } catch (_) { /* graceful */ }
  }, []);

  useEffect(() => {
    load();
    const onCapture = () => load();
    window.addEventListener('sf:alignment:capture', onCapture);
    return () => window.removeEventListener('sf:alignment:capture', onCapture);
  }, [load]);

  const dismiss = useCallback(async () => {
    if (!milestone) return;
    setDismissing(true);
    try {
      await api.post('/me/alignment-streak/ack', { milestone });
    } catch (_) { /* graceful */ }
    setMilestone(null);
    setDismissing(false);
  }, [milestone]);

  if (!milestone) return null;

  const copy = COPY[milestone] || {
    title: `${milestone} weeks of weekly alignment`,
    body: 'Your consistency is compounding.',
  };

  return (
    <div
      data-testid={`alignment-streak-milestone-${milestone}`}
      className="glass p-5 border border-[#C4A67A]/40 relative overflow-hidden"
    >
      {/* Subtle glow flourish so the card feels earned, not clinical. */}
      <div
        className="absolute -inset-x-4 -top-4 h-24 pointer-events-none opacity-40"
        style={{
          background: 'radial-gradient(circle at 20% 0%, rgba(196,166,122,0.35), transparent 60%)',
        }}
      />
      <div className="relative flex items-start gap-4">
        <div className="w-10 h-10 rounded-full bg-[#C4A67A]/15 border border-[#C4A67A]/40 flex items-center justify-center flex-shrink-0">
          <Sparkles size={16} className="text-[#C4A67A]" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="label-tiny text-[#C4A67A] mb-1 flex items-center gap-1.5">
            <Flame size={11} /> Milestone · {streak}-week streak
          </div>
          <div className="text-[15px] text-[#E8E3D9] leading-snug font-display">
            {copy.title}
          </div>
          <div className="text-[13px] text-[#8A9A92] leading-snug mt-1">
            {copy.body}
          </div>
        </div>
        <button
          type="button"
          onClick={dismiss}
          disabled={dismissing}
          aria-label="Dismiss milestone celebration"
          data-testid="alignment-streak-milestone-dismiss"
          className="flex-shrink-0 text-[#8A9A92] hover:text-[#E8E3D9] transition-colors p-1 disabled:opacity-40"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
