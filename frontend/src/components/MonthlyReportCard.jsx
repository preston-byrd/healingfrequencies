import React, { useEffect } from 'react';
import { X, TrendingUp, TrendingDown, Minus, Sparkles, Clock, Target, ArrowUpRight, ArrowDownRight } from 'lucide-react';

/**
 * MonthlyReportCard — beautifully designed full-screen monthly summary.
 * Rendered as a portal-style overlay from the Account section (via a
 * "View July Report" button) or auto-surfaced when a fresh report becomes
 * available.
 */
export default function MonthlyReportCard({ report, onClose }) {
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  if (!report) return null;

  const delta = report.resonance_score_delta;
  const hasPrev = report.resonance_score_previous !== null && report.resonance_score_previous !== undefined;
  const listeningMin = report.listening_minutes || 0;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center p-4 bg-[rgba(8,18,15,0.9)] backdrop-blur-md overflow-y-auto"
      data-testid="monthly-report-card"
    >
      <div className="max-w-3xl w-full my-8 rounded-2xl bg-gradient-to-b from-[#0f1c19] to-[#0a1613] border border-[rgba(196,166,122,0.3)] shadow-[0_30px_100px_rgba(0,0,0,0.7)] overflow-hidden">
        {/* Header with warm headline */}
        <div className="relative px-8 pt-8 pb-6 border-b border-[rgba(196,166,122,0.15)]">
          <button
            type="button"
            onClick={onClose}
            className="absolute top-6 right-6 text-[#8A9A92] hover:text-[#E8E3D9] transition p-1"
            aria-label="Close report"
            data-testid="monthly-report-close"
          >
            <X size={18} />
          </button>
          <div className="flex items-center gap-2 mb-2">
            <Sparkles size={13} className="text-[#C4A67A]" />
            <span className="label-tiny text-[#C4A67A]">Monthly Harmonic Blueprint</span>
          </div>
          <h2
            className="font-display text-3xl sm:text-4xl text-[#E8E3D9] leading-tight"
            data-testid="monthly-report-title"
          >
            {report.title}
          </h2>
          <div className="text-[#8A9A92] text-sm mt-2">
            A gentle summary of your resonance work this month
          </div>
        </div>

        <div className="px-8 py-6 space-y-6">
          {/* Top-line stats row */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <StatTile
              testid="monthly-report-total-sessions"
              icon={<Sparkles size={14} />}
              label="Blueprint sessions"
              value={report.total_sessions}
              hint="captures this month"
            />
            <StatTile
              testid="monthly-report-resonance-score"
              icon={<Target size={14} />}
              label="Resonance score"
              value={report.resonance_score_current ?? '—'}
              hint={hasPrev
                ? (delta > 0
                    ? `+${delta} vs ${report.resonance_score_previous} last month`
                    : delta < 0
                      ? `${delta} vs ${report.resonance_score_previous} last month`
                      : `unchanged from ${report.resonance_score_previous} last month`)
                : 'first month tracked'}
              trend={hasPrev ? (delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat') : null}
            />
            <StatTile
              testid="monthly-report-listening"
              icon={<Clock size={14} />}
              label="Blueprint listening"
              value={_fmtMinutes(listeningMin)}
              hint="on recommended sessions"
            />
          </div>

          {/* Most improved */}
          {report.most_improved_ranges && report.most_improved_ranges.length > 0 && (
            <SectionBlock
              testid="monthly-report-most-improved"
              title="Most improved resonance ranges"
              accent="#72C2AC"
              icon={<TrendingUp size={14} className="text-[#72C2AC]" />}
            >
              <ul className="space-y-2">
                {report.most_improved_ranges.map((b) => (
                  <RangeRow key={b.key} band={b} kind="improved" testidBase="monthly-report-improved" />
                ))}
              </ul>
            </SectionBlock>
          )}

          {/* Most persistent gaps */}
          {report.most_persistent_gaps && report.most_persistent_gaps.length > 0 && (
            <SectionBlock
              testid="monthly-report-most-persistent"
              title="Persistent gaps still needing attention"
              accent="#D9A45C"
              icon={<Minus size={14} className="text-[#D9A45C]" />}
            >
              <ul className="space-y-2">
                {report.most_persistent_gaps.map((b) => (
                  <RangeRow key={b.key} band={b} kind="persistent" testidBase="monthly-report-persistent" />
                ))}
              </ul>
            </SectionBlock>
          )}

          {/* Recommended focus */}
          {report.recommended_frequencies && report.recommended_frequencies.length > 0 && (
            <SectionBlock
              testid="monthly-report-recommended"
              title="Recommended focus for next month"
              accent="#C4A67A"
              icon={<ArrowUpRight size={14} className="text-[#C4A67A]" />}
            >
              <ul className="space-y-2">
                {report.recommended_frequencies.map((f, i) => (
                  <li
                    key={i}
                    data-testid={`monthly-report-recommended-${i}`}
                    className="flex items-baseline justify-between rounded-lg bg-[rgba(196,166,122,0.05)] border border-[rgba(196,166,122,0.15)] px-3 py-2.5"
                  >
                    <div>
                      <div className="text-[#E8E3D9] text-sm font-mono">{f.frequency} Hz</div>
                      <div className="text-[10px] text-[#8A9A92] mt-0.5">{f.band}</div>
                    </div>
                    <div className="text-[10px] font-mono text-[#8A9A92]">{f.range}</div>
                  </li>
                ))}
              </ul>
            </SectionBlock>
          )}

          {/* Bottom encouragement */}
          <div className="text-center text-[#8A9A92] text-xs italic border-t border-[rgba(196,166,122,0.12)] pt-5">
            Every capture teaches your blueprint. Rest tonight — tomorrow the tuning continues.
          </div>
        </div>

        <div className="px-8 py-5 border-t border-[rgba(196,166,122,0.15)] flex justify-end">
          <button
            type="button"
            onClick={onClose}
            data-testid="monthly-report-continue"
            className="inline-flex items-center gap-2 text-[#0d1a17] bg-[#C4A67A] hover:bg-[#d4b58a] transition text-xs px-5 py-2.5 rounded-full"
          >
            Continue your journey <ArrowUpRight size={12} />
          </button>
        </div>
      </div>
    </div>
  );
}


function StatTile({ testid, icon, label, value, hint, trend }) {
  const trendColor = trend === 'up' ? '#72C2AC' : trend === 'down' ? '#D9A45C' : '#8A9A92';
  const TrendIcon = trend === 'up' ? ArrowUpRight : trend === 'down' ? ArrowDownRight : null;
  return (
    <div
      data-testid={testid}
      className="rounded-xl bg-[rgba(196,166,122,0.04)] border border-[rgba(196,166,122,0.15)] p-4"
    >
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-[#8A9A92]">
        <span className="text-[#C4A67A]">{icon}</span>
        <span>{label}</span>
      </div>
      <div className="flex items-baseline gap-2 mt-2">
        <div className="text-[#E8E3D9] text-2xl font-display">{value}</div>
        {TrendIcon && <TrendIcon size={14} style={{ color: trendColor }} />}
      </div>
      <div className="text-[10px] text-[#8A9A92] mt-1" style={trend ? { color: trendColor } : undefined}>
        {hint}
      </div>
    </div>
  );
}


function SectionBlock({ testid, title, icon, accent, children }) {
  return (
    <div
      data-testid={testid}
      className="rounded-xl p-4 border"
      style={{ borderColor: `${accent}30` }}
    >
      <div className="flex items-center gap-2 mb-3">
        {icon}
        <div className="label-tiny" style={{ color: accent }}>{title}</div>
      </div>
      {children}
    </div>
  );
}


function RangeRow({ band, kind, testidBase }) {
  const positive = kind === 'improved';
  const accent = positive ? '#72C2AC' : '#D9A45C';
  return (
    <li
      data-testid={`${testidBase}-${band.key}`}
      className="flex items-baseline justify-between rounded-lg bg-[rgba(8,18,15,0.4)] border border-[rgba(196,166,122,0.08)] px-3 py-2.5"
    >
      <div>
        <div className="text-[#E8E3D9] text-sm">{band.label}</div>
        <div className="text-[10px] font-mono text-[#8A9A92] mt-0.5">
          {band.lo}–{band.hi} Hz
        </div>
      </div>
      <div className="text-right">
        <div className="text-xs font-mono" style={{ color: accent }}>
          {positive
            ? `-${band.closure_db.toFixed(1)} dB drift`
            : `${band.current_severity.toFixed(1)} dB off baseline`}
        </div>
      </div>
    </li>
  );
}


function _fmtMinutes(m) {
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}
