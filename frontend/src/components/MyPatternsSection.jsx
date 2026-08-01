import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Waves, Sparkles, Sunrise, Repeat, Clock, EyeOff, Loader2, Settings2, RotateCcw } from 'lucide-react';
import api from '@/lib/api';

/**
 * MyPatternsSection — surfaces the recurring behaviours the Wellness
 * Assistant has detected across the user's journey. Each pattern can be
 * dismissed individually; dismissed patterns automatically re-evaluate
 * themselves after 7 new sessions and re-surface if the underlying
 * behaviour is still present. A subtle settings gear tucked in the top
 * right exposes a manual "Reset patterns" escape hatch for the rare
 * case a user wants everything back immediately.
 *
 * The section stays quiet for cold-start users (server floors at ≥ 3 rows)
 * — an empty state explains what will show up here as the user builds a
 * history.
 */

const ICONS = {
  top_frequency: Waves,
  preferred_time_of_day: Sunrise,
  extension_favorite: Repeat,
  unused_soundscapes: Sparkles,
  mood_at_time: Clock,
};

const HEADINGS = {
  top_frequency: 'Frequency you keep coming back to',
  preferred_time_of_day: 'When you tune in',
  extension_favorite: 'Sessions you love to stretch',
  unused_soundscapes: 'A soundscape you haven’t tried yet',
  mood_at_time: 'A mood that recurs at this hour',
};

export default function MyPatternsSection() {
  const [patterns, setPatterns] = useState(null);
  const [dismissed, setDismissed] = useState(new Set());
  const [busyKey, setBusyKey] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [err, setErr] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/me/patterns');
      setPatterns(Array.isArray(data?.patterns) ? data.patterns : []);
      setDismissed(new Set(Array.isArray(data?.dismissed) ? data.dismissed : []));
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || 'Could not load your patterns');
      setPatterns([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Close the settings popover on outside click / Escape so it feels
  // like every other quiet menu in the app.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const active = useMemo(() => {
    if (!Array.isArray(patterns)) return [];
    return patterns.filter((p) => !dismissed.has(p.key));
  }, [patterns, dismissed]);

  const hasDismissed = useMemo(
    () => (Array.isArray(patterns) ? patterns.length - active.length : 0) > 0,
    [patterns, active],
  );

  const handleDismiss = async (key) => {
    setBusyKey(key);
    try {
      const enc = encodeURIComponent(key);
      await api.post(`/me/patterns/${enc}/dismiss`);
      setDismissed((prev) => {
        const next = new Set(prev);
        next.add(key);
        return next;
      });
    } catch (_) { /* graceful */ }
    finally { setBusyKey(null); }
  };

  const handleReset = async () => {
    setResetting(true);
    try {
      await api.post('/me/patterns/clear');
      setDismissed(new Set());
      setMenuOpen(false);
    } catch (_) { /* graceful */ }
    finally { setResetting(false); }
  };

  return (
    <div className="glass p-6 border border-[#C4A67A]/20" data-testid="my-patterns-section">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">My Patterns</div>
        </div>
        <div className="relative" ref={menuRef}>
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Pattern settings"
            data-testid="my-patterns-settings-toggle"
            className="inline-flex items-center justify-center w-7 h-7 rounded-full text-[#8A9A92] hover:text-[#C4A67A] hover:bg-[#C4A67A]/8 transition-colors"
          >
            <Settings2 size={13} />
          </button>
          {menuOpen && (
            <div
              role="menu"
              data-testid="my-patterns-settings-menu"
              className="absolute right-0 top-full mt-2 min-w-[220px] rounded-md border border-[#C4A67A]/25 bg-[#0E1414]/95 backdrop-blur-md shadow-lg z-20 py-1"
            >
              <button
                type="button"
                role="menuitem"
                onClick={handleReset}
                disabled={resetting || !hasDismissed}
                data-testid="my-patterns-reset"
                className="w-full text-left px-3 py-2 text-[12.5px] text-[#E8E3D9] hover:bg-[#C4A67A]/10 disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-2 transition-colors"
                title={hasDismissed
                  ? 'Un-dismiss every pattern so they re-surface now'
                  : 'Nothing to reset — no patterns are currently dismissed'}
              >
                {resetting ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} className="text-[#C4A67A]" />}
                Reset patterns
              </button>
              <div className="px-3 pt-1 pb-2 text-[10.5px] text-[#5A6B65] leading-snug">
                Dismissed patterns re-evaluate on their own after 7 new sessions.
              </div>
            </div>
          )}
        </div>
      </div>

      {patterns === null && (
        <div className="flex items-center gap-2 text-[13px] text-[#8A9A92] py-4" data-testid="my-patterns-loading">
          <Loader2 size={14} className="animate-spin" />
          Looking for patterns in your recent sessions…
        </div>
      )}

      {patterns && patterns.length === 0 && !err && (
        <div className="py-6 text-[13px] text-[#8A9A92] leading-relaxed" data-testid="my-patterns-empty">
          Once you've completed a few sessions, the Wellness Assistant will
          start noticing patterns — your steady favourites, the times you
          tune in, moods that recur — and gently reference them here.
        </div>
      )}

      {err && patterns && patterns.length === 0 && (
        <div className="text-[12px] text-[#C4A67A]/80 italic py-2" data-testid="my-patterns-error">{err}</div>
      )}

      {patterns && patterns.length > 0 && active.length === 0 && (
        <div className="py-4 text-[13px] text-[#8A9A92] leading-relaxed" data-testid="my-patterns-all-dismissed">
          You've dismissed every pattern for now. Each one will quietly
          re-evaluate itself after 7 new sessions and surface again if
          the behaviour is still present.
        </div>
      )}

      {active.length > 0 && (
        <div className="space-y-3" data-testid="my-patterns-list">
          {active.map((p) => {
            const Icon = ICONS[p.kind] || Sparkles;
            return (
              <div
                key={p.key}
                data-testid="my-patterns-row"
                className="flex items-start gap-3 py-2 pr-1 border-b border-[#C4A67A]/8 last:border-b-0"
              >
                <div className="w-8 h-8 rounded-full bg-[#C4A67A]/12 flex items-center justify-center shrink-0 mt-0.5">
                  <Icon size={13} className="text-[#C4A67A]" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] uppercase tracking-[0.14em] text-[#8A9A92]">
                    {HEADINGS[p.kind] || p.kind}
                  </div>
                  <div className="text-[13.5px] text-[#E8E3D9] leading-relaxed mt-1">
                    {p.message}
                  </div>
                  {p.count > 0 && (
                    <div className="text-[10px] text-[#5A6B65] mt-1 uppercase tracking-wider">
                      {p.count} occurrence{p.count === 1 ? '' : 's'}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => handleDismiss(p.key)}
                  disabled={busyKey === p.key}
                  data-testid={`my-patterns-dismiss-${p.kind}`}
                  className="shrink-0 inline-flex items-center gap-1 rounded-full text-[10px] uppercase tracking-wider text-[#8A9A92] hover:text-[#C4A67A] transition-colors px-2 py-1 disabled:opacity-40"
                >
                  {busyKey === p.key ? <Loader2 size={10} className="animate-spin" /> : <EyeOff size={10} />}
                  Dismiss
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
