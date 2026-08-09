import React, { useMemo, useState, useEffect } from 'react';
import { Heart, X, Sparkles, Loader2, Share2 } from 'lucide-react';
import api from '@/lib/api';

/**
 * WellnessCheckinCard — soft post-session prompt shown once the Smart Fade
 * Timer has fully faded the last session to silence.
 *
 * TWO-STEP FLOW (added in Phase 6):
 *   Step 1 — "How are you feeling now?"  → Continue +10 min | I'm good, thanks
 *   Step 2 — Rotating reflection question (only when the user picked "I'm
 *            good, thanks", so extended sessions aren't interrupted).
 *
 * Step 2 posts to /me/journey/{entry_id}/reflection so the Wellness
 * Assistant can use the response (and its server-derived sentiment) to
 * refine future frequency suggestions.
 *
 * Props:
 *   open            — bool
 *   onContinue      — extends session by 10 min. Skips step 2.
 *   onDone          — user is done; if a journey entry id is available and
 *                     no reflection was submitted, closes silently.
 *   journeyEntryId  — id returned from POST /me/journey/log for the session
 *                     that just ended. When present, enables step 2.
 *   onShare         — optional; when provided, renders a soft "Share your
 *                     session" button below the two primary actions on
 *                     step 1. Never a popup, always dismissable.
 */

const REFLECTION_QUESTIONS = [
  'Did that feel like the right frequency for today?',
  'Did you notice any shift during the session?',
  'Was there a moment that felt particularly resonant?',
  "Anything you'd change about that session?",
  "How's your body feeling right now?",
  'Would you come back to this one?',
];

function pickQuestion() {
  return REFLECTION_QUESTIONS[Math.floor(Math.random() * REFLECTION_QUESTIONS.length)];
}

export default function WellnessCheckinCard({ open, onContinue, onDone, journeyEntryId, onShare }) {
  const [step, setStep] = useState(1);
  const [response, setResponse] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const question = useMemo(pickQuestion, [open, journeyEntryId]);

  // Reset local state every time the card is (re)opened.
  useEffect(() => {
    if (open) {
      setStep(1);
      setResponse('');
      setSaving(false);
      setErr('');
    }
  }, [open, journeyEntryId]);

  if (!open) return null;

  const handleFinishStep1 = () => {
    // Only surface the reflection step when we have an entry to attach it
    // to. Cold-start (very short session, journey log rejected) → close.
    if (journeyEntryId) {
      setStep(2);
    } else {
      onDone && onDone();
    }
  };

  const submitReflection = async () => {
    const txt = response.trim();
    if (!txt || !journeyEntryId) {
      onDone && onDone();
      return;
    }
    setSaving(true);
    setErr('');
    try {
      await api.post(`/me/journey/${journeyEntryId}/reflection`, {
        question,
        response: txt.slice(0, 500),
      });
      onDone && onDone();
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not save your reflection');
      setSaving(false);
    }
  };

  return (
    <div
      className="absolute inset-0 z-30 flex items-center justify-center p-6 pointer-events-none"
      data-testid="wellness-checkin"
    >
      <div
        className="glass-soft border border-[#5C9E8C]/30 rounded-2xl px-6 py-6 max-w-sm w-full text-center pointer-events-auto relative"
        style={{ backdropFilter: 'blur(16px)' }}
        data-testid={step === 1 ? 'wellness-checkin-step1' : 'wellness-checkin-step2'}
      >
        <button
          data-testid="wellness-checkin-dismiss"
          onClick={onDone}
          aria-label="Close"
          className="absolute top-3 right-3 text-[#5A6B65] hover:text-[#C9DED6] transition-colors"
        >
          <X size={14} />
        </button>

        {step === 1 && (
          <>
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
                onClick={handleFinishStep1}
                className="w-full py-2.5 rounded-lg bg-black/25 hover:bg-black/40 border border-[#5C9E8C]/20 hover:border-[#5C9E8C]/40 text-[#C9DED6] text-sm tracking-wide transition-colors"
              >
                I&rsquo;m good, thank you
              </button>
              {onShare && (
                <button
                  data-testid="wellness-checkin-share"
                  onClick={onShare}
                  className="w-full mt-1 py-2 rounded-lg text-[#C4A67A] hover:text-[#E8B872] text-xs tracking-wide transition-colors inline-flex items-center justify-center gap-1.5"
                >
                  <Share2 size={12} />
                  Share your session
                </button>
              )}
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <div className="flex justify-center mb-3">
              <div className="w-10 h-10 rounded-full bg-[#C4A67A]/15 flex items-center justify-center">
                <Sparkles size={16} className="text-[#C4A67A]" />
              </div>
            </div>
            <div
              className="text-[15px] text-[#E8E3D9] font-medium mb-3 leading-relaxed"
              data-testid="wellness-checkin-question"
            >
              {question}
            </div>
            <textarea
              data-testid="wellness-checkin-response"
              value={response}
              onChange={(e) => setResponse(e.target.value.slice(0, 500))}
              placeholder="A few words is plenty…"
              rows={3}
              autoFocus
              className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none resize-none transition-colors"
            />
            <div className="flex items-center justify-between text-[10px] text-[#5A6B65] mt-1 mb-4">
              <span>Your Wellness Assistant will remember this.</span>
              <span>{response.length}/500</span>
            </div>
            {err && (
              <div className="text-[11px] text-[#C4A67A]/90 italic mb-3" data-testid="wellness-checkin-error">{err}</div>
            )}
            <div className="flex flex-col gap-2">
              <button
                data-testid="wellness-checkin-save"
                onClick={submitReflection}
                disabled={saving || !response.trim()}
                className="w-full py-2.5 rounded-lg bg-[#C4A67A]/25 hover:bg-[#C4A67A]/40 disabled:opacity-40 disabled:cursor-not-allowed border border-[#C4A67A]/50 hover:border-[#C4A67A] text-[#C4A67A] text-sm font-medium tracking-wide transition-colors inline-flex items-center justify-center gap-2"
              >
                {saving ? <><Loader2 size={13} className="animate-spin" />Saving…</> : 'Save reflection'}
              </button>
              <button
                data-testid="wellness-checkin-skip"
                onClick={onDone}
                disabled={saving}
                className="w-full py-2 rounded-lg text-[#8A9A92] hover:text-[#C9DED6] text-xs tracking-wide transition-colors"
              >
                Skip
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
