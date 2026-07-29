import React, { useEffect, useState, useMemo } from 'react';
import { Sparkles, Compass, Loader2, Clock, Zap, ArrowUpRight, ArrowDown } from 'lucide-react';
import api from '@/lib/api';

/**
 * "My Journey" — longitudinal timeline of a user's most recent listening
 * sessions (last 30, newest first). Reads from GET /api/me/journey which
 * returns the same rows the Wellness Assistant references in its LLM
 * prompt, so what the user sees here is exactly what the companion is
 * remembering about them.
 *
 * Not Pro-gated. Empty state prompts the user to play their first ≥ 60 s
 * session — that's what registers a journey entry.
 */

function fmtRelative(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (diffSec < 60) return 'just now';
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay === 1) return 'yesterday';
  if (diffDay < 7) return `${diffDay}d ago`;
  const diffWk = Math.round(diffDay / 7);
  if (diffWk < 5) return `${diffWk}w ago`;
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function fmtDuration(sec) {
  if (!sec) return '—';
  const m = Math.round(sec / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

function summariseAmbient(amb) {
  if (!amb || typeof amb !== 'object') return '';
  const parts = Object.entries(amb)
    .filter(([, v]) => Number(v) > 0.01)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 3)
    .map(([k, v]) => {
      const n = Number(v);
      const level = n >= 0.6 ? '' : n >= 0.3 ? 'light ' : 'faint ';
      return `${level}${k}`;
    });
  return parts.join(' + ');
}

function entryPrimaryLabel(e) {
  if (e.preset_label) return e.preset_label;
  if (e.soundscape && !e.frequency) return e.soundscape.charAt(0).toUpperCase() + e.soundscape.slice(1);
  if (Number.isFinite(e.frequency) && e.frequency > 0) return `${Math.round(e.frequency)} Hz`;
  return 'Freeform session';
}

const TAG_STYLES = {
  early: 'text-[#C4A67A] border-[#C4A67A]/40 bg-[#C4A67A]/8',
  extended: 'text-[#98C1B0] border-[#98C1B0]/40 bg-[#98C1B0]/8',
  assistant: 'text-[#8FB4C7] border-[#8FB4C7]/40 bg-[#8FB4C7]/8',
};

const TimelineRow = ({ entry, isLast }) => {
  const amb = summariseAmbient(entry.ambient);
  const primary = entryPrimaryLabel(entry);
  const tags = [];
  if (entry.ended_early) tags.push({ key: 'early', label: 'ended early', icon: ArrowDown });
  if (entry.extended) tags.push({ key: 'extended', label: 'extended', icon: ArrowUpRight });
  if (entry.agent_initiated) tags.push({ key: 'assistant', label: 'assistant-led', icon: Sparkles });
  return (
    <div className="relative flex gap-4 pb-6" data-testid="my-journey-row">
      {/* Timeline rail */}
      <div className="flex flex-col items-center pt-1">
        <div className="w-2 h-2 rounded-full bg-[#C4A67A] shadow-[0_0_8px_rgba(196,166,122,0.6)]" />
        {!isLast && <div className="w-px flex-1 bg-gradient-to-b from-[#C4A67A]/30 to-transparent mt-1" />}
      </div>
      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline flex-wrap gap-x-3 gap-y-1">
          <span className="text-[#E8E3D9] font-medium truncate max-w-[240px]" title={primary}>{primary}</span>
          <span className="label-tiny text-[#8A9A92]">{fmtRelative(entry.created_at)} · {entry.time_of_day || ''}</span>
        </div>
        {entry.mood && (
          <div className="text-[13px] italic text-[#B5C4BC] mt-1 truncate" title={entry.mood}>“{entry.mood}”</div>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-[12px] text-[#8A9A92]">
          <span className="inline-flex items-center gap-1"><Clock size={11} />{fmtDuration(entry.duration_actual_seconds)}</span>
          {amb && <span className="truncate max-w-[220px]">{amb}</span>}
          {tags.map(({ key, label, icon: Icon }) => (
            <span
              key={key}
              className={`inline-flex items-center gap-1 px-1.5 py-[1px] rounded-full border text-[10px] uppercase tracking-wider ${TAG_STYLES[key]}`}
              data-testid={`my-journey-tag-${key}`}
            >
              <Icon size={9} />{label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
};

export default function MyJourneySection() {
  const [entries, setEntries] = useState(null); // null = loading, [] = empty
  const [err, setErr] = useState('');
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { data } = await api.get('/me/journey');
        if (!alive) return;
        setEntries(Array.isArray(data?.entries) ? data.entries : []);
      } catch (e) {
        if (!alive) return;
        setErr(e?.response?.data?.detail || e?.message || 'Could not load your journey');
        setEntries([]);
      }
    })();
    return () => { alive = false; };
  }, []);

  const visible = useMemo(() => {
    if (!entries) return [];
    return expanded ? entries : entries.slice(0, 8);
  }, [entries, expanded]);

  return (
    <div className="glass p-6 border border-[#C4A67A]/20" data-testid="my-journey-section">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Compass size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">My Journey</div>
        </div>
        {entries && entries.length > 0 && (
          <div className="text-[11px] text-[#5A6B65]" data-testid="my-journey-count">
            {entries.length} recent session{entries.length === 1 ? '' : 's'}
          </div>
        )}
      </div>

      {entries === null && (
        <div className="flex items-center gap-2 text-[13px] text-[#8A9A92] py-4" data-testid="my-journey-loading">
          <Loader2 size={14} className="animate-spin" />
          Loading your recent sessions…
        </div>
      )}

      {entries && entries.length === 0 && !err && (
        <div className="py-6 text-[13px] text-[#8A9A92] leading-relaxed" data-testid="my-journey-empty">
          Once you complete a listening session (≥ 60 s), it'll show up here.
          Over time, your Wellness Assistant learns which frequencies and
          soundscapes help you most — and will suggest them again when the
          moment fits.
        </div>
      )}

      {err && entries && entries.length === 0 && (
        <div className="text-[12px] text-[#C4A67A]/80 italic py-2" data-testid="my-journey-error">{err}</div>
      )}

      {entries && entries.length > 0 && (
        <div data-testid="my-journey-list">
          {visible.map((e, i) => (
            <TimelineRow
              key={e.id || `${e.created_at}-${i}`}
              entry={e}
              isLast={i === visible.length - 1}
            />
          ))}

          {entries.length > 8 && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              data-testid="my-journey-toggle"
              className="mt-1 text-[11px] uppercase tracking-[0.14em] text-[#C4A67A]/80 hover:text-[#C4A67A] transition-colors"
            >
              {expanded ? 'Show less' : `Show all ${entries.length}`}
            </button>
          )}
        </div>
      )}

      <div className="mt-4 pt-4 border-t border-[#C4A67A]/10 flex items-start gap-2">
        <Zap size={11} className="text-[#8A9A92] mt-[3px] shrink-0" />
        <p className="text-[11px] leading-relaxed text-[#5A6B65]">
          Your Wellness Assistant references this history when suggesting new
          sessions. Stored locally on your account · last 30 sessions.
        </p>
      </div>
    </div>
  );
}
