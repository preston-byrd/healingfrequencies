import React, { useCallback, useEffect, useState } from 'react';
import { Mail, RefreshCw, Zap, Loader2 } from 'lucide-react';
import api from '@/lib/api';

/**
 * AdminEmailEngagement — admin-only panel that summarises the re-engagement
 * email pipeline. Total sends / opens / clicks (with rates), per-tier
 * breakdown (72h · 7d · 14d · 30d), unsubscribed user count, and a recent
 * nudges list. The "Trigger tick now" button fires one scheduler pass
 * immediately for on-demand ops without waiting on the 15-min cadence.
 */

const TIERS = ['72h', '7d', '14d', '30d'];

function fmtWhen(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch (_) { return iso; }
}

function pct(n, d) {
  if (!d) return '0%';
  return `${Math.round((n / d) * 1000) / 10}%`;
}

export default function AdminEmailEngagement() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [ticking, setTicking] = useState(false);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const { data: d } = await api.get('/admin/email-engagement');
      setData(d);
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not load engagement stats');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const triggerTick = async () => {
    setTicking(true);
    setErr('');
    try {
      await api.post('/admin/email-engagement/tick');
      await load();
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Trigger failed');
    } finally {
      setTicking(false);
    }
  };

  return (
    <div className="glass p-6 border border-[#C4A67A]/30" data-testid="admin-email-engagement">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Mail size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Admin · Email Engagement</div>
        </div>
        <div className="flex items-center gap-3">
          <button
            data-testid="admin-email-engagement-tick"
            onClick={triggerTick}
            disabled={ticking}
            title="Fire one scheduler pass now"
            className="text-[11px] text-[#C4A67A] hover:text-[#72C2AC] inline-flex items-center gap-1 transition-colors disabled:opacity-50"
          >
            {ticking ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
            Trigger tick
          </button>
          <button
            data-testid="admin-email-engagement-refresh"
            onClick={load}
            disabled={loading}
            className="text-[11px] text-[#C4A67A] hover:text-[#72C2AC] inline-flex items-center gap-1 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
      </div>

      {err && <div className="text-xs text-[#D96C6C] mb-3">{err}</div>}

      {!data ? (
        <div className="text-xs text-[#8A9A92]">Loading…</div>
      ) : (
        <>
          {/* Headline metric row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5" data-testid="admin-email-engagement-summary">
            <SummaryStat label="Sent" value={data.total} />
            <SummaryStat label="Delivered" value={data.delivered} />
            <SummaryStat label="Opens" value={data.opened} suffix={pct(data.opened, data.total)} />
            <SummaryStat label="Clicks" value={data.clicked} suffix={pct(data.clicked, data.total)} />
          </div>

          {/* Per-tier breakdown */}
          <div className="mb-5">
            <div className="label-tiny text-[#8A9A92] mb-2">Per-tier breakdown</div>
            <div className="glass-soft p-3 space-y-2">
              {TIERS.map((t) => {
                const row = data.per_tier[t] || { sent: 0, opened: 0, clicked: 0 };
                return (
                  <div key={t} className="flex items-center justify-between text-[12px]" data-testid={`admin-email-engagement-tier-${t}`}>
                    <div className="font-mono text-[#C4A67A] w-14">{t}</div>
                    <div className="text-[#E8E3D9] flex-1">
                      <span className="text-[#C9DED6]">{row.sent}</span>
                      <span className="text-[#5A6B65]"> sent · </span>
                      <span className="text-[#72C2AC]">{row.opened}</span>
                      <span className="text-[#5A6B65]"> opens ({pct(row.opened, row.sent)}) · </span>
                      <span className="text-[#C4A67A]">{row.clicked}</span>
                      <span className="text-[#5A6B65]"> clicks ({pct(row.clicked, row.sent)})</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="text-[11px] text-[#8A9A92] mb-3">
            {data.unsubscribed_users} user{data.unsubscribed_users === 1 ? '' : 's'} unsubscribed
          </div>

          {/* Recent nudges list */}
          <div>
            <div className="label-tiny text-[#8A9A92] mb-2">Recent nudges</div>
            {data.recent.length === 0 ? (
              <div className="text-xs text-[#8A9A92]" data-testid="admin-email-engagement-empty">
                No nudges sent yet.
              </div>
            ) : (
              <div className="space-y-1.5 max-h-[300px] overflow-y-auto custom-scrollbar pr-1">
                {data.recent.map((n) => (
                  <div key={n.id} className="glass-soft p-2.5 flex items-center justify-between text-[12px] gap-3" data-testid={`admin-email-engagement-row-${n.id}`}>
                    <div className="min-w-0 flex-1">
                      <div className="text-[#E8E3D9] truncate">{n.user_email}</div>
                      <div className="text-[10px] text-[#8A9A92] font-mono truncate">
                        {n.tier} · {n.variant_key} {n.top_freq ? `· ${n.top_freq} Hz` : ''}
                      </div>
                    </div>
                    <div className="text-right text-[10px] font-mono flex-shrink-0">
                      <div className="text-[#5A6B65]">{fmtWhen(n.sent_at)}</div>
                      <div className="flex items-center gap-1.5 justify-end mt-0.5">
                        {n.opened_at && <span className="text-[#72C2AC]">opened</span>}
                        {n.clicked_at && <span className="text-[#C4A67A]">clicked</span>}
                        {!n.opened_at && !n.clicked_at && <span className="text-[#5A6B65]">—</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function SummaryStat({ label, value, suffix }) {
  return (
    <div className="glass-soft p-3 text-center">
      <div className="text-[11px] text-[#8A9A92] uppercase tracking-widest">{label}</div>
      <div className="text-[22px] font-display text-[#E8E3D9] leading-tight mt-1">{value ?? 0}</div>
      {suffix && <div className="text-[10px] text-[#C4A67A] font-mono">{suffix}</div>}
    </div>
  );
}
