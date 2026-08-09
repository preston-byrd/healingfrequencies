import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Inbox, RefreshCw, Search, Send, CheckCircle, Trash2, RotateCcw, Loader2, ChevronDown, ChevronRight, X } from 'lucide-react';
import api from '@/lib/api';

/**
 * AdminSupportInbox — admin-only ticket browser for the support_messages
 * collection (populated by the Support Bubble on the frontend). Lets an
 * admin filter by open / resolved / all, search, expand a row to see the
 * full body + IP + UA, reply inline (delivered via Resend + saved back to
 * the ticket's admin_replies array), and mark tickets resolved / reopen /
 * delete permanently.
 *
 * Rendered inside AccountDashboard's admin gate so only signed-in admins
 * see it. The floating Support Bubble that USERS see is unaffected — this
 * is purely the admin's side of the conversation.
 */

const STATUS_TABS = [
  { key: 'open',     label: 'Open' },
  { key: 'resolved', label: 'Resolved' },
  { key: 'all',      label: 'All' },
];

function fmtWhen(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch (_) { return iso; }
}

export default function AdminSupportInbox() {
  const [status, setStatus] = useState('open');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState([]);
  const [meta, setMeta] = useState({ total: 0, offset: 0, limit: 25, counts: { open: 0, resolved: 0, all: 0 } });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState(null);
  const [replyDrafts, setReplyDrafts] = useState({}); // id -> {text, markResolved, sending, sent, err}

  const load = useCallback(async (nextStatus = status, nextQ = query, nextOffset = 0) => {
    setLoading(true);
    setError('');
    try {
      const { data } = await api.get('/admin/support', {
        params: {
          status: nextStatus,
          q: nextQ || undefined,
          skip: nextOffset,
          limit: meta.limit,
        },
      });
      setItems(data.items || []);
      setMeta({
        total: data.total || 0,
        offset: data.offset || 0,
        limit: data.limit || 25,
        counts: data.counts || { open: 0, resolved: 0, all: 0 },
      });
    } catch (e) {
      setError(e?.response?.data?.detail || 'Could not load support inbox');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta.limit]);

  // Initial fetch — one-shot on mount. Subsequent loads come from tab / search /
  // pagination clicks which call `load(...)` explicitly with the desired args.
  useEffect(() => {
    load('open', '', 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchTab = (tab) => {
    setStatus(tab);
    load(tab, query, 0);
  };

  const submitSearch = (e) => {
    e.preventDefault();
    load(status, query, 0);
  };

  const setDraft = (id, patch) => {
    setReplyDrafts((prev) => ({ ...prev, [id]: { ...(prev[id] || {}), ...patch } }));
  };

  const sendReply = async (msg) => {
    const draft = replyDrafts[msg.id] || {};
    const text = (draft.text || '').trim();
    if (text.length < 5) {
      setDraft(msg.id, { err: 'Reply must be at least 5 characters.' });
      return;
    }
    setDraft(msg.id, { sending: true, err: '' });
    try {
      const { data } = await api.post(`/admin/support/${msg.id}/reply`, {
        message: text.slice(0, 6000),
        mark_resolved: draft.markResolved !== false, // default true
      });
      // Merge the updated ticket back into local state.
      setItems((prev) => prev.map((m) => (m.id === msg.id ? data.message : m)));
      setDraft(msg.id, { sending: false, sent: true, text: '' });
      // Refresh sidebar counts.
      load(status, query, meta.offset);
    } catch (e) {
      setDraft(msg.id, { sending: false, err: e?.response?.data?.detail || 'Send failed' });
    }
  };

  const resolveMsg = async (id) => {
    try {
      const { data } = await api.post(`/admin/support/${id}/resolve`);
      setItems((prev) => prev.map((m) => (m.id === id ? data.message : m)));
      load(status, query, meta.offset);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Resolve failed');
    }
  };

  const reopenMsg = async (id) => {
    try {
      const { data } = await api.post(`/admin/support/${id}/reopen`);
      setItems((prev) => prev.map((m) => (m.id === id ? data.message : m)));
      load(status, query, meta.offset);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Reopen failed');
    }
  };

  const deleteMsg = async (id, email) => {
    if (!window.confirm(`Permanently delete this ticket from ${email || 'user'}? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/support/${id}`);
      setItems((prev) => prev.filter((m) => m.id !== id));
      load(status, query, meta.offset);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Delete failed');
    }
  };

  const totalPages = Math.max(1, Math.ceil(meta.total / meta.limit));
  const currentPage = Math.floor(meta.offset / meta.limit) + 1;

  return (
    <div className="glass p-6 border border-[#C4A67A]/30" data-testid="admin-support-inbox">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Inbox size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Admin · Support Inbox</div>
        </div>
        <button
          data-testid="admin-support-refresh"
          onClick={() => load(status, query, meta.offset)}
          disabled={loading}
          className="text-[11px] text-[#C4A67A] hover:text-[#72C2AC] inline-flex items-center gap-1 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {/* Status filter tabs */}
      <div className="flex items-center gap-1 mb-4 text-[11px]" data-testid="admin-support-tabs">
        {STATUS_TABS.map((t) => {
          const active = status === t.key;
          const count = meta.counts[t.key];
          return (
            <button
              key={t.key}
              data-testid={`admin-support-tab-${t.key}`}
              onClick={() => switchTab(t.key)}
              className={`px-3 py-1.5 rounded-full border transition-colors ${
                active
                  ? 'bg-[#C4A67A]/20 border-[#C4A67A]/45 text-[#C4A67A]'
                  : 'border-[#5C9E8C]/25 text-[#8A9A92] hover:text-[#C9DED6] hover:border-[#5C9E8C]/45'
              }`}
            >
              {t.label}
              <span className="ml-1.5 opacity-70 font-mono">{count}</span>
            </button>
          );
        })}
      </div>

      {/* Search */}
      <form onSubmit={submitSearch} className="flex items-center gap-2 mb-4 max-w-md">
        <Search size={14} className="text-[#8A9A92]" />
        <input
          data-testid="admin-support-search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by email, name, message, or reason…"
          className="flex-1 bg-transparent border-b border-[rgba(196,166,122,0.3)] focus:border-[#C4A67A] outline-none py-2 text-[#E8E3D9] text-sm"
        />
        <button
          type="submit"
          data-testid="admin-support-search-button"
          className="text-[11px] text-[#C4A67A] hover:text-[#72C2AC] px-3 py-1 transition-colors"
        >
          Search
        </button>
      </form>

      {error && (
        <div className="text-xs text-[#D96C6C] mb-3" data-testid="admin-support-error">{error}</div>
      )}

      {/* List */}
      {loading && items.length === 0 ? (
        <div className="text-xs text-[#8A9A92] inline-flex items-center gap-2"><Loader2 size={12} className="animate-spin" /> Loading tickets…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-[#8A9A92]" data-testid="admin-support-empty">
          {status === 'resolved' ? 'No resolved tickets yet.' : 'Inbox is clear. Beautiful.'}
        </div>
      ) : (
        <div className="space-y-2 max-h-[520px] overflow-y-auto custom-scrollbar pr-1">
          {items.map((m) => (
            <TicketRow
              key={m.id}
              ticket={m}
              expanded={expandedId === m.id}
              onExpand={() => setExpandedId(expandedId === m.id ? null : m.id)}
              draft={replyDrafts[m.id] || {}}
              onDraftChange={(patch) => setDraft(m.id, patch)}
              onSendReply={() => sendReply(m)}
              onResolve={() => resolveMsg(m.id)}
              onReopen={() => reopenMsg(m.id)}
              onDelete={() => deleteMsg(m.id, m.user_email)}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {meta.total > meta.limit && (
        <div className="flex items-center justify-between mt-4" data-testid="admin-support-pagination">
          <button
            type="button"
            data-testid="admin-support-prev"
            onClick={() => load(status, query, Math.max(0, meta.offset - meta.limit))}
            disabled={meta.offset === 0 || loading}
            className="text-[11px] uppercase tracking-[0.12em] text-[#C4A67A] hover:text-[#72C2AC] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            ← Prev
          </button>
          <div className="text-[10px] text-[#8A9A92] uppercase tracking-wider">
            Page {currentPage} of {totalPages}
          </div>
          <button
            type="button"
            data-testid="admin-support-next"
            onClick={() => load(status, query, meta.offset + meta.limit)}
            disabled={meta.offset + items.length >= meta.total || loading}
            className="text-[11px] uppercase tracking-[0.12em] text-[#C4A67A] hover:text-[#72C2AC] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function TicketRow({ ticket, expanded, onExpand, draft, onDraftChange, onSendReply, onResolve, onReopen, onDelete }) {
  const isResolved = ticket.status === 'resolved';
  const replyCount = (ticket.admin_replies || []).length;
  const previewText = useMemo(() => {
    const t = (ticket.message || '').replace(/\s+/g, ' ').trim();
    return t.length > 140 ? t.slice(0, 140) + '…' : t;
  }, [ticket.message]);

  return (
    <div
      data-testid={`admin-support-row-${ticket.id}`}
      className={`glass-soft p-3 border transition-colors ${
        expanded ? 'border-[#C4A67A]/45' : 'border-transparent hover:border-[#5C9E8C]/30'
      }`}
    >
      {/* Header line — click to expand */}
      <button
        type="button"
        data-testid={`admin-support-toggle-${ticket.id}`}
        onClick={onExpand}
        className="w-full text-left flex items-center gap-2"
      >
        {expanded ? <ChevronDown size={14} className="text-[#8A9A92] flex-shrink-0" /> : <ChevronRight size={14} className="text-[#8A9A92] flex-shrink-0" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] font-mono text-[#C4A67A]">{ticket.reason_label}</span>
            <span className="text-[11px] text-[#8A9A92]">·</span>
            <span className="text-sm text-[#E8E3D9] truncate">{ticket.user_name || ticket.user_email || 'Unknown'}</span>
            {isResolved ? (
              <span className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC]">Resolved</span>
            ) : (
              <span className="text-[9px] uppercase tracking-widest px-1.5 py-0.5 rounded-full bg-[#C4A67A]/20 text-[#C4A67A]">Open</span>
            )}
            {replyCount > 0 && (
              <span className="text-[9px] uppercase tracking-widest text-[#8A9A92]">{replyCount} repl{replyCount === 1 ? 'y' : 'ies'}</span>
            )}
          </div>
          {!expanded && (
            <div className="text-[12px] text-[#8A9A92] truncate mt-0.5">{previewText}</div>
          )}
        </div>
        <div className="text-[10px] text-[#5A6B65] font-mono flex-shrink-0">{fmtWhen(ticket.created_at)}</div>
      </button>

      {expanded && (
        <div className="mt-3 pt-3 border-t border-[#5C9E8C]/15 space-y-3" data-testid={`admin-support-detail-${ticket.id}`}>
          {/* Metadata strip */}
          <div className="text-[10px] text-[#5A6B65] font-mono grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-0.5">
            <div>From: {ticket.user_email || '—'}</div>
            <div>IP: {ticket.ip || '—'}</div>
            <div>User ID: <span className="text-[#8A9A92]">{(ticket.user_id || '').slice(0, 12)}…</span></div>
            <div>Delivered to admin: {ticket.delivered ? 'yes' : 'no'}</div>
          </div>

          {/* Original message body */}
          <div className="bg-black/25 border border-[#5C9E8C]/15 rounded-lg p-3 text-[13px] text-[#E8E3D9] leading-relaxed whitespace-pre-wrap break-words" data-testid={`admin-support-message-${ticket.id}`}>
            {ticket.message}
          </div>

          {/* Prior admin replies */}
          {(ticket.admin_replies || []).length > 0 && (
            <div className="space-y-2">
              {ticket.admin_replies.map((rep, i) => (
                <div key={i} className="bg-[#5C9E8C]/10 border-l-2 border-[#72C2AC]/60 rounded-r-lg p-3 text-[12px] text-[#C9DED6] leading-relaxed whitespace-pre-wrap break-words">
                  <div className="text-[10px] uppercase tracking-widest text-[#72C2AC] mb-1">
                    Reply · {fmtWhen(rep.at)} {rep.delivered === false && <span className="text-[#C4A67A]/80 ml-2">(not delivered)</span>}
                  </div>
                  {rep.message}
                </div>
              ))}
            </div>
          )}

          {/* Reply form */}
          {!isResolved && (
            <div className="space-y-2">
              {draft.sent && (
                <div className="text-[11px] text-[#72C2AC] inline-flex items-center gap-1"><CheckCircle size={12} /> Reply sent.</div>
              )}
              <textarea
                data-testid={`admin-support-reply-input-${ticket.id}`}
                value={draft.text || ''}
                onChange={(e) => onDraftChange({ text: e.target.value.slice(0, 6000), sent: false, err: '' })}
                placeholder="Write a reply — this goes straight to the user's inbox."
                rows={3}
                className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none resize-none transition-colors"
              />
              {draft.err && (
                <div className="text-[11px] text-[#D96C6C] italic">{draft.err}</div>
              )}
              <div className="flex items-center justify-between flex-wrap gap-2">
                <label className="inline-flex items-center gap-1.5 text-[11px] text-[#8A9A92] cursor-pointer select-none">
                  <input
                    type="checkbox"
                    data-testid={`admin-support-mark-resolved-${ticket.id}`}
                    checked={draft.markResolved !== false}
                    onChange={(e) => onDraftChange({ markResolved: e.target.checked })}
                    className="accent-[#C4A67A]"
                  />
                  Mark resolved after sending
                </label>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    data-testid={`admin-support-resolve-${ticket.id}`}
                    onClick={onResolve}
                    className="text-[11px] text-[#72C2AC] hover:text-[#a8e0cf] px-3 py-1.5 rounded-full border border-[#5C9E8C]/40 hover:border-[#72C2AC] transition-colors inline-flex items-center gap-1"
                  >
                    <CheckCircle size={12} /> Resolve
                  </button>
                  <button
                    type="button"
                    data-testid={`admin-support-send-${ticket.id}`}
                    onClick={onSendReply}
                    disabled={draft.sending || (draft.text || '').trim().length < 5}
                    className="text-[11px] text-[#C4A67A] hover:text-[#E8B872] bg-[#C4A67A]/15 hover:bg-[#C4A67A]/30 border border-[#C4A67A]/40 hover:border-[#C4A67A] px-3 py-1.5 rounded-full transition-colors inline-flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {draft.sending ? <><Loader2 size={12} className="animate-spin" /> Sending…</> : <><Send size={12} /> Send reply</>}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Bottom row — reopen / delete controls */}
          <div className="flex items-center justify-end gap-2 pt-1">
            {isResolved && (
              <button
                type="button"
                data-testid={`admin-support-reopen-${ticket.id}`}
                onClick={onReopen}
                className="text-[11px] text-[#8A9A92] hover:text-[#C4A67A] inline-flex items-center gap-1 px-3 py-1 rounded-full border border-[#5C9E8C]/25 hover:border-[#C4A67A]/45 transition-colors"
              >
                <RotateCcw size={12} /> Reopen
              </button>
            )}
            <button
              type="button"
              data-testid={`admin-support-delete-${ticket.id}`}
              onClick={onDelete}
              title="Permanently delete this ticket"
              className="text-[11px] text-[#8A9A92] hover:text-[#D96C6C] inline-flex items-center gap-1 px-3 py-1 rounded-full border border-transparent hover:border-[#D96C6C]/45 transition-colors"
            >
              <Trash2 size={12} /> Delete
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
