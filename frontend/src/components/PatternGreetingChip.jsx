import React, { useEffect, useMemo, useState } from 'react';
import { Sparkles, X, ArrowRight } from 'lucide-react';
import api from '@/lib/api';

/**
 * PatternGreetingChip — a small warm chip that surfaces on Dashboard open
 * once the user has recurring behaviours worth noticing (e.g. top frequency,
 * time-of-day preference, extension favourite, unused soundscape, mood-at-time).
 *
 * Design principles:
 *   • Only one chip at a time (highest-priority undismissed pattern).
 *   • Dismissed patterns persist server-side across sessions.
 *   • Cold-start users see nothing (server floors at ≥ 3 rows).
 *   • Session-scoped fade-out: once the user X's it, the chip won't
 *     re-render this page load even if we haven't posted the dismiss yet.
 *
 * Props:
 *   onArm(cta)  — called when the user taps the CTA. Cta shape:
 *                 { action:'arm_frequency'|'arm_preset'|'arm_soundscape', ... }
 *                 Parent (Dashboard) applies the cta to audioEngine + state.
 */

// Priority match to backend `_pattern_priority` — kept in sync so the chip
// matches what the LLM sees in USER_PATTERNS.
const PRIORITY = {
  mood_at_time: 5,
  extension_favorite: 4,
  top_frequency: 3,
  preferred_time_of_day: 2,
  unused_soundscapes: 1,
};

export default function PatternGreetingChip({ onArm }) {
  const [patterns, setPatterns] = useState(null); // null = loading
  const [dismissed, setDismissed] = useState(new Set());
  const [sessionHidden, setSessionHidden] = useState(false); // this page load only
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { data } = await api.get('/me/patterns');
        if (!alive) return;
        setPatterns(Array.isArray(data?.patterns) ? data.patterns : []);
        setDismissed(new Set(Array.isArray(data?.dismissed) ? data.dismissed : []));
      } catch (_) {
        if (alive) setPatterns([]);
      }
    })();
    return () => { alive = false; };
  }, []);

  const chosen = useMemo(() => {
    if (!Array.isArray(patterns)) return null;
    const active = patterns
      .filter((p) => !dismissed.has(p.key))
      .sort((a, b) => (PRIORITY[b.kind] || 0) - (PRIORITY[a.kind] || 0));
    return active[0] || null;
  }, [patterns, dismissed]);

  if (!chosen || sessionHidden) return null;

  const handleDismiss = async () => {
    // Fade instantly — the network call is fire-and-forget for UX snap.
    setSessionHidden(true);
    try {
      const enc = encodeURIComponent(chosen.key);
      await api.post(`/me/patterns/${enc}/dismiss`);
      setDismissed((prev) => {
        const next = new Set(prev);
        next.add(chosen.key);
        return next;
      });
    } catch (_) { /* graceful — chip is already hidden locally */ }
  };

  const handleArm = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (chosen.cta && typeof onArm === 'function') {
        // Guard against a throwing / rejecting onArm — otherwise it
        // becomes an unhandled promise rejection when React invokes
        // the async handler. The chip should still dismiss even if the
        // parent's CTA application fails, so the user isn't stuck.
        try { await onArm(chosen.cta); } catch (e) { console.warn('[PatternGreetingChip] onArm failed', e); }
      }
      // Once acted on, the chip has served its purpose — dismiss it so the
      // next pattern in line gets a turn on the next open.
      const enc = encodeURIComponent(chosen.key);
      api.post(`/me/patterns/${enc}/dismiss`).catch(() => {});
      setSessionHidden(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="w-full flex justify-center px-3 pt-3 pointer-events-none"
      data-testid="pattern-greeting-chip"
    >
      <div
        className="glass-soft border border-[#C4A67A]/30 rounded-full pl-3 pr-1.5 py-1.5 flex items-center gap-2 max-w-[560px] pointer-events-auto animate-[fadeIn_0.5s_ease-out]"
        style={{ backdropFilter: 'blur(14px)' }}
      >
        <Sparkles size={12} className="text-[#C4A67A] shrink-0" />
        <span
          className="text-[12px] text-[#E8E3D9] leading-snug truncate"
          data-testid="pattern-greeting-message"
          title={chosen.message}
        >
          {chosen.message}
        </span>
        {chosen.cta && (
          <button
            type="button"
            onClick={handleArm}
            disabled={busy}
            data-testid="pattern-greeting-cta"
            className="shrink-0 inline-flex items-center gap-1 rounded-full bg-[#C4A67A]/20 hover:bg-[#C4A67A]/35 border border-[#C4A67A]/40 hover:border-[#C4A67A] text-[#C4A67A] text-[11px] font-medium px-2.5 py-1 transition-colors disabled:opacity-40"
          >
            Start there
            <ArrowRight size={10} />
          </button>
        )}
        <button
          type="button"
          onClick={handleDismiss}
          data-testid="pattern-greeting-dismiss"
          aria-label="Dismiss pattern"
          className="shrink-0 w-6 h-6 flex items-center justify-center rounded-full text-[#5A6B65] hover:text-[#C9DED6] hover:bg-black/25 transition-colors"
        >
          <X size={11} />
        </button>
      </div>
    </div>
  );
}
