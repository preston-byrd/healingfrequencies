import React, { useEffect, useMemo, useState } from 'react';
import { Volume2, RotateCcw, Save, AlertCircle, Check } from 'lucide-react';
import api from '@/lib/api';
import {
  getBaselineDefaults,
  getMergedDefaults,
  invalidateOverridesCache,
  loadAdminOverrides,
} from '@/lib/frequencyDefaults';

/**
 * AdminFrequencyDefaults — admin-only editor for per-frequency ideal
 * default tone volumes.
 *
 * The frontend ships a baseline map (see lib/frequencyDefaults.js). Any
 * value the admin edits here upserts a document into the
 * `frequency_volume_defaults` Mongo collection and overrides the baseline
 * for every user's next session. Clearing a row removes the override so
 * the baseline value re-applies.
 *
 * The editor is deliberately compact — one row per known frequency, a
 * percentage slider (0-100%), and per-row Save / Reset controls. No
 * mass-save button; each row commits independently so a mistake never
 * poisons the whole table.
 */
export default function AdminFrequencyDefaults() {
  const baseline = useMemo(() => getBaselineDefaults(), []);
  const [merged, setMerged] = useState(() => getMergedDefaults());
  const [drafts, setDrafts] = useState({}); // { hz: number 0..1 } — unsaved edits
  const [saving, setSaving] = useState({}); // { hz: true } — in-flight requests
  const [savedFlash, setSavedFlash] = useState({}); // { hz: true } — success indicator
  const [err, setErr] = useState('');

  // Force a fresh fetch of overrides on mount, ignoring the 5-min client cache.
  useEffect(() => {
    invalidateOverridesCache();
    loadAdminOverrides()
      .then(() => setMerged(getMergedDefaults()))
      .catch(() => setErr('Could not load admin overrides.'));
  }, []);

  const rows = useMemo(() => {
    const keys = Object.keys(merged).map(Number).sort((a, b) => a - b);
    return keys.map((hz) => ({
      hz,
      baseline: baseline[hz],
      current: merged[hz],
      isOverride: baseline[hz] === undefined || merged[hz] !== baseline[hz],
    }));
  }, [merged, baseline]);

  const setDraft = (hz, vol) => {
    setDrafts((d) => ({ ...d, [hz]: vol }));
  };

  const getEffective = (hz) => {
    if (typeof drafts[hz] === 'number') return drafts[hz];
    return merged[hz];
  };

  const saveOne = async (hz) => {
    const vol = getEffective(hz);
    if (typeof vol !== 'number') return;
    setSaving((s) => ({ ...s, [hz]: true }));
    setErr('');
    try {
      await api.put('/admin/frequency-defaults', { hz, volume: vol });
      // Merge locally + drop the draft so the row re-renders as saved.
      setMerged((m) => ({ ...m, [hz]: vol }));
      setDrafts((d) => {
        const next = { ...d };
        delete next[hz];
        return next;
      });
      setSavedFlash((f) => ({ ...f, [hz]: true }));
      setTimeout(() => {
        setSavedFlash((f) => {
          const next = { ...f };
          delete next[hz];
          return next;
        });
      }, 1600);
      invalidateOverridesCache();
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Save failed';
      setErr(typeof msg === 'string' ? msg : 'Save failed');
    } finally {
      setSaving((s) => {
        const next = { ...s };
        delete next[hz];
        return next;
      });
    }
  };

  const resetOne = async (hz) => {
    // No stored override → nothing to delete on the server; just clear any
    // in-flight draft so the row re-renders at the baseline value.
    if (baseline[hz] !== undefined && merged[hz] === baseline[hz]) {
      setDrafts((d) => {
        const next = { ...d };
        delete next[hz];
        return next;
      });
      return;
    }
    setSaving((s) => ({ ...s, [hz]: true }));
    setErr('');
    try {
      await api.delete(`/admin/frequency-defaults/${hz}`);
      const nextVal = baseline[hz];
      setMerged((m) => {
        const copy = { ...m };
        if (nextVal === undefined) delete copy[hz];
        else copy[hz] = nextVal;
        return copy;
      });
      setDrafts((d) => {
        const next = { ...d };
        delete next[hz];
        return next;
      });
      invalidateOverridesCache();
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Reset failed';
      setErr(typeof msg === 'string' ? msg : 'Reset failed');
    } finally {
      setSaving((s) => {
        const next = { ...s };
        delete next[hz];
        return next;
      });
    }
  };

  return (
    <div
      data-testid="admin-frequency-defaults-panel"
      className="mt-8 p-5 rounded-2xl bg-white/[0.02] border border-white/[0.06]"
    >
      <div className="flex items-center gap-2 mb-1">
        <Volume2 size={16} className="text-[#72C2AC]" />
        <h3 className="text-sm font-medium text-[#E7EFEA]">Ideal default volume per frequency</h3>
      </div>
      <p className="text-[11px] text-[#8A9A92] leading-relaxed mb-4">
        Tune the starting tone volume applied when each frequency begins
        playback. Users can still adjust the volume mid-session, and the
        "Reset to recommended" chip returns them to the value set here.
        Rows highlighted in gold have an active admin override.
      </p>

      {err && (
        <div className="mb-3 text-xs text-[#EFB067] flex items-center gap-1">
          <AlertCircle size={12} /> {err}
        </div>
      )}

      <div className="grid grid-cols-1 gap-2">
        {rows.map(({ hz, baseline: base, isOverride }) => {
          const val = getEffective(hz);
          const dirty = typeof drafts[hz] === 'number' && drafts[hz] !== merged[hz];
          const isSaving = !!saving[hz];
          const justSaved = !!savedFlash[hz];
          return (
            <div
              key={hz}
              data-testid={`freq-default-row-${hz}`}
              className={`flex items-center gap-3 p-2 rounded-lg ${
                isOverride
                  ? 'bg-[#EFB067]/[0.06] border border-[#EFB067]/20'
                  : 'bg-white/[0.02] border border-white/[0.04]'
              }`}
            >
              <div className="w-16 text-xs font-mono text-[#E7EFEA]">{hz} Hz</div>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={val}
                onChange={(e) => setDraft(hz, parseFloat(e.target.value))}
                className="slider flex-1"
                style={{ '--v': `${val * 100}%` }}
                data-testid={`freq-default-slider-${hz}`}
              />
              <div className="w-12 text-right text-xs font-mono text-[#72C2AC]">
                {Math.round(val * 100)}%
              </div>
              {base !== undefined && (
                <div className="w-16 text-right text-[10px] text-[#8A9A92]">
                  base {Math.round(base * 100)}%
                </div>
              )}
              <button
                type="button"
                onClick={() => saveOne(hz)}
                disabled={!dirty || isSaving}
                data-testid={`freq-default-save-${hz}`}
                className={`px-2 py-1 rounded text-[10px] inline-flex items-center gap-1 transition-colors ${
                  dirty && !isSaving
                    ? 'bg-[#72C2AC]/20 hover:bg-[#72C2AC]/30 text-[#72C2AC]'
                    : 'opacity-30 text-[#8A9A92]'
                }`}
              >
                {justSaved ? <Check size={11} /> : <Save size={11} />}
                Save
              </button>
              <button
                type="button"
                onClick={() => resetOne(hz)}
                disabled={!isOverride || isSaving}
                data-testid={`freq-default-reset-${hz}`}
                className={`px-2 py-1 rounded text-[10px] inline-flex items-center gap-1 transition-colors ${
                  isOverride && !isSaving
                    ? 'bg-white/[0.04] hover:bg-white/[0.08] text-[#8A9A92]'
                    : 'opacity-30 text-[#8A9A92]'
                }`}
                title="Remove admin override for this frequency"
              >
                <RotateCcw size={11} />
                Reset
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
