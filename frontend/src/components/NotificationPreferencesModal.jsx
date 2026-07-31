import React, { useEffect, useState } from 'react';
import { X, Bell, BellOff, Smartphone, Loader2 } from 'lucide-react';
import api from '@/lib/api';
import { pushSupported, currentPermission, subscribeToPush, unsubscribeFromPush } from '@/lib/pushClient';

const CATEGORY_LABELS = {
  feature_announcement: {
    label: 'Feature announcements',
    hint: 'When something new lands in Solarisound.',
  },
  checkin: {
    label: 'Gentle check-ins',
    hint: 'Warm invitations to notice how you\'re arriving or feeling.',
  },
  recommendation: {
    label: 'Personalised suggestions',
    hint: 'Frequencies or soundscapes we think might land for you today.',
  },
  session_reminder: {
    label: 'Session reminders',
    hint: 'Quiet nudges when it\'s time for your practice.',
  },
  harmonic_blueprint: {
    label: 'Harmonic Blueprint',
    hint: 'Invitations to capture or rescan your resonance profile.',
  },
};

const HOURS = Array.from({ length: 24 }, (_, i) => i);
const fmtHour = (h) => {
  const H = ((h + 24) % 24);
  const am = H < 12 ? 'AM' : 'PM';
  const disp = H % 12 === 0 ? 12 : H % 12;
  return `${disp}:00 ${am}`;
};

