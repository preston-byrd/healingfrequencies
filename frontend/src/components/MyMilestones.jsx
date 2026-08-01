import React, { useCallback, useEffect, useState } from 'react';
import { Award, Check } from 'lucide-react';
import api from '@/lib/api';
import MilestoneCelebration from './MilestoneCelebration';

/**
 * MyMilestones — dual-purpose component:
 *  1. Auto-fetches the user's milestone timeline on mount.
 *  2. If any milestone hasn't been celebrated yet, renders the
 *     full-screen MilestoneCelebration overlay for it. Dismissing that
 *     overlay POSTs /celebrate and advances to the next pending one.
 *  3. Renders an inline "My Milestones" card in the HB profile section
 *     showing every earned milestone with its date.
 *
 * Rendered inside HarmonicBlueprintSection only for Pro users.
 */
export default function MyMilestones({ showInlineCard = true }) {
  const [milestones, setMilestones] = useState([]);
  const [pending, setPending] = useState([]);
  const [idx, setIdx] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await api.get('/hb/milestones');
      const data = r.data || {};
      setMilestones(data.milestones || []);
      setPending(data.pending_celebration || []);
      setIdx(0);
    } catch (_) { /* silent */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  const current = pending[idx] || null;

  const dismissCurrent = useCallback(async () => {
    if (!current) return;
    try {
      await api.post(`/hb/milestones/${encodeURIComponent(current.key)}/celebrate`);
    } catch (_) { /* silent — still advance locally */ }
    // Reflect celebrated state locally so the inline card updates without a refetch.
    setMilestones((prev) => prev.map((m) =>
      m.key === current.key ? { ...m, celebrated_at: new Date().toISOString() } : m));
    if (idx + 1 < pending.length) {
      setIdx(idx + 1);
    } else {
      setPending([]);
    }
  }, [current, idx, pending.length]);

  return (
    <>
      {showInlineCard && milestones.length > 0 && (
        <div
          className="rounded-xl p-5 border border-[rgba(196,166,122,0.2)] bg-[rgba(196,166,122,0.02)]"
          data-testid="my-milestones-card"
        >
          <div className="flex items-center gap-2 mb-3">
            <Award size={13} className="text-[#C4A67A]" />
            <div className="label-tiny text-[#C4A67A]">My milestones</div>
          </div>
          <div className="text-[#8A9A92] text-xs mb-4 leading-relaxed">
            Moments worth remembering along your resonance path.
          </div>
          <ul className="space-y-2.5">
            {milestones.map((m) => (
              <li
                key={m.key}
                data-testid={`my-milestones-row-${m.key}`}
                className="flex items-baseline gap-3 rounded-lg bg-[rgba(8,18,15,0.4)] border border-[rgba(196,166,122,0.1)] px-3 py-2.5"
              >
                <span className="text-[#72C2AC] mt-0.5">
                  <Check size={12} />
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-[#E8E3D9] text-sm">{m.title}</div>
                  <div className="text-[10px] font-mono text-[#8A9A92] mt-0.5">
                    {_fmtDate(m.achieved_at)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {current && (
        <MilestoneCelebration milestone={current} onDismiss={dismissCurrent} />
      )}
    </>
  );
}


function _fmtDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '';
  }
}
