import React, { useCallback, useEffect, useState } from 'react';
import { Sparkles, Anchor, X, Waves, Clock, Trash2, RotateCcw } from 'lucide-react';
import api, { formatApiError } from '@/lib/api';

/**
 * Phase 4 — Harmonic Blueprint section for the Account dashboard.
 *
 * Aggregates everything the user has accumulated:
 *   - Saved Eigenmode baseline (band snapshot + top dominant frequencies)
 *   - Current drift from baseline (top ranked findings)
 *   - Confirmed resonance points (editable / removable, no re-recording)
 *   - Most recent Eigenmode Journey playlist (compact summary)
 *   - Drift history sparkline (per-capture drift score over time)
 *
 * Voice audio is NEVER stored server-side — this view reads only from the
 * derived resonance profile + journey documents (see /api/harmonic-blueprint/
 * summary and /history endpoints). The banner reiterates this to the user.
 *
 * Props:
 *   isPro         — bool. Controls visibility of the drift-history request
 *                   and the "Manage points" affordance.
 *   onOpenSheet   — () => void. Opens the full-screen Harmonic Blueprint
 *                   sheet so the user can re-run analysis, reset baseline,
 *                   or view the full spectrum map.
 */
export default function HarmonicBlueprintSection({ isPro = false, onOpenSheet }) {
  const [summary, setSummary] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError('');
    setLoading(true);
    try {
      const [s, h] = await Promise.all([
        api.get('/harmonic-blueprint/summary'),
        isPro
          ? api.get('/harmonic-blueprint/history').catch(() => ({ data: { history: [] } }))
          : Promise.resolve({ data: { history: [] } }),
      ]);
      setSummary(s.data || null);
      setHistory((h.data && h.data.history) || []);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [isPro]);

  useEffect(() => { load(); }, [load]);

  // Remove a single confirmed resonance point without re-recording. Uses the
  // PATCH endpoint which replaces the full list — so we just filter and POST.
  const removeGap = async (gapKey) => {
    if (!summary || !summary.latest_profile) return;
    const remaining = (summary.confirmed_gaps || []).filter((g) => g.key !== gapKey);
    setBusy(true);
    try {
      await api.patch(
        `/harmonic-blueprint/profile/${summary.latest_profile.id}/gaps`,
        { confirmed_gaps: remaining },
      );
      setSummary((prev) => ({ ...prev, confirmed_gaps: remaining }));
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="glass p-6" data-testid="harmonic-blueprint-section-loading">
        <div className="label-tiny text-[#C4A67A] inline-flex items-center gap-2">
          <Sparkles size={12} /> Harmonic Blueprint
        </div>
        <div className="text-[#8A9A92] text-sm mt-3">Loading your resonance profile…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="glass p-6 border border-[rgba(217,108,108,0.4)]" data-testid="harmonic-blueprint-section-error">
        <div className="label-tiny text-[#C4A67A] mb-2 inline-flex items-center gap-2">
          <Sparkles size={12} /> Harmonic Blueprint
        </div>
        <div className="text-[#D96C6C] text-sm">{error}</div>
        <button
          type="button"
          onClick={load}
          className="mt-3 text-[#72C2AC] text-xs tracking-widest uppercase hover:text-[#C4A67A] transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  const hasBaseline = !!(summary && summary.eigenmode);

  return (
    <div className="glass p-6 space-y-6" data-testid="harmonic-blueprint-section">
      {/* Header + Open sheet CTA */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="label-tiny text-[#C4A67A] inline-flex items-center gap-2">
            <Sparkles size={12} /> Harmonic Blueprint
          </div>
          <div className="font-display text-2xl text-[#E8E3D9] mt-2 leading-tight">
            {hasBaseline ? 'Your resonance profile' : 'Not captured yet'}
          </div>
          <div className="text-[#8A9A92] text-xs mt-2 leading-relaxed max-w-md">
            Voice samples are processed locally on your device and never stored
            on our servers — only your derived resonance data lives here.
          </div>
        </div>
        <button
          data-testid="harmonic-blueprint-section-open-button"
          type="button"
          onClick={onOpenSheet}
          className="px-5 py-2.5 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] text-sm font-medium tracking-wide transition-colors inline-flex items-center gap-2"
        >
          <RotateCcw size={14} />
          {hasBaseline ? 'Re-run analysis' : 'Capture blueprint'}
        </button>
      </div>

      {!hasBaseline && (
        <div className="text-[#8A9A92] text-sm leading-relaxed" data-testid="harmonic-blueprint-section-empty">
          When you capture your first Harmonic Blueprint, your natural harmonic
          signature is saved as your eigenmode baseline. Every future session
          compares against it and progressively personalises your recommendations.
        </div>
      )}

      {hasBaseline && (
        <>
          {/* Eigenmode baseline snapshot */}
          <EigenmodeCard eigenmode={summary.eigenmode} />

          {/* Current drift */}
          {summary.current_drift && summary.current_drift.length > 0 && (
            <DriftCard drift={summary.current_drift} />
          )}

          {/* Confirmed resonance points (editable) */}
          <ConfirmedPointsCard
            points={summary.confirmed_gaps || []}
            isPro={isPro}
            busy={busy}
            onRemove={removeGap}
          />

          {/* Recent Eigenmode Journey */}
          {summary.latest_journey && (
            <RecentJourneyCard journey={summary.latest_journey} onOpen={onOpenSheet} />
          )}

          {/* Drift history sparkline (Pro only) */}
          {isPro && history.length > 0 && (
            <DriftHistoryCard history={history} />
          )}
        </>
      )}
    </div>
  );
}

// ---------- Sub-cards ------------------------------------------------------

function EigenmodeCard({ eigenmode }) {
  const bands = eigenmode.bands || [];
  const min = Math.min(...bands.map((b) => b.db), -1);
  const max = Math.max(...bands.map((b) => b.db), 0);
  const range = Math.max(1, max - min);
  return (
    <div
      className="rounded-xl p-5 border border-[rgba(196,166,122,0.35)] bg-[rgba(196,166,122,0.03)]"
      data-testid="harmonic-blueprint-section-eigenmode"
    >
      <div className="flex items-center gap-2 mb-4">
        <Anchor size={13} className="text-[#C4A67A]" />
        <div className="label-tiny text-[#C4A67A]">Eigenmode baseline</div>
      </div>
      <div className="space-y-2">
        {bands.map((b) => {
          const pct = Math.max(4, Math.round(((b.db - min) / range) * 100));
          return (
            <div key={b.key} className="flex items-center gap-3">
              <div className="w-24 text-[#C6CDCA] text-xs">{b.label}</div>
              <div className="flex-1 h-1.5 bg-[rgba(196,166,122,0.08)] rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-[#C4A67A] to-[#D6B98A]"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="w-20 text-right text-[#8A9A92] text-[10px] font-mono">
                {b.lo}–{b.hi} Hz
              </div>
            </div>
          );
        })}
      </div>
      {eigenmode.dominant && eigenmode.dominant.length > 0 && (
        <div className="mt-4 pt-4 border-t border-[rgba(196,166,122,0.15)]">
          <div className="text-[#8A9A92] text-[11px] mb-2">Baseline dominant frequencies</div>
          <div className="flex flex-wrap gap-2">
            {eigenmode.dominant.map((p, i) => (
              <span
                key={`${p.hz}-${i}`}
                className="px-2.5 py-1 rounded-full bg-[#72C2AC]/10 text-[#72C2AC] text-xs font-mono"
              >
                {Math.round(p.hz)} Hz
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DriftCard({ drift }) {
  return (
    <div
      className="rounded-xl p-5 border border-[rgba(114,194,172,0.2)]"
      data-testid="harmonic-blueprint-section-drift"
    >
      <div className="flex items-center gap-2 mb-3">
        <Waves size={13} className="text-[#72C2AC]" />
        <div className="label-tiny text-[#72C2AC]">Current drift from baseline</div>
      </div>
      <ul className="space-y-2">
        {drift.map((f, i) => (
          <li key={`${f.key}-${i}`} className="flex items-start gap-3">
            <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-[#72C2AC] shrink-0" />
            <div className="flex-1 flex items-baseline justify-between gap-2 flex-wrap">
              <div className="text-[#E8E3D9] text-sm">
                {f.label}
                <span className="text-[#8A9A92] font-mono ml-2 text-xs">
                  {f.lo}–{f.hi} Hz
                </span>
              </div>
              <div className="text-[#5A6B65] text-xs font-mono">
                {f.delta_db > 0 ? '+' : ''}{f.delta_db.toFixed(1)} dB · {f.direction}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ConfirmedPointsCard({ points, isPro, busy, onRemove }) {
  return (
    <div
      className="rounded-xl p-5 border border-[rgba(114,194,172,0.15)]"
      data-testid="harmonic-blueprint-section-points"
    >
      <div className="flex items-center gap-2 mb-3">
        <Sparkles size={13} className="text-[#C4A67A]" />
        <div className="label-tiny text-[#C4A67A]">Confirmed resonance points</div>
      </div>
      {points.length === 0 ? (
        <div className="text-[#8A9A92] text-sm italic">
          No points confirmed on your latest capture. Re-run your Harmonic
          Blueprint and review the findings to save the ones that feel
          personally relevant.
        </div>
      ) : (
        <ul className="space-y-3">
          {points.map((g, i) => (
            <li
              key={`${g.key}-${i}`}
              className="flex items-start gap-3"
              data-testid={`harmonic-blueprint-section-point-${g.key}`}
            >
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-[#72C2AC] shrink-0" />
              <div className="flex-1">
                <div className="flex items-baseline justify-between gap-2 flex-wrap">
                  <div className="text-[#E8E3D9] text-sm">
                    {g.label}
                    <span className="text-[#8A9A92] font-mono ml-2 text-xs">
                      {g.lo}–{g.hi} Hz
                    </span>
                  </div>
                  {isPro && (
                    <button
                      data-testid={`harmonic-blueprint-section-remove-${g.key}`}
                      type="button"
                      onClick={() => onRemove(g.key)}
                      disabled={busy}
                      className="text-[#8A9A92] hover:text-[#D96C6C] transition-colors p-1 disabled:opacity-40"
                      aria-label={`Remove ${g.label}`}
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
                {g.description && (
                  <div className="text-[#8A9A92] text-xs mt-1 leading-relaxed">
                    {g.description}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RecentJourneyCard({ journey, onOpen }) {
  const tracks = journey.tracks || [];
  const totalMin = Math.round((journey.total_duration_seconds || 0) / 60);
  return (
    <div
      className="rounded-xl p-5 border border-[rgba(114,194,172,0.15)]"
      data-testid="harmonic-blueprint-section-journey"
    >
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Sparkles size={13} className="text-[#72C2AC]" />
          <div className="label-tiny text-[#72C2AC]">{journey.name || 'Your Eigenmode Journey'}</div>
        </div>
        <div className="text-[#5A6B65] text-[10px] tracking-widest uppercase inline-flex items-center gap-1.5">
          <Clock size={10} /> {tracks.length} tracks · {totalMin} min
        </div>
      </div>
      <ul className="space-y-2">
        {tracks.slice(0, 4).map((t, i) => (
          <li key={t.id || i} className="flex items-baseline gap-3">
            <span className="text-[#5A6B65] font-mono text-[10px] w-5 shrink-0">
              {String(i + 1).padStart(2, '0')}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-[#E8E3D9] text-sm truncate">{t.name}</div>
              <div className="text-[#8A9A92] text-[11px] mt-0.5 leading-relaxed line-clamp-2">
                {t.rationale}
              </div>
            </div>
          </li>
        ))}
      </ul>
      {tracks.length > 4 && (
        <div className="text-[#8A9A92] text-xs mt-3">
          + {tracks.length - 4} more tracks
        </div>
      )}
      <button
        type="button"
        onClick={onOpen}
        className="mt-4 text-[#72C2AC] hover:text-[#C4A67A] text-xs tracking-widest uppercase transition-colors"
      >
        Open in Harmonic Blueprint →
      </button>
    </div>
  );
}

function DriftHistoryCard({ history }) {
  // Simple SVG sparkline of drift_score over time. Handles single-entry data
  // gracefully by rendering a flat baseline.
  const w = 320, h = 60, pad = 4;
  const scores = history.map((h) => h.drift_score || 0);
  const max = Math.max(1, ...scores);
  const step = scores.length > 1 ? (w - pad * 2) / (scores.length - 1) : 0;
  const points = scores.map((s, i) => {
    const x = pad + i * step;
    const y = pad + (1 - s / max) * (h - pad * 2);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const last = history[history.length - 1];
  return (
    <div
      className="rounded-xl p-5 border border-[rgba(114,194,172,0.15)]"
      data-testid="harmonic-blueprint-section-history"
    >
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Waves size={13} className="text-[#72C2AC]" />
          <div className="label-tiny text-[#72C2AC]">Drift over time</div>
        </div>
        <div className="text-[#5A6B65] text-[10px] tracking-widest uppercase">
          {history.length} capture{history.length === 1 ? '' : 's'}
        </div>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-auto max-w-md">
        <line x1={pad} x2={w - pad} y1={h - pad} y2={h - pad} stroke="rgba(114,194,172,0.15)" />
        {scores.length >= 2 && (
          <path d={points} fill="none" stroke="#72C2AC" strokeWidth="1.5" />
        )}
        {history.map((h, i) => {
          const x = pad + i * step;
          const y = pad + (1 - (h.drift_score || 0) / max) * (60 - pad * 2);
          const isEigen = !!h.is_eigenmode;
          return (
            <circle
              key={h.id}
              cx={x} cy={y} r={isEigen ? 3.5 : 2.5}
              fill={isEigen ? '#C4A67A' : '#72C2AC'}
            />
          );
        })}
      </svg>
      <div className="flex items-center justify-between text-[10px] text-[#8A9A92] font-mono mt-2">
        <span>oldest</span>
        <span>latest · drift {last ? last.drift_score.toFixed(1) : '0'} dB</span>
      </div>
      <div className="flex items-center gap-4 text-[10px] text-[#8A9A92] mt-3">
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#C4A67A]" /> Eigenmode
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#72C2AC]" /> Capture
        </span>
      </div>
    </div>
  );
}
