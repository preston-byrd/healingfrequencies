import React, { useEffect, useState } from 'react';
import { X, AlertTriangle, Loader2 } from 'lucide-react';
import api from '@/lib/api';

/**
 * CancelSubscriptionModal — HF-049 exit survey shown when a Pro/trial user
 * clicks "cancel subscription" from AccountDashboard. Blocks accidental
 * cancellations (no bare `window.confirm`) and captures a structured
 * reason so the Admin > Cancellations tab can show top-reason analytics.
 *
 * The reason list comes from `GET /me/cancellation-reasons` (single source
 * of truth with the backend's CANCELLATION_REASONS catalog). If the fetch
 * fails, a small hardcoded fallback keeps the modal usable.
 */

const FALLBACK_REASONS = [
  { key: 'too_expensive',    label: 'Too expensive' },
  { key: 'missing_features', label: 'Missing features' },
  { key: 'just_testing',     label: 'Just testing it out' },
  { key: 'not_using',        label: 'Not using it enough' },
  { key: 'technical_issues', label: 'Technical issues' },
  { key: 'found_alternative', label: 'Found an alternative' },
  { key: 'other',            label: 'Other' },
];

export default function CancelSubscriptionModal({ open, onClose, onCancelled }) {
  const [reasons, setReasons] = useState(FALLBACK_REASONS);
  const [reasonKey, setReasonKey] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!open) return;
    setReasonKey('');
    setNote('');
    setErr('');
    setBusy(false);
    (async () => {
      try {
        const { data } = await api.get('/me/cancellation-reasons');
        if (data?.reasons?.length) setReasons(data.reasons);
      } catch (_) { /* fallback list already loaded */ }
    })();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const h = (e) => { if (e.key === 'Escape' && !busy) onClose && onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, busy, onClose]);

  if (!open) return null;

  const submit = async () => {
    if (!reasonKey) {
      setErr('Please choose a reason so we can improve.');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      await api.post('/me/cancel-subscription', {
        reason_key: reasonKey,
        reason_note: note.trim().slice(0, 500) || null,
      });
      onCancelled && onCancelled();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      setErr(typeof detail === 'string' ? detail : 'Cancellation failed. Please try again.');
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end sm:items-center justify-center p-3 sm:p-4 bg-black/70 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      data-testid="cancel-subscription-modal"
      onClick={() => !busy && onClose && onClose()}
    >
      <div
        className="relative w-full sm:max-w-md rounded-2xl border border-[#C4A67A]/30 bg-[#0B1814] shadow-[0_20px_60px_rgba(0,0,0,0.5)] p-5 sm:p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          data-testid="cancel-subscription-close"
          onClick={() => !busy && onClose && onClose()}
          aria-label="Close"
          className="absolute top-3 right-3 text-[#8A9A92] hover:text-[#E8E3D9] transition-colors p-1"
        >
          <X size={16} />
        </button>

        <div className="text-center mb-5">
          <div className="flex justify-center mb-2">
            <div className="w-9 h-9 rounded-full bg-[#C4A67A]/15 flex items-center justify-center">
              <AlertTriangle size={16} className="text-[#C4A67A]" strokeWidth={1.75} />
            </div>
          </div>
          <div className="text-[11px] uppercase tracking-[0.2em] text-[#C4A67A] font-mono mb-2">
            Cancel Subscription
          </div>
          <div className="font-display text-[22px] text-[#E8E3D9] leading-tight">
            Why are you leaving?
          </div>
          <div className="text-[12px] text-[#8A9A92] mt-1.5 leading-relaxed">
            You&apos;ll keep Pro access until the end of your current period.
            Your answer helps us improve — it stays with our small team.
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 mb-3" data-testid="cancel-reason-list">
          {reasons.map((r) => (
            <button
              key={r.key}
              type="button"
              data-testid={`cancel-reason-${r.key}`}
              onClick={() => setReasonKey(r.key)}
              className={`text-left py-2.5 px-3 rounded-lg border text-[13px] tracking-wide transition-colors ${
                reasonKey === r.key
                  ? 'bg-[#5C9E8C]/25 border-[#72C2AC] text-[#E8E3D9]'
                  : 'bg-black/25 border-[#5C9E8C]/20 hover:border-[#72C2AC]/50 text-[#C9DED6] hover:text-[#E8E3D9]'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>

        <div className="mb-3">
          <label className="block text-[10px] uppercase tracking-widest text-[#8A9A92] mb-1.5">
            Anything else? (optional)
          </label>
          <textarea
            data-testid="cancel-reason-note"
            value={note}
            onChange={(e) => setNote(e.target.value.slice(0, 500))}
            placeholder="Optional details — feature we&apos;re missing, moment that lost you, etc."
            rows={3}
            maxLength={500}
            className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none resize-none transition-colors"
          />
          <div className="text-right text-[10px] text-[#5A6B65] mt-1">{note.length}/500</div>
        </div>

        {err && (
          <div
            className="text-[11px] text-[#D96C6C] italic mb-2"
            data-testid="cancel-modal-error"
          >
            {err}
          </div>
        )}

        <div className="flex flex-col sm:flex-row gap-2 pt-1">
          <button
            type="button"
            data-testid="cancel-modal-keep"
            onClick={() => !busy && onClose && onClose()}
            disabled={busy}
            className="flex-1 py-2.5 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium text-sm transition-colors disabled:opacity-50"
          >
            Keep my subscription
          </button>
          <button
            type="button"
            data-testid="cancel-modal-confirm"
            onClick={submit}
            disabled={busy}
            className="flex-1 py-2.5 rounded-full border border-[#8A9A92]/40 hover:border-[#C4A67A] text-[#8A9A92] hover:text-[#C4A67A] text-sm transition-colors disabled:opacity-50 inline-flex items-center justify-center gap-2"
          >
            {busy
              ? <><Loader2 size={13} className="animate-spin" /> Cancelling…</>
              : 'Confirm cancellation'}
          </button>
        </div>
      </div>
    </div>
  );
}
