import React, { useEffect, useState, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Bell, X, Check, CheckCheck, Sparkles, MessageCircleHeart, Waves, Moon, Wand2, ArrowLeft, ArrowRight } from 'lucide-react';
import api from '@/lib/api';

const CATEGORY_ICONS = {
  feature_announcement: Sparkles,
  checkin: MessageCircleHeart,
  recommendation: Wand2,
  session_reminder: Moon,
  harmonic_blueprint: Waves,
};

const CATEGORY_TINT = {
  feature_announcement: '#E8B872',
  checkin: '#F0B4A8',
  recommendation: '#72C2AC',
  session_reminder: '#A9B7D2',
  harmonic_blueprint: '#B79FE8',
};

const CATEGORY_LABEL = {
  feature_announcement: 'What\'s new',
  checkin: 'Gentle check-in',
  recommendation: 'A gentle suggestion',
  session_reminder: 'Session reminder',
  harmonic_blueprint: 'Harmonic Blueprint',
};

// Human-friendly CTA copy per destination path.
function ctaLabelFor(destination) {
  if (!destination) return 'Open';
  const raw = destination.trim();
  if (raw.startsWith('#')) {
    const k = raw.slice(1).toLowerCase();
    if (k === 'wellness-assistant') return 'Open Wellness Assistant';
    if (k === 'harmonic-blueprint') return 'Open Harmonic Blueprint';
    if (k === 'notification-preferences' || k === 'notifications') return 'Open notification settings';
    if (k === 'account') return 'Open Account';
    return 'Open';
  }
  try {
    const u = new URL(raw, 'https://x.example');
    if (u.pathname.startsWith('/play')) {
      const hz = parseFloat(u.searchParams.get('frequency') || '');
      if (!isNaN(hz) && hz > 0) return `Start ${hz} Hz`;
      return 'Start listening';
    }
    if (u.pathname.startsWith('/account')) return 'Open Account';
  } catch { /* fall through */ }
  return 'Explore';
}

// Returns true if the destination actually goes somewhere. A bare "/" is
// treated as "no meaningful destination" — the notification is
// informational and the Explore button is hidden.
function hasMeaningfulDestination(destination) {
  if (!destination) return false;
  const raw = String(destination).trim();
  if (!raw) return false;
  if (raw === '/' || raw === '') return false;
  return true;
}

function timeAgo(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso).getTime();
    const diff = Math.max(0, Date.now() - d);
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const dd = Math.floor(h / 24);
    return `${dd}d ago`;
  } catch { return ''; }
}

/**
 * Bell icon with unread badge + slide-down notification center panel.
 * Polls unread count every 60s; also refreshes on window focus.
 * Applies notification `destination` when user taps a card (handled by parent
 * via `onNavigate(destination)` callback which knows the app's routing).
 */
