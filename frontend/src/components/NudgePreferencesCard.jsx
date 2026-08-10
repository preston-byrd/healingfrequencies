import React, { useEffect, useState } from 'react';
import { Bell, Loader2, Check } from 'lucide-react';
import api from '@/lib/api';

/**
 * NudgePreferencesCard — small "Email preferences" tile inside the Account
 * view that lets a signed-in user adjust re-engagement email cadence or
 * unsubscribe entirely. Backed by GET/PUT /api/me/nudge-prefs which is the
 * same source of truth used by the one-tap unsubscribe links in nudge
 * email footers.
 */
export default function NudgePreferencesCard() {
  const [prefs, setPrefs] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.get('/me/nudge-prefs')
      .then(({ data }) => { if (!cancelled) setPrefs(data); })
      .catch(() => { if (!cancelled) setPrefs({ unsubscribed: false, cadence: 'default' }); });
    return () => { cancelled = true; };
  }, []);

  const update = async (patch) => {
    setSaving(true);
    setSaved(false);
    try {
      const { data } = await api.put('/me/nudge-prefs', patch);
      setPrefs({
        unsubscribed: !!data.nudge_unsubscribed,
        cadence: data.nudge_cadence || prefs?.cadence || 'default',
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 1600);
    } finally {
      setSaving(false);
    }
  };

  if (!prefs) return null;

  const cadence = prefs.cadence || 'default';
  const off = !!prefs.unsubscribed || cadence === 'off';

  return (
    <div className="glass p-6 mb-6" data-testid="nudge-preferences-card">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Bell size={14} className="text-[#72C2AC]" />
          <div className="label-tiny">Email preferences</div>
        </div>
        {saved && (
          <div className="text-[10px] text-[#72C2AC] inline-flex items-center gap-1">
            <Check size={11} /> Saved
          </div>
        )}
      </div>
      <div className="text-[12px] text-[#8A9A92] mb-4 leading-relaxed">
        We send a gentle re-engagement email if you've been away for a few days.
        Adjust how often — or pause them entirely.
      </div>

      <div className="space-y-2" data-testid="nudge-cadence-options">
        {[
          { key: 'default', label: 'Default',  desc: 'A warm check-in every few days when you\'ve been away' },
          { key: 'weekly',  label: 'Weekly',   desc: 'At most one message per week' },
          { key: 'off',     label: 'Paused',   desc: 'No re-engagement emails' },
        ].map((opt) => {
          const active = (off && opt.key === 'off') || (!off && cadence === opt.key);
          return (
            <button
              key={opt.key}
              type="button"
              data-testid={`nudge-cadence-${opt.key}`}
              disabled={saving}
              onClick={() => update({ cadence: opt.key, unsubscribed: opt.key === 'off' })}
              className={`w-full text-left p-3 rounded-lg border transition-colors ${
                active
                  ? 'bg-[#5C9E8C]/15 border-[#72C2AC]/50 text-[#E8E3D9]'
                  : 'bg-black/25 border-[#5C9E8C]/20 hover:border-[#5C9E8C]/45 text-[#C9DED6]'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium">{opt.label}</div>
                {active && <Check size={13} className="text-[#72C2AC]" />}
              </div>
              <div className="text-[11px] text-[#8A9A92] mt-0.5">{opt.desc}</div>
            </button>
          );
        })}
        {saving && (
          <div className="text-[10px] text-[#5A6B65] inline-flex items-center gap-1 pt-1">
            <Loader2 size={11} className="animate-spin" /> Saving…
          </div>
        )}
      </div>
    </div>
  );
}
