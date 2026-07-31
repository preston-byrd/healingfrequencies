import React, { useEffect, useState } from 'react';
import { Bell, X, Loader2 } from 'lucide-react';
import api from '@/lib/api';
import { pushSupported, currentPermission, subscribeToPush } from '@/lib/pushClient';

const STORAGE_KEY = 'solar:push_opt_in_v1';
const SNOOZE_DAYS = 7;
const DECLINED_DAYS = 60;

// Read the last-decision timestamp + verdict from localStorage.
const readState = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch { return null; }
};
const writeState = (verdict) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ verdict, at: Date.now() }));
  } catch { /* private mode */ }
};

// Public helper that other components call after a peak-value moment.
// It's intentionally cheap and idempotent — if it's not the right time to
// prompt, this returns false and the caller carries on.
export function requestPushOptInIfWarm() {
  if (!pushSupported()) return false;
  if (currentPermission() !== 'default') return false; // granted OR denied — done
  const state = readState();
  if (state) {
    const daysAgo = (Date.now() - Number(state.at || 0)) / (1000 * 60 * 60 * 24);
    if (state.verdict === 'later' && daysAgo < SNOOZE_DAYS) return false;
    if (state.verdict === 'declined' && daysAgo < DECLINED_DAYS) return false;
    if (state.verdict === 'enabled') return false;
  }
  try {
    window.dispatchEvent(new CustomEvent('sf:push-opt-in:show'));
  } catch { /* IE11 shrug — irrelevant */ }
  return true;
}

/**
 * Small slip-up card, mounted once at the Dashboard root. Listens for
 * `sf:push-opt-in:show` custom events and slides in from the bottom-right.
 * Non-blocking, easy to dismiss, and never re-appears for at least a week
 * after "Later" (or 60 days after "No thanks").
 */
export default function PushOptInSlip() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errorText, setErrorText] = useState('');

  useEffect(() => {
    const onShow = () => { setErrorText(''); setOpen(true); };
    window.addEventListener('sf:push-opt-in:show', onShow);
    return () => window.removeEventListener('sf:push-opt-in:show', onShow);
  }, []);

  if (!open) return null;

  const dismiss = (verdict) => {
    writeState(verdict);
    setOpen(false);
  };

  const enable = async () => {
    setErrorText('');
    setBusy(true);
    try {
      const res = await subscribeToPush();
      if (!res.ok) {
        if (res.reason === 'denied' || res.reason === 'NotAllowedError') {
          setErrorText('Your browser blocked notifications. You can enable them in your browser settings.');
          writeState('declined');
        } else if (res.reason === 'unsupported' || res.reason === 'no_vapid_key') {
          setErrorText('Not available on this device — in-app notifications still work.');
          writeState('declined');
        } else {
          setErrorText('We couldn\'t enable notifications right now.');
        }
        return;
      }
      // Best-effort — server flipped push_enabled inside subscribeToPush.
      try { await api.put('/me/notifications/prefs', { push_enabled: true }); } catch { /* graceful */ }
      writeState('enabled');
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="push-opt-in-slip"
      className="fixed z-[80] bottom-6 right-6 left-6 sm:left-auto sm:w-[360px] bg-[#0A1612] border border-[#5C9E8C]/45 rounded-2xl shadow-[0_18px_40px_-6px_rgba(0,0,0,0.85)] p-5 animate-in fade-in slide-in-from-bottom-4 duration-300"
      role="dialog"
      aria-live="polite"
    >
      <button
        type="button"
        data-testid="push-opt-in-close"
        onClick={() => dismiss('later')}
        aria-label="Close"
        className="absolute top-2.5 right-2.5 text-[#5A6B65] hover:text-[#F0B4A8] transition-colors p-1"
      >
        <X size={14} />
      </button>
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-full bg-[#72C2AC]/15 flex items-center justify-center shrink-0">
          <Bell size={18} className="text-[#72C2AC]" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-[0.2em] text-[#5A6B65] mb-1">Optional</div>
          <div className="text-[15px] text-[#E8E3D9] leading-snug" style={{ fontFamily: 'Cormorant Garamond, serif' }}>
            Get quiet nudges from Solarisound?
          </div>
          <div className="text-xs text-[#8A9A92] mt-1.5 leading-relaxed">
            Warm, supportive check-ins and personalised suggestions — no marketing, no urgency. Off by default.
          </div>
          {errorText && (
            <div className="text-[11px] text-[#F0B4A8] mt-2 leading-snug">{errorText}</div>
          )}
          <div className="flex items-center gap-2 mt-4">
            <button
              type="button"
              data-testid="push-opt-in-enable"
              onClick={enable}
              disabled={busy}
              className="text-[11px] uppercase tracking-[0.14em] px-3.5 py-2 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium transition-colors flex items-center gap-1.5 disabled:opacity-60"
            >
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Bell size={12} />} Enable
            </button>
            <button
              type="button"
              data-testid="push-opt-in-later"
              onClick={() => dismiss('later')}
              className="text-[11px] uppercase tracking-[0.14em] px-3.5 py-2 rounded-full border border-[#5C9E8C]/40 hover:border-[#72C2AC]/70 text-[#C9DED6] transition-colors"
            >
              Later
            </button>
            <button
              type="button"
              data-testid="push-opt-in-decline"
              onClick={() => dismiss('declined')}
              className="ml-auto text-[10px] uppercase tracking-[0.14em] text-[#5A6B65] hover:text-[#C4A67A] transition-colors"
            >
              No thanks
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