export default function NotificationBell({ onNavigate, onOpenPreferences, testid = 'notification-bell' }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(false);
  // Reader view — the currently-expanded notification (if any). While a
  // notification is being read, the list view is hidden and a full-body
  // reader takes over the panel body. Back / X return the user to the list
  // WITHOUT closing the panel or navigating.
  const [reading, setReading] = useState(null);
  const panelRef = useRef(null);
  const buttonRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/me/notifications', { params: { limit: 30 } });
      setItems(data?.items || []);
      setUnread(Number(data?.unread || 0));
    } catch { /* graceful */ }
    finally { setLoading(false); }
  }, []);

  const pollUnread = useCallback(async () => {
    try {
      const { data } = await api.get('/me/notifications/unread-count');
      setUnread(Number(data?.unread || 0));
    } catch { /* graceful */ }
  }, []);

  // Initial tick — fires the sweep AND fetches list.
  useEffect(() => {
    (async () => {
      try { await api.post('/me/notifications/tick', {}); } catch { /* graceful */ }
      await load();
    })();
    const iv = setInterval(pollUnread, 60000);
    const onFocus = () => pollUnread();
    window.addEventListener('focus', onFocus);
    return () => { clearInterval(iv); window.removeEventListener('focus', onFocus); };
  }, [load, pollUnread]);

  // Close panel on outside click / Escape. IMPORTANT: also treat the bell
  // button itself as "inside" so a second tap on the bell doesn't first
  // close-via-mousedown and then immediately re-open via onClick.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (panelRef.current && panelRef.current.contains(e.target)) return;
      if (buttonRef.current && buttonRef.current.contains(e.target)) return;
      setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  // Refresh list every time the panel opens; reset reader when panel closes.
  useEffect(() => { if (open) load(); else setReading(null); }, [open, load]);

  const handleOpenItem = (n) => {
    // Mark as opened server-side + locally so the unread badge decrements
    // right away — BUT do NOT navigate. Instead, expand this notification
    // in the reader view so the user can read the full body. Navigation is
    // an explicit second action ("Open" CTA) inside the reader.
    if (!n.opened_at) {
      try { api.post(`/me/notifications/${n.id}/opened`).catch(() => {}); } catch {}
      setItems((prev) => prev.map((x) => x.id === n.id ? { ...x, opened_at: new Date().toISOString() } : x));
      setUnread((u) => Math.max(0, u - 1));
    }
    setReading(n);
  };

  const handleReaderNavigate = () => {
    if (!reading) return;
    const dest = reading.destination;
    if (onNavigate && dest) onNavigate(dest, reading);
    setReading(null);
    setOpen(false);
  };

  const handleReaderDismiss = () => {
    if (!reading) return;
    const n = reading;
    try { api.post(`/me/notifications/${n.id}/dismissed`).catch(() => {}); } catch {}
    setItems((prev) => prev.filter((x) => x.id !== n.id));
    setReading(null);
  };

  const handleDismiss = async (e, n) => {
    e.stopPropagation();
    try { api.post(`/me/notifications/${n.id}/dismissed`).catch(() => {}); } catch {}
    setItems((prev) => prev.filter((x) => x.id !== n.id));
    if (!n.opened_at) setUnread((u) => Math.max(0, u - 1));
  };

  const handleMarkAllRead = async () => {
    try { api.post('/me/notifications/read-all').catch(() => {}); } catch {}
    setItems((prev) => prev.map((x) => ({ ...x, opened_at: x.opened_at || new Date().toISOString() })));
    setUnread(0);
  };

  return (
    <div className="relative inline-flex items-center">
      <button
        ref={buttonRef}
        type="button"
        data-testid={testid}
        onClick={() => setOpen((o) => !o)}
        className="relative inline-flex items-center justify-center w-5 h-5 text-[#8A9A92] hover:text-[#72C2AC] transition-colors"
        aria-label="Notifications"
        aria-expanded={open}
        title="Notifications"
      >
        <Bell size={16} />
        {unread > 0 && (
          <span
            data-testid="notification-unread-badge"
            className="absolute -top-1.5 -right-1.5 min-w-[16px] h-[16px] px-1 rounded-full bg-[#E8B872] text-[#08120F] text-[10px] font-semibold flex items-center justify-center leading-none"
          >
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>
      {open && typeof document !== 'undefined' && createPortal(
        <>
          {/* Mobile backdrop so taps outside the panel dismiss cleanly and the
              user can see the panel isn't buried behind other content. */}
          <div
            className="fixed inset-0 z-40 bg-black/40 sm:hidden"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div
            ref={panelRef}
            data-testid="notification-center"
            role="dialog"
            className="fixed left-3 right-3 top-[64px] sm:left-6 sm:right-auto sm:top-20 sm:w-[380px] max-h-[75vh] sm:max-h-[70vh] z-50 bg-[#0A1612] border border-[#5C9E8C]/40 rounded-xl shadow-[0_18px_40px_-8px_rgba(0,0,0,0.85)] overflow-hidden flex flex-col"
          >
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#5C9E8C]/20">
            <div className="flex items-center gap-2 min-w-0">
              {reading ? (
                <button
                  type="button"
                  data-testid="notification-reader-back"
                  onClick={() => setReading(null)}
                  className="text-[#8A9A92] hover:text-[#72C2AC] transition-colors -ml-1 p-1"
                  aria-label="Back to notifications"
                  title="Back"
                >
                  <ArrowLeft size={14} />
                </button>
              ) : null}
              <div className="flex flex-col min-w-0">
                <div className="text-[10px] uppercase tracking-[0.2em] text-[#5A6B65]">
                  {reading ? 'Reading' : 'Quiet space'}
                </div>
                <div className="text-sm text-[#E8E3D9] truncate" style={{ fontFamily: 'Cormorant Garamond, serif' }}>
                  {reading ? (CATEGORY_LABEL[reading.category] || 'Message') : 'Notifications'}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {!reading && (
                <button
                  type="button"
                  data-testid="notification-mark-all-read"
                  onClick={handleMarkAllRead}
                  className="text-[10px] uppercase tracking-[0.14em] text-[#C4A67A] hover:text-[#E8B872] transition-colors flex items-center gap-1"
                  title="Mark all as read"
                >
                  <CheckCheck size={12} /> Read all
                </button>
              )}
              <button
                type="button"
                data-testid="notification-panel-close"
                onClick={() => setOpen(false)}
                aria-label="Close notifications"
                className="text-[#8A9A92] hover:text-[#F0B4A8] transition-colors p-1 -mr-1"
              >
                <X size={14} />
              </button>
            </div>
          </div>
          {reading ? (
            <NotificationReader
              n={reading}
              onNavigate={handleReaderNavigate}
              onDismiss={handleReaderDismiss}
            />
          ) : (
          <div className="overflow-y-auto divide-y divide-[#5C9E8C]/10">
            {loading && items.length === 0 && (
              <div className="p-6 text-center text-xs text-[#5A6B65]">Loading…</div>
            )}
            {!loading && items.length === 0 && (
              <div className="p-6 text-center text-xs text-[#5A6B65]" data-testid="notification-empty">
                Nothing yet. When something arrives, it will be here — quietly.
              </div>
            )}
            {items.map((n) => {
              const Icon = CATEGORY_ICONS[n.category] || Bell;
              const tint = CATEGORY_TINT[n.category] || '#C4A67A';
              const isNew = !n.opened_at;
              return (
                <button
                  key={n.id}
                  type="button"
                  data-testid={`notification-item-${n.id}`}
                  data-category={n.category}
                  data-unread={isNew ? 'true' : 'false'}
                  onClick={() => handleOpenItem(n)}
                  className={`w-full text-left px-4 py-3 flex gap-3 transition-colors ${
                    isNew ? 'bg-[#5C9E8C]/8 hover:bg-[#5C9E8C]/15' : 'hover:bg-black/25'
                  }`}
                >
                  <div className="mt-0.5 shrink-0" style={{ color: tint }}>
                    <Icon size={16} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="text-sm text-[#E8E3D9] leading-tight truncate">{n.title}</div>
                      {isNew && <span className="w-1.5 h-1.5 rounded-full bg-[#E8B872] shrink-0" aria-hidden />}
                    </div>
                    <div className="text-xs text-[#8A9A92] mt-1 line-clamp-2 leading-snug">{n.body}</div>
                    <div className="text-[10px] uppercase tracking-[0.14em] text-[#5A6B65] mt-1.5">{timeAgo(n.created_at)}</div>
                  </div>
                  <span
                    role="button"
                    aria-label="Dismiss"
                    data-testid={`notification-dismiss-${n.id}`}
                    onClick={(e) => handleDismiss(e, n)}
                    className="text-[#5A6B65] hover:text-[#F0B4A8] transition-colors shrink-0 p-1 -m-1 rounded-full"
                  >
                    <X size={12} />
                  </span>
                </button>
              );
            })}
          </div>
          )}
          <div className="px-4 py-2.5 border-t border-[#5C9E8C]/20 flex items-center justify-between">
            <button
              type="button"
              data-testid="notification-open-preferences"
              onClick={() => { setOpen(false); onOpenPreferences && onOpenPreferences(); }}
              className="text-[10px] uppercase tracking-[0.14em] text-[#C4A67A] hover:text-[#E8B872] transition-colors"
            >
              Notification preferences
            </button>
            <div className="text-[10px] uppercase tracking-[0.14em] text-[#5A6B65] flex items-center gap-1">
              <Check size={10} /> Under your control
            </div>
          </div>
        </div>
        </>,
        document.body,
      )}
    </div>
  );
}

/**
 * Full-message reader shown inside the notification panel when a user taps
 * a card. Displays the complete title + body, timestamp, category chip, and
 * (when present) an explicit "Open" CTA that navigates to the destination.
 * Back arrow / X in the panel header return here to the list without
 * navigating; a Dismiss button in the footer removes the notification.
 */
function NotificationReader({ n, onNavigate, onDismiss }) {
  const Icon = CATEGORY_ICONS[n.category] || Bell;
  const tint = CATEGORY_TINT[n.category] || '#C4A67A';
  const chipLabel = CATEGORY_LABEL[n.category] || 'Notification';
  return (
    <div className="flex-1 overflow-y-auto" data-testid={`notification-reader-${n.id}`}>
      <div className="p-5 space-y-4">
        <div className="flex items-center gap-2">
          <span
            className="inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[10px] uppercase tracking-[0.14em] border"
            style={{ color: tint, borderColor: `${tint}55`, backgroundColor: `${tint}12` }}
          >
            <Icon size={11} /> {chipLabel}
          </span>
          <span className="text-[10px] uppercase tracking-[0.14em] text-[#5A6B65]">{timeAgo(n.created_at)}</span>
        </div>
        <h3
          className="text-lg text-[#E8E3D9] leading-snug"
          style={{ fontFamily: 'Cormorant Garamond, serif' }}
          data-testid="notification-reader-title"
        >
          {n.title}
        </h3>
        <p
          className="text-sm text-[#C9DED6] leading-relaxed whitespace-pre-wrap"
          data-testid="notification-reader-body"
        >
          {n.body}
        </p>
        {hasMeaningfulDestination(n.destination) && (
          <button
            type="button"
            data-testid="notification-reader-navigate"
            onClick={onNavigate}
            className="mt-2 inline-flex items-center gap-2 px-4 py-2 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium text-sm transition-colors"
          >
            {ctaLabelFor(n.destination)} <ArrowRight size={13} />
          </button>
        )}
      </div>
      <div className="px-5 pb-5">
        <button
          type="button"
          data-testid="notification-reader-dismiss"
          onClick={onDismiss}
          className="text-[10px] uppercase tracking-[0.14em] text-[#8A9A92] hover:text-[#F0B4A8] transition-colors"
        >
          Dismiss this notification
        </button>
      </div>
    </div>
  );
}
