import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw, Download, ChevronLeft, ChevronRight, Search } from 'lucide-react';
import api, { formatApiError } from '@/lib/api';

/**
 * AdminCancellationsInbox — HF-049 admin dashboard tile that surfaces the
 * `db.cancellations` collection. Mirrors the AdminSupportInbox information
 * architecture so the admin's mental model stays consistent:
 *   • Header title + refresh
 *   • Filter chips (phase: All / Scheduled / Final)
 *   • Search by email
 *   • Row list with reason + type + timestamps
 *   • Summary tiles (this-month total, trial→paid rate, top 3 reasons)
 *   • Download CSV button
 */

const PAGE = 25;
const SUB_TYPE_LABEL = {
  trial:       'Trial',
  pro:         'Pro',
  pro_monthly: 'Pro Monthly',
  pro_annual:  'Pro Annual',
};
const PHASE_LABEL = { scheduled: 'Scheduled', final: 'Final' };

function fmtWhen(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    });
  } catch (_) { return iso; }
}

export default function AdminCancellationsInbox() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [stats, setStats] = useState(null);
  const [phase, setPhase] = useState('');       // '', scheduled, final
  const [subType, setSubType] = useState('');   // '', trial, pro, pro_monthly, pro_annual
  const [q, setQ] = useState('');
  const [qInput, setQInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setErr('');
    try {
      const [{ data: list }, { data: s }] = await Promise.all([
        api.get('/admin/cancellations', {
          params: { offset, limit: PAGE, phase, sub_type: subType, q },
        }),
        api.get('/admin/cancellations/stats'),
      ]);
      setItems(list.items || []);
      setTotal(list.total || 0);
      setStats(s);
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, [offset, phase, subType, q]);

  useEffect(() => { load(); }, [load]);

  const downloadCsv = async () => {
    try {
      const resp = await api.get('/admin/cancellations.csv', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([resp.data], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `cancellations_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setErr(formatApiError(e));
    }
  };

  return (
    <div
      className="glass p-6 border border-[#C4A67A]/30 mt-6"
      data-testid="admin-cancellations-inbox"
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <AlertTriangle size={14} className="text-[#C4A67A]" />
          <div className="label-tiny">Admin · Cancellations</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            data-testid="admin-cancellations-refresh"
            onClick={load}
            className="inline-flex items-center gap-1 text-[11px] text-[#8A9A92] hover:text-[#C4A67A] transition-colors"
          >
            <RefreshCw size={12} /> Refresh
          </button>
          <button
            type="button"
            data-testid="admin-cancellations-csv"
            onClick={downloadCsv}
            className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-full bg-[#C4A67A]/15 border border-[#C4A67A]/35 text-[#C4A67A] hover:bg-[#C4A67A]/25 transition-colors"
          >
            <Download size={12} /> Export CSV
          </button>
        </div>
      </div>

      {/* Summary tiles */}
      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5" data-testid="admin-cancellations-stats">
          <StatTile
            label="Cancellations this month"
            value={stats.total_this_month}
            hint={`${stats.total_all_time} all time`}
            testid="stat-this-month"
          />
          <StatTile
            label="Trial → paid conversion"
            value={`${stats.trial_to_paid_rate}%`}
            hint={`${stats.converted_trial_count}/${stats.total_trial_users} trials`}
            testid="stat-trial-conv"
            accent="teal"
          />
          <TopReasonsTile reasons={stats.top_reasons || []} />
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-1 mb-3 flex-wrap" data-testid="admin-cancellations-filters">
        {[
          { key: '', label: 'All' },
          { key: 'scheduled', label: 'Scheduled' },
          { key: 'final', label: 'Final' },
        ].map((t) => (
          <button
            key={t.key || 'all'}
            data-testid={`admin-cancellations-tab-${t.key || 'all'}`}
            onClick={() => { setOffset(0); setPhase(t.key); }}
            className={`px-3 py-1 rounded-full text-[11px] tracking-wide border transition-colors ${
              phase === t.key
                ? 'bg-[#C4A67A]/20 border-[#C4A67A] text-[#C4A67A]'
                : 'border-[#5C9E8C]/25 text-[#8A9A92] hover:text-[#C9DED6]'
            }`}
          >
            {t.label}
          </button>
        ))}
        <div className="mx-2 h-4 border-l border-[#5C9E8C]/25" />
        <select
          data-testid="admin-cancellations-type"
          value={subType}
          onChange={(e) => { setOffset(0); setSubType(e.target.value); }}
          className="text-[11px] bg-black/25 border border-[#5C9E8C]/25 text-[#C9DED6] px-2 py-1 rounded-full outline-none focus:border-[#72C2AC]"
        >
          <option value="">All types</option>
          <option value="trial">Trial</option>
          <option value="pro_monthly">Pro Monthly</option>
          <option value="pro_annual">Pro Annual</option>
          <option value="pro">Pro</option>
        </select>
      </div>

      {/* Search */}
      <form
        className="flex items-center gap-2 mb-4"
        onSubmit={(e) => { e.preventDefault(); setOffset(0); setQ(qInput); }}
      >
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-[#5A6B65]" />
          <input
            data-testid="admin-cancellations-search-input"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Search by user email…"
            className="w-full bg-black/25 border border-[#5C9E8C]/25 text-[#E8E3D9] text-[12px] pl-7 pr-3 py-1.5 rounded-full outline-none focus:border-[#72C2AC]"
          />
        </div>
        <button
          type="submit"
          data-testid="admin-cancellations-search"
          className="text-[11px] text-[#C4A67A] hover:text-[#E8B872] transition-colors px-2"
        >
          Search
        </button>
      </form>

      {err && (
        <div className="text-xs text-[#D96C6C] mb-3" data-testid="admin-cancellations-error">
          {err}
        </div>
      )}

      {/* Rows */}
      {loading ? (
        <div className="text-xs text-[#8A9A92]" data-testid="admin-cancellations-loading">Loading…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-[#8A9A92]" data-testid="admin-cancellations-empty">
          No cancellations match this view.
        </div>
      ) : (
        <div className="divide-y divide-[#5C9E8C]/15" data-testid="admin-cancellations-list">
          {items.map((r) => <Row key={r.id} row={r} />)}
        </div>
      )}

      {/* Pagination */}
      {total > PAGE && (
        <div className="flex items-center justify-between mt-4 text-[11px] text-[#8A9A92]" data-testid="admin-cancellations-pagination">
          <div>
            {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
          </div>
          <div className="flex items-center gap-1">
            <button
              data-testid="admin-cancellations-prev"
              disabled={offset <= 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
              className="p-1 disabled:opacity-30 hover:text-[#C4A67A] transition-colors"
            >
              <ChevronLeft size={13} />
            </button>
            <button
              data-testid="admin-cancellations-next"
              disabled={offset + PAGE >= total}
              onClick={() => setOffset(offset + PAGE)}
              className="p-1 disabled:opacity-30 hover:text-[#C4A67A] transition-colors"
            >
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function StatTile({ label, value, hint, testid, accent }) {
  const valueColor = accent === 'teal' ? 'text-[#72C2AC]' : 'text-[#C4A67A]';
  return (
    <div
      data-testid={testid}
      className="rounded-lg border border-[#5C9E8C]/20 bg-black/25 p-4"
    >
      <div className="text-[9px] uppercase tracking-widest text-[#8A9A92] mb-1">{label}</div>
      <div className={`font-display text-[26px] leading-none ${valueColor}`}>{value}</div>
      <div className="text-[10px] text-[#5A6B65] mt-1">{hint}</div>
    </div>
  );
}

function TopReasonsTile({ reasons }) {
  return (
    <div
      data-testid="stat-top-reasons"
      className="rounded-lg border border-[#5C9E8C]/20 bg-black/25 p-4"
    >
      <div className="text-[9px] uppercase tracking-widest text-[#8A9A92] mb-2">Top reasons (90d)</div>
      {reasons.length === 0 ? (
        <div className="text-[11px] text-[#5A6B65]">No survey responses yet.</div>
      ) : (
        <ul className="space-y-1">
          {reasons.slice(0, 3).map((r) => (
            <li key={r.reason_key} className="flex items-center justify-between text-[11px] text-[#C9DED6]">
              <span className="truncate max-w-[70%]">{r.reason_label}</span>
              <span className="text-[#C4A67A] font-mono">
                {r.count} · {r.pct}%
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({ row }) {
  const isFinal = row.phase === 'final';
  return (
    <div
      className="py-3 flex items-start gap-3"
      data-testid={`admin-cancellations-row-${row.id}`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap mb-0.5">
          <span
            className={`px-1.5 py-0.5 rounded-full text-[9px] tracking-widest uppercase border ${
              isFinal
                ? 'bg-[#D96C6C]/10 border-[#D96C6C]/40 text-[#D96C6C]'
                : 'bg-[#D9A45C]/10 border-[#D9A45C]/40 text-[#D9A45C]'
            }`}
          >
            {PHASE_LABEL[row.phase] || row.phase}
          </span>
          <span className="px-1.5 py-0.5 rounded-full text-[9px] tracking-widest uppercase bg-[#5C9E8C]/10 border border-[#5C9E8C]/30 text-[#8A9A92]">
            {SUB_TYPE_LABEL[row.subscription_type] || row.subscription_type || 'Pro'}
          </span>
          {row.reactivated_at && (
            <span className="px-1.5 py-0.5 rounded-full text-[9px] tracking-widest uppercase bg-[#72C2AC]/10 border border-[#72C2AC]/40 text-[#72C2AC]">
              Reactivated
            </span>
          )}
        </div>
        <div className="text-sm text-[#E8E3D9] truncate">
          {row.user_email || '—'}
        </div>
        <div className="text-[11px] text-[#8A9A92] mt-0.5">
          <span>Cancelled {fmtWhen(row.cancelled_at)}</span>
          {row.period_end && (
            <span> · Access until {fmtWhen(row.period_end)}</span>
          )}
        </div>
        {row.reason_label && (
          <div className="text-[11px] text-[#C9DED6] mt-1">
            <span className="text-[#C4A67A]">{row.reason_label}</span>
            {row.reason_note && (
              <span className="text-[#8A9A92]"> — “{row.reason_note}”</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
