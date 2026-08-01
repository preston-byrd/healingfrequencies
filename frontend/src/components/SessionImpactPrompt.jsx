import React, { useEffect, useState, useCallback } from 'react';
import { Sparkles, ArrowRight, X } from 'lucide-react';
import api, { formatApiError } from '@/lib/api';

/**
 * SessionImpactPrompt — a soft, dismissible modal shown when the user opens
 * the app and has any HB-recommended session from ≥ 24h ago without an
 * impact rating yet. Cycles through pending entries one at a time.
 *
 * Non-blocking: dismissing skips the current entry for this session
 * (never re-prompted until page reload). Rating persists on the server.
 */
export default function SessionImpactPrompt({ isPro = true }) {
  const [pending, setPending] = useState([]);
  const [idx, setIdx] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [dismissedIds, setDismissedIds] = useState(() => new Set());

  useEffect(() => {
    if (!isPro) return;
    let alive = true;
    (async () => {
      try {
        const r = await api.get('/hb/pending-impact-ratings');
        if (alive) setPending((r.data && r.data.pending) || []);
      } catch (_) { /* silent — prompt is opportunistic */ }
    })();
    return () => { alive = false; };
  }, [isPro]);

  const visible = pending.filter((p) => !dismissedIds.has(p.id));
  const current = visible[idx] || null;

  const submit = useCallback(async (rating) => {
    if (!current || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      await api.post('/hb/impact-rating', { entry_id: current.id, rating });
      // Remove from local pending so we advance to the next entry (if any).
      setDismissedIds((prev) => new Set(prev).add(current.id));
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setSubmitting(false);
    }
  }, [current, submitting]);

  const dismiss = useCallback(() => {
    if (!current) return;
    // Local-only dismiss (no server call) — session stays pending server-side
    // so we ask again next time the user opens the app.
    setDismissedIds((prev) => new Set(prev).add(current.id));
  }, [current]);

  if (!isPro || !current) return null;

  const label = current.label || `${Math.round(current.frequency || 0)} Hz`;

  return (
    <div
      className="fixed inset-0 z-[75] flex items-end sm:items-center justify-center p-4 bg-[rgba(8,18,15,0.7)] backdrop-blur-sm"
      data-testid="session-impact-prompt"
    >
      <div className="max-w-md w-full rounded-2xl bg-[#0d1a17] border border-[rgba(114,194,172,0.25)] p-6 shadow-[0_20px_80px_rgba(0,0,0,0.55)]">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="flex items-center gap-2">
            <Sparkles size={13} className="text-[#72C2AC]" />
            <span className="label-tiny text-[#72C2AC]">Wellness assistant · check-in</span>
          </div>
          <button
            type="button"
            onClick={dismiss}
            className="text-[#8A9A92] hover:text-[#E8E3D9] transition p-1 -m-1"
            aria-label="Skip for now"
            data-testid="session-impact-prompt-dismiss"
          >
            <X size={16} />
          </button>
        </div>
        <h3 className="text-[#E8E3D9] text-lg leading-snug mt-3">
          How did you feel after yesterday's{' '}
          <span className="text-[#C4A67A]" data-testid="session-impact-prompt-label">
            {label}
          </span>{' '}
          session?
        </h3>
        <div className="text-[#8A9A92] text-xs mt-2">
          Your answer helps me learn which frequencies really move the needle for you.
        </div>

        <div className="mt-5 space-y-2">
          {[
            { key: 'clear_shift', label: 'I noticed a clear shift', color: '#72C2AC' },
            { key: 'subtle_difference', label: 'I felt a subtle difference', color: '#C4A67A' },
            { key: 'not_sure', label: 'Not sure yet', color: '#8A9A92' },
          ].map((opt) => (
            <button
              key={opt.key}
              type="button"
              disabled={submitting}
              onClick={() => submit(opt.key)}
              data-testid={`session-impact-prompt-${opt.key}`}
              className="w-full text-left px-4 py-3 rounded-xl bg-[rgba(196,166,122,0.04)] border border-[rgba(196,166,122,0.15)] hover:bg-[rgba(114,194,172,0.08)] hover:border-[rgba(114,194,172,0.4)] transition text-[#E8E3D9] text-sm flex items-center justify-between disabled:opacity-50"
            >
              <span className="flex items-center gap-2.5">
                <span className="w-2 h-2 rounded-full" style={{ background: opt.color }} />
                {opt.label}
              </span>
              <ArrowRight size={13} className="text-[#8A9A92]" />
            </button>
          ))}
        </div>

        {error && (
          <div className="text-[#D9A45C] text-xs mt-3" data-testid="session-impact-prompt-error">
            {error}
          </div>
        )}

        {visible.length > 1 && (
          <div className="text-[10px] text-[#8A9A92] mt-4 text-center">
            {idx + 1} of {visible.length} · more sessions to review after this one
          </div>
        )}
      </div>
    </div>
  );
}
