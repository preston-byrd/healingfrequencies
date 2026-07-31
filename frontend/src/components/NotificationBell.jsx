import React, { useEffect, useState, useRef, useCallback } from 'react';
import { Bell, X, Check, CheckCheck, Sparkles, MessageCircleHeart, Waves, Moon, Wand2 } from 'lucide-react';
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

  // Refresh list every time the panel opens.
  useEffect(() => { if (open) load(); }, [open, load]);

  const handleOpenItem = async (n) => {
    try { api.post(`/me/notifications/${n.id}/opened`).catch(() => {}); } catch {}
    setItems((prev) => prev.map((x) => x.id === n.id ? { ...x, opened_at: new Date().toISOString() } : x));
    setUnread((u) => Math.max(0, u - (n.opened_at ? 0 : 1)));
    if (onNavigate && n.destination) onNavigate(n.destination, n);
    setOpen(false);
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
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        data-testid={testid}
        onClick={() => setOpen((o) => !o)}
        className="relative text-[#8A9A92] hover:text-[#72C2AC] transition-colors"
        aria-label="Notifications"
        aria-expanded={open}
        title="Notifications"
      >
        <Bell size={16} />
        {unread > 0 && (
          <span
            data-testid="notification-unread-badge"
            className="absolute -top-1 -right-1 min-w-[16px] h-[16px] px-1 rounded-full bg-[#E8B872] text-[#08120F] text-[10px] font-semibold flex items-center justify-center leading-none"
          >
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>
      {open && (
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
            className="fixed left-3 right-3 top-[64px] sm:absolute sm:left-auto sm:right-0 sm:top-auto sm:mt-2 sm:w-[380px] max-h-[75vh] sm:max-h-[70vh] z-50 bg-[#0A1612] border border-[#5C9E8C]/40 rounded-xl shadow-[0_18px_40px_-8px_rgba(0,0,0,0.85)] overflow-hidden flex flex-col"
          >
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#5C9E8C]/20">
            <div className="flex flex-col">
              <div className="text-[10px] uppercase tracking-[0.2em] text-[#5A6B65]">Quiet space</div>
              <div className="text-sm text-[#E8E3D9]" style={{ fontFamily: 'Cormorant Garamond, serif' }}>Notifications</div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                data-testid="notification-mark-all-read"
                onClick={handleMarkAllRead}
                className="text-[10px] uppercase tracking-[0.14em] text-[#C4A67A] hover:text-[#E8B872] transition-colors flex items-center gap-1"
                title="Mark all as read"
              >
                <CheckCheck size={12} /> Read all
              </button>
            </div>
          </div>
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
        </>
      )}
    </div>
  );
}
