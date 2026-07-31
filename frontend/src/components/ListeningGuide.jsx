import React, { useEffect, useState } from 'react';
import { X, Headphones, VolumeX, Volume2, Moon, Info, Wifi } from 'lucide-react';

/**
 * Small, dismissible "For best results" listening guide accessible from the
 * player. Renders as a soft glass card overlay (not a hard modal), so it
 * never blocks playback. Also used as the copy source for the in-session
 * headphone reminder toast.
 *
 * Content is deliberately non-clinical: recommendations, not prescriptions.
 */
export default function ListeningGuide({ open, onClose }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose && onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      data-testid="listening-guide-modal"
      className="fixed inset-0 z-[95] flex items-end sm:items-center justify-center px-3 sm:px-6 bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md bg-[#0A1612] border border-[#5C9E8C]/40 rounded-t-2xl sm:rounded-2xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 pt-6 pb-2 flex items-start justify-between">
          <div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-[#5A6B65] mb-1">
              A gentle guide
            </div>
            <h3 className="text-2xl text-[#E8E3D9] leading-tight" style={{ fontFamily: 'Cormorant Garamond, serif' }}>
              For best results
            </h3>
          </div>
          <button
            type="button"
            data-testid="listening-guide-close"
            onClick={onClose}
            aria-label="Close"
            className="text-[#8A9A92] hover:text-[#F0B4A8] transition-colors -mt-1 -mr-1 p-1"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          <GuideRow
            icon={<Headphones size={16} className="text-[#72C2AC]" />}
            title="Quality wired headphones"
            body="Wired over-ear or in-ear headphones preserve the exact frequencies. Bluetooth adds latency and compression that softens the finer harmonic detail."
          />
          <GuideRow
            icon={<Volume2 size={16} className="text-[#C4A67A]" />}
            title="Moderate volume"
            body="Loud enough to feel present, gentle enough to disappear. If the tones start to feel tiring, they're a touch too loud."
          />
          <GuideRow
            icon={<Moon size={16} className="text-[#B79FE8]" />}
            title="A quiet environment"
            body="Every ambient sound the room adds — traffic, fans, chatter — masks a little of the tone. Even a soft, familiar room helps."
          />
          <GuideRow
            icon={<Wifi size={16} className="text-[#A9B7D2]" />}
            title="Airplane mode is beautiful"
            body="Notifications break the thread of attention. If you can, let this be uninterrupted time."
          />
          <div className="text-[11px] text-[#5A6B65] italic pt-2">
            These are suggestions, not requirements. Solarisound still works beautifully without any of them.
          </div>
        </div>

        <div className="px-6 py-3 border-t border-[#5C9E8C]/20 flex items-center justify-end">
          <button
            type="button"
            data-testid="listening-guide-ok"
            onClick={onClose}
            className="text-[11px] uppercase tracking-[0.14em] px-4 py-2 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium transition-colors"
          >
            I'm ready to begin
          </button>
        </div>
      </div>
    </div>
  );
}

function GuideRow({ icon, title, body }) {
  return (
    <div className="flex items-start gap-3">
      <div className="w-8 h-8 rounded-full bg-black/25 border border-[#5C9E8C]/25 flex items-center justify-center shrink-0 mt-0.5">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-[#E8E3D9] leading-tight">{title}</div>
        <div className="text-xs text-[#8A9A92] mt-1 leading-relaxed">{body}</div>
      </div>
    </div>
  );
}

/**
 * Compact one-line headphone reminder that surfaces briefly at session start
 * when a fidelity-sensitive mode is active (binaural, isochronic, Golden
 * Stack). Snoozes for 24h on dismiss so it doesn't nag every session.
 *
 * `reason` is one of: 'binaural' | 'isochronic' | 'golden_stack' — used to
 * tune the copy so it reads honest instead of generic.
 */
const REMINDER_KEY = 'solar:headphone_reminder_v1';
const REMINDER_SNOOZE_MS = 24 * 60 * 60 * 1000; // 24h

export function HeadphoneReminder({ visible, reason, onDismiss }) {
  if (!visible) return null;
  const line = (
    reason === 'binaural'  ? 'Binaural offset comes alive with quality wired headphones — each ear gets a slightly different tone.'
    : reason === 'isochronic' ? 'Isochronic pulses come through clearly on any speaker — but wired headphones sharpen the entrainment.'
    : reason === 'golden_stack' ? 'Golden Stack layers three harmonics. Wired headphones let you hear each one clearly.'
    : 'For the fullest fidelity, quality wired headphones are recommended.'
  );
  return (
    <div
      data-testid="headphone-reminder"
      data-reason={reason || 'general'}
      className="fixed left-3 right-3 bottom-6 sm:left-auto sm:right-6 sm:w-[380px] z-[70] bg-[#0A1612] border border-[#5C9E8C]/40 rounded-xl shadow-[0_18px_40px_-8px_rgba(0,0,0,0.85)] p-4 animate-in fade-in slide-in-from-bottom-4 duration-300"
      role="status"
    >
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-full bg-[#72C2AC]/15 flex items-center justify-center shrink-0">
          <Headphones size={16} className="text-[#72C2AC]" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-[0.2em] text-[#5A6B65] mb-1">A gentle tip</div>
          <div className="text-xs text-[#E8E3D9] leading-snug">{line}</div>
        </div>
        <button
          type="button"
          data-testid="headphone-reminder-dismiss"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="text-[#5A6B65] hover:text-[#F0B4A8] transition-colors p-1 -mt-1 -mr-1 shrink-0"
        >
          <X size={13} />
        </button>
      </div>
    </div>
  );
}

// Hook that returns { visible, reason, dismiss } for a given session context.
// The reminder is shown once per browser-day per user, in the order:
// binaural → isochronic → golden_stack (only one at a time). If the user
// dismisses it, we snooze for 24h regardless of which reason triggered.
export function useHeadphoneReminder({ playing, binaural, isochronic, goldenStack }) {
  const [visible, setVisible] = useState(false);
  const [reason, setReason] = useState(null);

  const readSnooze = () => {
    try {
      const raw = localStorage.getItem(REMINDER_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch { return null; }
  };
  const writeSnooze = () => {
    try { localStorage.setItem(REMINDER_KEY, JSON.stringify({ at: Date.now() })); } catch (_) {}
  };

  useEffect(() => {
    if (!playing) { setVisible(false); return; }
    // Pick the strongest reason present.
    const r = (binaural > 0)
      ? 'binaural'
      : (isochronic > 0)
        ? 'isochronic'
        : (goldenStack)
          ? 'golden_stack'
          : null;
    if (!r) { setVisible(false); return; }
    const snooze = readSnooze();
    if (snooze && (Date.now() - Number(snooze.at || 0)) < REMINDER_SNOOZE_MS) return;
    setReason(r);
    // Small delay so the reminder appears after the play button flip settles.
    const t = setTimeout(() => setVisible(true), 900);
    // Auto-hide after 12s if the user doesn't dismiss.
    const t2 = setTimeout(() => setVisible(false), 12900);
    return () => { clearTimeout(t); clearTimeout(t2); };
  }, [playing, binaural, isochronic, goldenStack]);

  const dismiss = () => {
    writeSnooze();
    setVisible(false);
  };

  return { visible, reason, dismiss };
}
