import React, { useCallback, useEffect, useState } from 'react';
import { MessageSquare, RefreshCw, PhoneOff, PhoneCall, CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react';
import api from '@/lib/api';

/**
 * AdminSMSStats — compact admin tile summarising the Twilio SMS pipeline.
 *
 * Pulls from GET /api/admin/sms/stats and surfaces:
 *   • Sent · Delivered · Failed · Skipped (all-time rolled up)
 *   • 24h + 7d rolling sent momentum
 *   • Opted-in vs stopped user counts (consent health)
 *   • Verified phones
 *   • Last 10 messages (category · last-4 · status · time)
 *
 * Sits next to AdminEmailEngagement so admins can compare email + SMS
 * program health without leaving the account dashboard.
 */

function fmtWhen(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch (_) { return iso; }
}

const STATUS_COLOR = {
  delivered: 'text-[#72C2AC]',
  'sent-test-mode': 'text-[#72C2AC]',
  queued: 'text-[#C4A67A]',
  sent: 'text-[#C4A67A]',
  failed: 'text-[#D96C6C]',
  undelivered: 'text-[#D96C6C]',
  'skipped-unconfigured': 'text-[#8A9A92]',
  unknown: 'text-[#8A9A92]',
};

export default function AdminSMSStats() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const { data: d } = await api.get('/admin/sms/stats');
      setData(d);
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not load SMS stats');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="glass p-6 border border-[#72C2AC]/30" data-testid="admin-sms-stats">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <MessageSquare size={14} className="text-[#72C2AC]" />
          <div className="label-tiny text-[#72C2AC]">Admin · SMS Pipeline</div>
        </div>
        <button
          data-testid="admin-sms-stats-refresh"
          onClick={load}
          disabled={loading}
          className="text-[11px] text-[#72C2AC] hover:text-[#C4A67A] inline-flex items-center gap-1 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {err && (
        <div className="text-xs text-[#D96C6C] mb-3 inline-flex items-center gap-1">
          <AlertTriangle size={11} /> {err}
        </div>
      )}

      {!data ? (
        <div className="text-xs text-[#8A9A92] inline-flex items-center gap-1">
          <Loader2 size={11} className="animate-spin" /> Loading…
        </div>
      ) : (
        <>
          {/* Headline metric row — mirrors AdminEmailEngagement layout. */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5" data-testid="admin-sms-stats-summary">
            <SummaryStat label="Sent" value={data.sent} testid="admin-sms-stats-sent" />
            <SummaryStat label="Delivered" value={data.delivered} testid="admin-sms-stats-delivered" />
            <SummaryStat label="Failed" value={data.failed} tone="danger" testid="admin-sms-stats-failed" />
            <SummaryStat label="Opted-in" value={data.opted_in} testid="admin-sms-stats-optedin" />
          </div>

          {/* Momentum + consent row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <MiniStat icon={<MessageSquare size={11} />} label="Last 24h" value={data.sent_24h} testid="admin-sms-stats-24h" />
            <MiniStat icon={<MessageSquare size={11} />} label="Last 7d" value={data.sent_7d} testid="admin-sms-stats-7d" />
            <MiniStat icon={<CheckCircle2 size={11} className="text-[#72C2AC]" />} label="Verified" value={data.verified_users} testid="admin-sms-stats-verified" />
            <MiniStat icon={<PhoneOff size={11} className="text-[#D96C6C]" />} label="Stopped" value={data.stopped} testid="admin-sms-stats-stopped" />
          </div>

          {/* Category breakdown */}
          {Object.keys(data.by_category || {}).length > 0 && (
            <div className="mb-5">
              <div className="label-tiny text-[#8A9A92] mb-2">Sends by category</div>
              <div className="glass-soft p-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[12px]">
                {Object.entries(data.by_category).map(([cat, count]) => (
                  <div key={cat} className="flex items-center gap-1.5" data-testid={`admin-sms-stats-cat-${cat}`}>
                    <span className="font-mono text-[#C4A67A]">{cat}</span>
                    <span className="text-[#5A6B65]">·</span>
                    <span className="text-[#E8E3D9]">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent sends strip */}
          <div>
            <div className="label-tiny text-[#8A9A92] mb-2">Recent messages</div>
            {(!data.recent || data.recent.length === 0) ? (
              <div className="text-xs text-[#8A9A92]" data-testid="admin-sms-stats-empty">
                No SMS messages yet.
              </div>
            ) : (
              <div className="space-y-1.5 max-h-[240px] overflow-y-auto custom-scrollbar pr-1">
                {data.recent.map((m) => {
                  const statusCls = STATUS_COLOR[m.status] || 'text-[#8A9A92]';
                  return (
                    <div
                      key={m.id}
                      className="glass-soft p-2.5 flex items-center justify-between text-[12px] gap-3"
                      data-testid={`admin-sms-stats-row-${m.id}`}
                    >
                      <div className="min-w-0 flex-1 flex items-center gap-2">
                        <PhoneCall size={11} className="text-[#8A9A92] flex-shrink-0" />
                        <span className="font-mono text-[#C4A67A]">····{m.phone_last4 || '????'}</span>
                        <span className="text-[#5A6B65]">·</span>
                        <span className="text-[#E8E3D9] truncate">{m.category || 'unknown'}</span>
                      </div>
                      <div className="text-right text-[10px] font-mono flex-shrink-0">
                        <div className="text-[#5A6B65]">{fmtWhen(m.sent_at)}</div>
                        <div className={`${statusCls} mt-0.5`}>{m.status}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function SummaryStat({ label, value, suffix, tone, testid }) {
  const valColor = tone === 'danger' && (value ?? 0) > 0 ? 'text-[#D96C6C]' : 'text-[#E8E3D9]';
  return (
    <div className="glass-soft p-3 text-center" data-testid={testid}>
      <div className="text-[11px] text-[#8A9A92] uppercase tracking-widest">{label}</div>
      <div className={`text-[22px] font-display ${valColor} leading-tight mt-1`}>{value ?? 0}</div>
      {suffix && <div className="text-[10px] text-[#C4A67A] font-mono">{suffix}</div>}
    </div>
  );
}

function MiniStat({ icon, label, value, testid }) {
  return (
    <div className="glass-soft p-2.5 flex items-center justify-between" data-testid={testid}>
      <div className="flex items-center gap-1.5 text-[10px] text-[#8A9A92] uppercase tracking-wider">
        {icon} {label}
      </div>
      <div className="text-[14px] font-display text-[#E8E3D9]">{value ?? 0}</div>
    </div>
  );
}
