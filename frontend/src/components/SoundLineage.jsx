import React, { useEffect, useMemo, useState } from 'react';
import api from '@/lib/api';
import { Sparkles } from 'lucide-react';

/**
 * Sound Lineage timeline.
 *
 * One inline SVG chart showing the last `days` of product growth:
 *  - Pale teal bars = daily active users (height = DAU)
 *  - Gold dots     = signups that day
 *  - Coral dots    = checkout sessions initiated
 *  - Bright teal ✦ = billing fulfilments (the conversion point)
 *  - Sand dots     = admin Pro grants
 *
 * All driven by /api/admin/sound-lineage. Lightweight: pure SVG, no chart
 * library, ~6 KB on top of the existing AccountDashboard bundle.
 */
export default function SoundLineage() {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(30);
  const [hover, setHover] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.get('/admin/sound-lineage', { params: { days } })
      .then(({ data: d }) => { if (!cancelled) setData(d); })
      .catch((e) => console.warn('[SoundLineage] load failed', e));
    return () => { cancelled = true; };
  }, [days]);

  const layout = useMemo(() => {
    if (!data || !data.series) return null;
    const W = 760;          // viewBox width (responsive via width="100%")
    const H = 220;
    const PAD_L = 36;
    const PAD_R = 8;
    const PAD_T = 12;
    const PAD_B = 28;
    const innerW = W - PAD_L - PAD_R;
    const innerH = H - PAD_T - PAD_B;
    const n = data.series.length;
    const colW = innerW / n;
    const maxDau = Math.max(1, ...data.series.map((d) => d.daily_active));
    const yFor = (v) => PAD_T + innerH - (v / maxDau) * innerH;
    return { W, H, PAD_L, PAD_R, PAD_T, PAD_B, innerW, innerH, n, colW, maxDau, yFor };
  }, [data]);

  if (!data) {
    return (
      <div className="text-[11px] text-[#5A6B65] italic" data-testid="sound-lineage-loading">
        Loading Sound Lineage…
      </div>
    );
  }
  if (!layout) return null;
  const { W, H, PAD_L, PAD_T, innerH, colW, yFor, maxDau } = layout;

  // Pick ~6 evenly-spaced x-axis labels so the dates stay readable.
  const labelStep = Math.max(1, Math.ceil(data.series.length / 6));

  return (
    <div data-testid="sound-lineage-card" className="glass p-6 border border-[#C4A67A]/30">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Admin · Sound Lineage</div>
        </div>
        <div className="flex gap-1.5" data-testid="lineage-window-chips">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              data-testid={`lineage-window-${d}`}
              onClick={() => setDays(d)}
              className={`px-2.5 py-1 rounded-full text-[10px] tracking-[0.18em] uppercase font-mono border transition-colors ${
                days === d
                  ? 'border-[#C4A67A]/60 bg-[#C4A67A]/15 text-[#C4A67A]'
                  : 'border-[#5C9E8C]/25 bg-black/30 text-[#8A9A92] hover:text-[#E8E3D9]'
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* Totals row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-4" data-testid="lineage-totals">
        {[
          { k: 'peak_dau', label: 'Peak DAU', accent: '#72C2AC' },
          { k: 'signups', label: 'Sign-ups', accent: '#C4A67A' },
          { k: 'checkouts_started', label: 'Checkouts', accent: '#E07A5F' },
          { k: 'billing_fulfilled', label: 'Pro convs.', accent: '#72C2AC' },
          { k: 'admin_grants', label: 'Admin grants', accent: '#C4A67A' },
        ].map(({ k, label, accent }) => (
          <div key={k} className="rounded-lg border border-[#5C9E8C]/15 bg-black/20 p-2.5">
            <div className="text-[9px] tracking-widest uppercase text-[#8A9A92]">{label}</div>
            <div className="font-mono text-xl mt-0.5" style={{ color: accent }}>
              {data.totals[k] ?? 0}
            </div>
          </div>
        ))}
      </div>

      {/* Chart */}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ maxHeight: 260 }}
        data-testid="lineage-svg"
      >
        {/* Horizontal grid lines (3 ticks: 0, mid, max) */}
        {[0, 0.5, 1].map((f, i) => {
          const v = Math.round(maxDau * f);
          const y = PAD_T + innerH - f * innerH;
          return (
            <g key={i}>
              <line x1={PAD_L} x2={W - 8} y1={y} y2={y} stroke="#5C9E8C" strokeOpacity="0.12" strokeWidth="1" />
              <text x={PAD_L - 6} y={y + 3} fontSize="9" fill="#5A6B65" textAnchor="end" fontFamily="ui-monospace, monospace">{v}</text>
            </g>
          );
        })}

        {/* DAU bars */}
        {data.series.map((d, i) => {
          const x = PAD_L + i * colW + colW * 0.18;
          const w = colW * 0.64;
          const y = yFor(d.daily_active);
          const h = (PAD_T + innerH) - y;
          return (
            <rect
              key={d.date}
              x={x} y={y} width={Math.max(2, w)} height={Math.max(0, h)}
              fill="#72C2AC" fillOpacity="0.15" rx="1.5"
              data-testid={`lineage-bar-${d.date}`}
              onMouseEnter={() => setHover({ day: d })}
              onMouseLeave={() => setHover(null)}
            />
          );
        })}

        {/* Event dots overlaid on each day */}
        {data.series.map((d, i) => {
          const cx = PAD_L + i * colW + colW * 0.5;
          // Stack the events vertically near the top so they don't overlap the bar.
          const items = [
            { v: d.signups, color: '#C4A67A', y: 14 },
            { v: d.checkouts_started, color: '#E07A5F', y: 24 },
            { v: d.billing_fulfilled, color: '#72C2AC', y: 34, star: true },
            { v: d.admin_grants, color: '#C4A67A', y: 44 },
          ].filter((it) => it.v > 0);
          return items.map((it, j) => (
            <g key={`${d.date}-${j}`}>
              {it.star ? (
                <text x={cx} y={it.y + 3} fontSize="11" fill={it.color} textAnchor="middle">✦</text>
              ) : (
                <circle cx={cx} cy={it.y} r={Math.min(5, 1.5 + it.v * 0.6)} fill={it.color} />
              )}
            </g>
          ));
        })}

        {/* X-axis labels */}
        {data.series.map((d, i) => {
          if (i % labelStep !== 0 && i !== data.series.length - 1) return null;
          const x = PAD_L + i * colW + colW * 0.5;
          // "MM-DD" — concise enough to fit
          const lbl = d.date.slice(5);
          return (
            <text key={d.date} x={x} y={H - 10} fontSize="9" fill="#5A6B65" textAnchor="middle" fontFamily="ui-monospace, monospace">
              {lbl}
            </text>
          );
        })}
      </svg>

      {/* Hover tooltip for the focused day */}
      {hover && (
        <div className="text-[11px] font-mono text-[#8A9A92] mt-1" data-testid="lineage-hover">
          <span className="text-[#C4A67A]">{hover.day.date}</span>
          {'  '}DAU {hover.day.daily_active}
          {hover.day.signups > 0 && <>  ·  +{hover.day.signups} signup{hover.day.signups === 1 ? '' : 's'}</>}
          {hover.day.checkouts_started > 0 && <>  ·  {hover.day.checkouts_started} checkout{hover.day.checkouts_started === 1 ? '' : 's'}</>}
          {hover.day.billing_fulfilled > 0 && <>  ·  <span className="text-[#72C2AC]">{hover.day.billing_fulfilled} Pro conv</span></>}
          {hover.day.admin_grants > 0 && <>  ·  {hover.day.admin_grants} grant</>}
        </div>
      )}

      {/* Legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 text-[10px] font-mono text-[#5A6B65]">
        <span><span className="inline-block w-2 h-2 mr-1 align-middle rounded-sm" style={{ background: 'rgba(114,194,172,0.4)' }} />DAU</span>
        <span><span className="inline-block w-2 h-2 mr-1 align-middle rounded-full" style={{ background: '#C4A67A' }} />Signups</span>
        <span><span className="inline-block w-2 h-2 mr-1 align-middle rounded-full" style={{ background: '#E07A5F' }} />Checkouts</span>
        <span><span className="text-[#72C2AC] mr-1">✦</span>Pro conversions</span>
      </div>

      {/* Annotation feed — recent Pro unlocks with email + plan */}
      {data.annotations && data.annotations.length > 0 && (
        <div className="mt-4">
          <div className="label-tiny mb-1 text-[#8A9A92]">Recent Pro unlocks</div>
          <div className="space-y-1 max-h-32 overflow-y-auto custom-scrollbar pr-1" data-testid="lineage-annotations">
            {data.annotations.slice(0, 10).map((a, i) => (
              <div key={i} className="flex items-center gap-3 text-[11px] font-mono">
                <span className="text-[#5A6B65] w-[78px] shrink-0">{new Date(a.ts).toLocaleDateString()}</span>
                <span className="text-[#72C2AC] w-[60px] shrink-0">
                  {a.event === 'admin.grant_pro' ? 'grant' : 'paid'}
                </span>
                <span className="text-[#E8E3D9] truncate">{a.user_email || '—'}</span>
                {a.plan && <span className="text-[#C4A67A]">{a.plan}</span>}
                {a.days_added && <span className="text-[#C4A67A]">+{a.days_added}d</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