export default function NotificationPreferencesModal({ open, onClose }) {
  const [prefs, setPrefs] = useState(null);
  const [saving, setSaving] = useState(false);
  const [permission, setPermission] = useState(currentPermission());
  const [pushBusy, setPushBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    let alive = true;
    (async () => {
      try {
        const { data } = await api.get('/me/notifications/prefs');
        if (alive) setPrefs(data);
        setPermission(currentPermission());
      } catch (e) {
        if (alive) setError('Could not load your preferences right now.');
      }
    })();
    return () => { alive = false; };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose && onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const savePartial = async (patch) => {
    if (!prefs) return;
    // Optimistic — deep merge for categories / quiet_hours.
    const next = { ...prefs };
    Object.keys(patch).forEach((k) => {
      if (k === 'categories' && patch.categories) next.categories = { ...next.categories, ...patch.categories };
      else if (k === 'quiet_hours' && patch.quiet_hours) next.quiet_hours = { ...next.quiet_hours, ...patch.quiet_hours };
      else next[k] = patch[k];
    });
    setPrefs(next);
    setSaving(true);
    try {
      const { data } = await api.put('/me/notifications/prefs', patch);
      setPrefs(data);
    } catch (e) {
      setError('That change couldn\'t be saved. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const handleEnableBrowserPush = async () => {
    setError('');
    setPushBusy(true);
    try {
      const res = await subscribeToPush();
      if (!res.ok) {
        if (res.reason === 'unsupported') setError('Push notifications aren\'t available in this browser.');
        else if (res.reason === 'denied' || res.reason === 'NotAllowedError') setError('Your browser blocked notifications. Enable them in your browser settings, then try again.');
        else if (res.reason === 'no_vapid_key') setError('Push isn\'t configured on the server right now. In-app notifications still work.');
        else setError('We couldn\'t enable push notifications right now. In-app notifications still work.');
        return;
      }
      setPermission('granted');
      await savePartial({ push_enabled: true });
    } finally {
      setPushBusy(false);
    }
  };

  const handleDisableBrowserPush = async () => {
    setPushBusy(true);
    try {
      await unsubscribeFromPush();
      await savePartial({ push_enabled: false });
    } finally {
      setPushBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div
      data-testid="notification-preferences-modal"
      className="fixed inset-0 z-[100] flex items-center justify-center px-3 sm:px-6 bg-black/70 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-[#0A1612] border border-[#5C9E8C]/40 rounded-2xl shadow-2xl">
        <div className="sticky top-0 flex items-start justify-between px-6 pt-6 pb-3 bg-[#0A1612]/95 backdrop-blur-sm border-b border-[#5C9E8C]/15">
          <div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-[#5A6B65] mb-1">Under your control</div>
            <h3 className="text-xl text-[#E8E3D9]" style={{ fontFamily: 'Cormorant Garamond, serif' }}>
              Notification preferences
            </h3>
          </div>
          <button
            type="button"
            data-testid="notif-prefs-close"
            onClick={onClose}
            className="text-[#8A9A92] hover:text-[#F0B4A8] transition-colors -mt-1 -mr-1 p-1"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {!prefs && (
          <div className="p-8 text-center text-sm text-[#5A6B65]">Loading your preferences…</div>
        )}

        {prefs && (
          <div className="px-6 py-4 space-y-6">
            {error && (
              <div className="text-xs text-[#F0B4A8] bg-[#F0B4A8]/10 border border-[#F0B4A8]/30 rounded-lg px-3 py-2">
                {error}
              </div>
            )}

            {/* Master toggle */}
            <section>
              <label className="flex items-start justify-between gap-3 cursor-pointer">
                <div className="flex-1">
                  <div className="text-sm text-[#E8E3D9]">Notifications</div>
                  <div className="text-xs text-[#8A9A92] mt-1 leading-snug">Turning this off silences every notification, everywhere. You can turn it back on any time.</div>
                </div>
                <TogglePill
                  testid="notif-prefs-master-toggle"
                  checked={!!prefs.enabled}
                  onChange={(v) => savePartial({ enabled: v })}
                />
              </label>
            </section>

            {/* Browser push */}
            <section className="border-t border-[#5C9E8C]/15 pt-5">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="text-sm text-[#E8E3D9] flex items-center gap-2">
                    <Smartphone size={14} className="text-[#72C2AC]" /> Browser & PWA push
                  </div>
                  <div className="text-xs text-[#8A9A92] mt-1 leading-snug">
                    Reach you gently even when the app is closed. Requires browser permission.
                  </div>
                  {pushSupported() ? (
                    permission === 'granted' && prefs.push_enabled ? (
                      <button
                        type="button"
                        data-testid="notif-prefs-push-disable"
                        onClick={handleDisableBrowserPush}
                        disabled={pushBusy}
                        className="mt-3 text-[11px] uppercase tracking-[0.14em] text-[#F0B4A8] hover:text-[#F5C3B8] transition-colors flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {pushBusy ? <Loader2 className="animate-spin" size={12} /> : <BellOff size={12} />}
                        Turn off push
                      </button>
                    ) : (
                      <button
                        type="button"
                        data-testid="notif-prefs-push-enable"
                        onClick={handleEnableBrowserPush}
                        disabled={pushBusy || !prefs.enabled}
                        className="mt-3 text-[11px] uppercase tracking-[0.14em] text-[#72C2AC] hover:text-[#8ED8C1] transition-colors flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {pushBusy ? <Loader2 className="animate-spin" size={12} /> : <Bell size={12} />}
                        Enable browser push
                      </button>
                    )
                  ) : (
                    <div className="mt-2 text-[10px] uppercase tracking-[0.14em] text-[#5A6B65]">
                      Not available on this device — in-app notifications still work
                    </div>
                  )}
                </div>
              </div>
            </section>

            {/* Categories */}
            <section className="border-t border-[#5C9E8C]/15 pt-5">
              <div className="text-[10px] uppercase tracking-[0.2em] text-[#5A6B65] mb-3">Categories</div>
              <div className="space-y-4">
                {Object.entries(CATEGORY_LABELS).map(([key, meta]) => (
                  <label key={key} className="flex items-start justify-between gap-3 cursor-pointer">
                    <div className="flex-1">
                      <div className="text-sm text-[#E8E3D9]">{meta.label}</div>
                      <div className="text-xs text-[#8A9A92] mt-1 leading-snug">{meta.hint}</div>
                    </div>
                    <TogglePill
                      testid={`notif-prefs-cat-${key}`}
                      checked={!!(prefs.categories && prefs.categories[key])}
                      disabled={!prefs.enabled}
                      onChange={(v) => savePartial({ categories: { [key]: v } })}
                    />
                  </label>
                ))}
              </div>
            </section>

            {/* Quiet hours */}
            <section className="border-t border-[#5C9E8C]/15 pt-5">
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="flex-1">
                  <div className="text-sm text-[#E8E3D9]">Quiet hours</div>
                  <div className="text-xs text-[#8A9A92] mt-1 leading-snug">Silence non-urgent notifications overnight.</div>
                </div>
                <TogglePill
                  testid="notif-prefs-quiet-toggle"
                  checked={!!(prefs.quiet_hours && prefs.quiet_hours.enabled)}
                  disabled={!prefs.enabled}
                  onChange={(v) => savePartial({ quiet_hours: { enabled: v } })}
                />
              </div>
              {prefs.quiet_hours && prefs.quiet_hours.enabled && (
                <div className="flex items-center gap-3 mt-2">
                  <div className="flex flex-col">
                    <label className="text-[10px] uppercase tracking-[0.14em] text-[#5A6B65] mb-1">From</label>
                    <select
                      data-testid="notif-prefs-quiet-start"
                      value={prefs.quiet_hours.start_hour}
                      onChange={(e) => savePartial({ quiet_hours: { start_hour: parseInt(e.target.value, 10) } })}
                      className="bg-black/30 border border-[#5C9E8C]/30 rounded-md text-sm text-[#E8E3D9] px-2 py-1.5"
                    >
                      {HOURS.map((h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
                    </select>
                  </div>
                  <div className="flex flex-col">
                    <label className="text-[10px] uppercase tracking-[0.14em] text-[#5A6B65] mb-1">To</label>
                    <select
                      data-testid="notif-prefs-quiet-end"
                      value={prefs.quiet_hours.end_hour}
                      onChange={(e) => savePartial({ quiet_hours: { end_hour: parseInt(e.target.value, 10) } })}
                      className="bg-black/30 border border-[#5C9E8C]/30 rounded-md text-sm text-[#E8E3D9] px-2 py-1.5"
                    >
                      {HOURS.map((h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
                    </select>
                  </div>
                </div>
              )}
            </section>

            {/* Daily cap */}
            <section className="border-t border-[#5C9E8C]/15 pt-5">
              <div className="flex items-center justify-between gap-3">
                <div className="flex-1">
                  <div className="text-sm text-[#E8E3D9]">Daily maximum</div>
                  <div className="text-xs text-[#8A9A92] mt-1 leading-snug">Never more than this many notifications in a day.</div>
                </div>
                <select
                  data-testid="notif-prefs-max-per-day"
                  value={prefs.max_per_day ?? 4}
                  disabled={!prefs.enabled}
                  onChange={(e) => savePartial({ max_per_day: parseInt(e.target.value, 10) })}
                  className="bg-black/30 border border-[#5C9E8C]/30 rounded-md text-sm text-[#E8E3D9] px-2 py-1.5 disabled:opacity-50"
                >
                  {[1, 2, 3, 4, 6, 8, 12].map((n) => <option key={n} value={n}>{n} / day</option>)}
                </select>
              </div>
            </section>

            <div className="text-[10px] text-[#5A6B65] pt-2">
              {saving ? 'Saving…' : 'All changes are saved automatically.'}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TogglePill({ checked, onChange, disabled = false, testid }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!checked}
      data-testid={testid}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative w-11 h-6 rounded-full transition-colors shrink-0 disabled:opacity-40 disabled:cursor-not-allowed ${
        checked ? 'bg-[#5C9E8C]' : 'bg-[#1E2A26] border border-[#5C9E8C]/30'
      }`}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform bg-[#E8E3D9] ${
          checked ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </button>
  );
}
