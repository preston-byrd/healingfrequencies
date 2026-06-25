import React, { useEffect, useState, useCallback } from 'react';
import { Flame, Bell, BellOff, Clock } from 'lucide-react';
import api from '@/lib/api';

const REMINDER_KEY = 'hf_reminder_enabled';
const REMINDER_TIME_KEY = 'hf_reminder_time'; // "HH:MM"
const REMINDER_LAST_SHOWN = 'hf_reminder_last_shown'; // YYYY-MM-DD

function todayIso() { return new Date().toISOString().slice(0, 10); }

function scheduleReminder() {
  if (!('Notification' in window)) return;
  if (Notification.permission !== 'granted') return;
  const enabled = localStorage.getItem(REMINDER_KEY) === '1';
  if (!enabled) return;
  const time = localStorage.getItem(REMINDER_TIME_KEY) || '20:00';
  const [hh, mm] = time.split(':').map(Number);
  const now = new Date();
  const target = new Date();
  target.setHours(hh, mm, 0, 0);
  if (target <= now) target.setDate(target.getDate() + 1);
  const delay = target - now;
  // Cap timeout to ~24h to avoid setTimeout edge cases
  const safeDelay = Math.min(delay, 24 * 3600 * 1000);
  window.clearTimeout(window.__hfReminderT);
  window.__hfReminderT = window.setTimeout(() => {
    const last = localStorage.getItem(REMINDER_LAST_SHOWN);
    if (last !== todayIso()) {
      try {
        new Notification('A quiet moment awaits 🌿', {
          body: 'Tune in for a few minutes — keep your streak going.',
          silent: false,
        });
        localStorage.setItem(REMINDER_LAST_SHOWN, todayIso());
      } catch (e) { /* noop */ }
    }
    scheduleReminder(); // re-arm
  }, safeDelay);
}

export default function StreakPanel({ refreshKey }) {
  const [streak, setStreak] = useState(null);
  const [permission, setPermission] = useState(
    typeof Notification !== 'undefined' ? Notification.permission : 'denied'
  );
  const [enabled, setEnabled] = useState(localStorage.getItem(REMINDER_KEY) === '1');
  const [time, setTime] = useState(localStorage.getItem(REMINDER_TIME_KEY) || '20:00');

  const load = useCallback(async () => {
    try { const { data } = await api.get('/streak'); setStreak(data); } catch (e) { /* ignore */ }
  }, []);

  useEffect(() => { load(); }, [load, refreshKey]);
  useEffect(() => { scheduleReminder(); }, [enabled, time, permission]);

  const requestPermission = async () => {
    if (!('Notification' in window)) return;
    const p = await Notification.requestPermission();
    setPermission(p);
    if (p === 'granted') {
      setEnabled(true);
      localStorage.setItem(REMINDER_KEY, '1');
    }
  };

  const toggleEnabled = () => {
    const next = !enabled;
    setEnabled(next);
    localStorage.setItem(REMINDER_KEY, next ? '1' : '0');
  };

  const onTimeChange = (v) => {
    setTime(v);
    localStorage.setItem(REMINDER_TIME_KEY, v);
  };

  if (!streak) return null;

  const flameActive = streak.current_streak > 0;
  const totalMin = Math.round(streak.total_minutes || 0);

  return (
    <div className="glass p-5" data-testid="streak-panel">
      <div className="label-tiny mb-3 flex items-center justify-between">
        <span>Your Practice</span>
        {streak.checked_in_today && (
          <span className="text-[#72C2AC] text-[10px] tracking-widest">CHECKED IN</span>
        )}
      </div>

      <div className="flex items-center gap-4 mb-4">
        <div className="relative">
          <Flame
            size={36}
            className={flameActive ? 'text-[#C4A67A]' : 'text-[#8A9A92]'}
            style={flameActive ? { filter: 'drop-shadow(0 0 12px rgba(196,166,122,0.6))' } : {}}
          />
        </div>
        <div>
          <div className="font-mono text-3xl text-[#E8E3D9]" data-testid="current-streak">
            {streak.current_streak}
            <span className="text-xs text-[#8A9A92] ml-2">day{streak.current_streak === 1 ? '' : 's'}</span>
          </div>
          <div className="text-[11px] text-[#8A9A92]">
            Longest {streak.longest_streak} · {totalMin} min total
          </div>
        </div>
      </div>

      <div className="border-t border-[rgba(92,158,140,0.12)] pt-3">
        <div className="flex items-center justify-between mb-2">
          <span className="label-tiny flex items-center gap-1.5">
            {enabled && permission === 'granted' ? <Bell size={12} /> : <BellOff size={12} />}
            Daily Reminder
          </span>
          {permission === 'granted' ? (
            <button
              data-testid="reminder-toggle"
              onClick={toggleEnabled}
              className={`relative w-9 h-5 rounded-full transition-colors ${
                enabled ? 'bg-[#5C9E8C]' : 'bg-[#1A332A]'
              }`}
            >
              <span
                className="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-[#E8E3D9] transition-transform"
                style={{ transform: enabled ? 'translateX(16px)' : 'translateX(0)' }}
              />
            </button>
          ) : (
            <button
              data-testid="enable-notifications-button"
              onClick={requestPermission}
              className="text-[11px] text-[#72C2AC] hover:text-[#C4A67A] transition-colors"
            >
              Enable
            </button>
          )}
        </div>
        {enabled && permission === 'granted' && (
          <div className="flex items-center gap-2">
            <Clock size={12} className="text-[#8A9A92]" />
            <input
              data-testid="reminder-time-input"
              type="time"
              value={time}
              onChange={(e) => onTimeChange(e.target.value)}
              className="bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none text-xs text-[#E8E3D9] py-1 font-mono"
            />
            <span className="text-[10px] text-[#8A9A92]">local time</span>
          </div>
        )}
        {permission === 'denied' && (
          <div className="text-[10px] text-[#8A9A92]">
            Notifications blocked in browser settings.
          </div>
        )}
      </div>
    </div>
  );
}
