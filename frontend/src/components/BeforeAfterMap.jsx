import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight, Sparkles } from 'lucide-react';
import api, { formatApiError } from '@/lib/api';

/**
 * BeforeAfterMap — side-by-side visual comparison of the user's first
 * eigenmode baseline vs their most recent Harmonic Blueprint capture.
 *
 * Colour language (per Solarisound palette):
 *  - Aligned bands (|delta| < 2 dB) → teal (#72C2AC)
 *  - Near bands (2-4 dB)           → soft gold (#C4A67A)
 *  - Drift bands (≥ 4 dB)          → soft amber (#D9A45C)
 *
 * The component fetches its own data when `data` isn't passed in, so it can be
 * mounted standalone from the Account section OR embedded inside the capture
 * celebration overlay with pre-fetched data.
 */
export default function BeforeAfterMap({ data: injected = null, compact = false }) {
  const [data, setData] = useState(injected);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!injected);

  useEffect(() => {
    if (injected) {
      setData(injected);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const r = await api.get('/harmonic-blueprint/before-after');
        if (alive) setData(r.data);
      } catch (e) {
        if (alive) setError(formatApiError(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [injected]);

  if (loading) {
    return (
      <div className="text-[#8A9A92] text-xs" data-testid="before-after-map-loading">
        Loading before-and-after map…
      </div>
    );
  }
  if (error) {
    return (
      <div className="text-[#D9A45C] text-xs" data-testid="before-after-map-error">{error}</div>
    );
  }
  if (!data || !data.baseline) {
    return (
      <div className="text-[#8A9A92] text-xs" data-testid="before-after-map-empty">
        {data?.summary_text || 'Capture your baseline eigenmode to unlock your before-and-after map.'}
      </div>
    );
  }
  if (!data.latest) {
    return (
      <div className="text-[#8A9A92] text-xs" data-testid="before-after-map-no-latest">
        {data.summary_text}
      </div>
    );
  }

  const baseDate = _fmtDate(data.baseline.created_at);
  const latestDate = _fmtDate(data.latest.created_at);

  return (
    <div
      className="rounded-xl border border-[rgba(196,166,122,0.2)] bg-[rgba(196,166,122,0.02)] p-5"
      data-testid="before-after-map"
    >
      <div className="flex items-center gap-2 mb-4">
        <Sparkles size={13} className="text-[#C4A67A]" />
        <div className="label-tiny text-[#C4A67A]">Before &amp; after frequency map</div>
      </div>

      <div className={`grid gap-4 ${compact ? 'grid-cols-1 sm:grid-cols-2' : 'grid-cols-1 md:grid-cols-2'}`}>
        <FrequencyMapPanel
          title="Your first baseline"
          subtitle={baseDate}
          band_deltas={data.band_deltas}
          which="baseline"
        />
        <FrequencyMapPanel
          title="Your latest reading"
          subtitle={latestDate}
          band_deltas={data.band_deltas}
          which="latest"
        />
      </div>

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-4 text-[10px] text-[#8A9A92] mt-4">
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#72C2AC]" /> Aligned with baseline
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#C4A67A]" /> Near
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#D9A45C]" /> Drift
        </span>
      </div>

      {/* Plain-language summary */}
      {data.summary_text && (
        <div
          className="mt-4 text-[#E8E3D9] text-sm leading-relaxed border-t border-[rgba(196,166,122,0.15)] pt-4"
          data-testid="before-after-map-summary"
        >
          {data.summary_text}
        </div>
      )}
    </div>
  );
}


function FrequencyMapPanel({ title, subtitle, band_deltas, which }) {
  // Normalise band dB values into a 0..1 bar length. dB range shown is
  // roughly -45 (silent) to -10 (loud). We clamp so bars stay visually
  // legible even for extreme values.
  const dbMin = -45;
  const dbMax = -10;
  const norm = (db) => Math.max(0, Math.min(1, (db - dbMin) / (dbMax - dbMin)));

  return (
    <div
      className="rounded-lg bg-[rgba(8,18,15,0.4)] border border-[rgba(196,166,122,0.12)] p-4"
      data-testid={`before-after-panel-${which}`}
    >
      <div className="flex items-baseline justify-between gap-2 mb-4">
        <div className="text-[#E8E3D9] text-xs">{title}</div>
        <div className="text-[10px] font-mono text-[#8A9A92]">{subtitle}</div>
      </div>
      <ul className="space-y-3">
        {band_deltas.map((b) => {
          const db = which === 'baseline' ? b.baseline_db : b.latest_db;
          const w = Math.round(norm(db) * 100);
          // Baseline panel: use neutral gold for the user's own baseline so
          // the "improved teal / drift amber" language reads only on the
          // latest panel where the comparison lives.
          const color = which === 'baseline'
            ? '#C4A67A'
            : (b.alignment === 'aligned'
                ? '#72C2AC'
                : b.alignment === 'drift' ? '#D9A45C' : '#C4A67A');
          return (
            <li key={b.key} data-testid={`before-after-band-${which}-${b.key}`}>
              <div className="flex items-baseline justify-between text-[11px] mb-1">
                <span className="text-[#E8E3D9]">{b.label}</span>
                <span className="font-mono text-[#8A9A92]">
                  {b.lo}–{b.hi} Hz
                </span>
              </div>
              <div className="h-2 rounded-full bg-[rgba(196,166,122,0.06)] overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${w}%`, background: color, opacity: 0.85 }}
                />
              </div>
              <div className="text-[10px] font-mono text-[#8A9A92] mt-0.5">
                {db.toFixed(1)} dB
                {which === 'latest' && b.alignment !== 'aligned' && (
                  <span
                    className="ml-2"
                    style={{ color: b.alignment === 'drift' ? '#D9A45C' : '#C4A67A' }}
                  >
                    Δ {b.delta_db > 0 ? '+' : ''}{b.delta_db.toFixed(1)} dB
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}


function _fmtDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '';
  }
}


/**
 * BeforeAfterCelebration — a lightweight overlay shown at the end of every
 * fifth Harmonic Blueprint capture. Wraps <BeforeAfterMap /> inside a
 * calming, dismissible modal so the user gets a gentle progress moment
 * without breaking their session flow.
 */
export function BeforeAfterCelebration({ data, onClose }) {
  const scrollRef = React.useRef(null);
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const raf = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = 0;
      });
    });
    return () => {
      cancelAnimationFrame(raf);
      document.body.style.overflow = prev;
    };
  }, []);

  return createPortal((
    <div
      ref={scrollRef}
      className="fixed inset-0 z-[80] overflow-y-auto bg-[rgba(8,18,15,0.85)] backdrop-blur-md"
      data-testid="before-after-celebration"
    >
      <div className="min-h-full flex items-start justify-center px-4 py-8">
        <div className="max-w-3xl w-full rounded-2xl bg-[#0d1a17] border border-[rgba(196,166,122,0.25)] p-6 shadow-[0_20px_80px_rgba(0,0,0,0.6)]">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="label-tiny text-[#C4A67A]">Progress celebration</div>
            <h3 className="text-[#E8E3D9] text-xl mt-1">
              Look how far you've come
            </h3>
            <div className="text-[#8A9A92] text-xs mt-1">
              {data.session_count} sessions since your first baseline · a gentle look at your shift
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-[#8A9A92] hover:text-[#E8E3D9] transition text-xs px-3 py-1.5 rounded-full border border-[rgba(196,166,122,0.2)]"
            data-testid="before-after-celebration-close"
          >
            Close
          </button>
        </div>
        <BeforeAfterMap data={data} compact />
        <div className="mt-5 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center gap-2 text-[#0d1a17] bg-[#C4A67A] hover:bg-[#d4b58a] transition text-xs px-4 py-2 rounded-full"
            data-testid="before-after-celebration-continue"
          >
            Continue your journey <ArrowRight size={12} />
          </button>
        </div>
        </div>
      </div>
    </div>
  ), document.body);
}
