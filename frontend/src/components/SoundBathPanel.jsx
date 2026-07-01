import React, { useEffect, useState } from 'react';
import { Waves, Sparkles, Play, Square, Lock, Bookmark, Check } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import { BATH_PRESETS, getSoundBath } from '@/lib/soundBathEngine';

/**
 * SoundBathPanel — collapsible panel with 7 sound-bath presets. Each preset
 * fires the algorithmic scheduler in `soundBathEngine` — every click starts a
 * fresh randomised arrangement (different notes / timing / pan / velocity
 * pattern) while still adhering to the preset's scale + voice mix.
 *
 * Props:
 *   isPro       — bool. When false the panel is Pro-locked (blurred + upsell
 *                 CTA, all preset clicks route to onUnlock).
 *   onUnlock    — () => void, opens the Account paywall.
 *   onSaveBath  — (presetKey, label) => Promise<void>, persists the currently
 *                 playing bath as a bookmarkable "arrangement".
 *   onBathStart — (presetKey) => void, notifies parent that a bath just
 *                 started so it can arm the session timer / update player UI.
 *   onBathStop  — () => void, notifies parent that the bath was stopped from
 *                 within the panel (e.g. same-preset re-tap or Stop button).
 */
export default function SoundBathPanel({ isPro = true, onUnlock, onSaveBath, onBathStart, onBathStop }) {
  const bath = getSoundBath(audioEngine);
  const [snap, setSnap] = useState(() => bath.snapshot());
  const [saveState, setSaveState] = useState('idle');   // idle | saving | saved | error

  useEffect(() => bath.on((s) => setSnap(s)), [bath]);

  // Cleanly stop the bath if the parent unmounts (e.g. logout).
  useEffect(() => () => { if (snap.active) bath.stop({ hardCancel: true }); }, []);

  const clickPreset = async (key) => {
    if (!isPro) { onUnlock && onUnlock(); return; }
    // Same-preset click while active = stop. Otherwise start (which
    // internally stops any prior bath before launching the new arrangement).
    if (snap.active && snap.preset === key) {
      bath.stop();
      onBathStop && onBathStop();
    } else {
      if (!audioEngine.playing) {
        // Sound bath is a stand-alone experience — make sure the audio
        // context is unlocked. Calling start() with no other sources set
        // will simply idle the main oscillator at zero gain.
        try { await audioEngine.start(); } catch (e) { /* graceful */ }
      }
      await bath.start(key);
      onBathStart && onBathStart(key);
    }
  };

  const saveArrangement = async () => {
    if (!snap.active || !snap.preset || !onSaveBath) return;
    const preset = BATH_PRESETS[snap.preset];
    if (!preset) return;
    setSaveState('saving');
    try {
      await onSaveBath(snap.preset, preset.label);
      setSaveState('saved');
      setTimeout(() => setSaveState('idle'), 2400);
    } catch (e) {
      setSaveState('error');
      setTimeout(() => setSaveState('idle'), 2400);
    }
  };

  const entries = Object.entries(BATH_PRESETS);

  return (
    <div className={`glass p-5 relative ${!isPro ? 'overflow-hidden' : ''}`} data-testid="sound-bath-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Waves size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Sound Bath</div>
          {!isPro && <Lock size={11} className="text-[#C4A67A]" />}
        </div>
        {!isPro ? (
          <span
            data-testid="sound-bath-pro-badge"
            className="text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-0.5 rounded-full"
          >
            PRO
          </span>
        ) : snap.active ? (
          <div className="flex items-center gap-2 shrink-0">
            <button
              data-testid="sound-bath-save"
              onClick={saveArrangement}
              disabled={saveState === 'saving'}
              title="Save this arrangement"
              className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono text-[#C4A67A] hover:text-[#E8B872] disabled:opacity-50 transition-colors whitespace-nowrap"
            >
              {saveState === 'saved' ? (
                <><Check size={10} /> Saved</>
              ) : saveState === 'error' ? (
                <><Bookmark size={10} /> Retry</>
              ) : saveState === 'saving' ? (
                <><Bookmark size={10} /> Saving</>
              ) : (
                <><Bookmark size={10} /> Save</>
              )}
            </button>
            <button
              data-testid="sound-bath-stop-all"
              onClick={() => { bath.stop(); onBathStop && onBathStop(); }}
              className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono text-[#C4A67A] hover:text-[#E8B872] transition-colors whitespace-nowrap"
            >
              <Square size={10} /> Stop
            </button>
          </div>
        ) : null}
      </div>
      <p className="text-[11px] text-[#8A9A92] leading-relaxed mb-3">
        Algorithmic washes of crystal bowls, chimes, and gongs. Every tap arranges the notes anew — no two sessions sound identical.
      </p>
      <div className={`grid grid-cols-1 sm:grid-cols-2 gap-2 transition-opacity ${!isPro ? 'opacity-45 pointer-events-none select-none' : ''}`}>
        {entries.map(([key, p]) => {
          const isActive = snap.active && snap.preset === key;
          return (
            <button
              key={key}
              data-testid={`sound-bath-preset-${key}`}
              onClick={() => clickPreset(key)}
              className={`text-left p-3 rounded-xl border transition-colors ${
                isActive
                  ? 'border-[#72C2AC]/60 bg-[#5C9E8C]/15'
                  : 'border-[#5C9E8C]/20 bg-black/30 hover:border-[#72C2AC]/40 hover:bg-[#5C9E8C]/10'
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                {isActive ? (
                  <Sparkles size={12} className="text-[#72C2AC] shrink-0" />
                ) : (
                  <Play size={12} className="text-[#8A9A92] shrink-0" />
                )}
                <div className={`text-sm ${isActive ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>{p.label}</div>
                {isActive && (
                  <span
                    className="ml-auto text-[9px] uppercase tracking-widest font-mono text-[#72C2AC]"
                    data-testid={`sound-bath-active-badge-${key}`}
                  >
                    Playing
                  </span>
                )}
              </div>
              <div className="text-[11px] text-[#8A9A92] leading-relaxed">{p.description}</div>
            </button>
          );
        })}
      </div>

      {!isPro && (
        <button
          data-testid="sound-bath-unlock-cta"
          onClick={() => onUnlock && onUnlock()}
          className="absolute inset-0 flex items-end justify-center pb-5 px-5 cursor-pointer group"
        >
          <div className="glass-soft px-4 py-3 border border-[#C4A67A]/40 hover:border-[#C4A67A] hover:-translate-y-0.5 transition-all text-center w-full max-w-[280px]">
            <div className="flex items-center justify-center gap-2 text-[#C4A67A] text-xs font-medium">
              <Lock size={12} /> Included in Pro
            </div>
            <div className="text-[10px] text-[#8A9A92] mt-1">
              Unlock 7 algorithmic crystal-bowl, chime &amp; gong baths
            </div>
          </div>
        </button>
      )}
    </div>
  );
}
