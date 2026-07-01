import React, { useEffect, useState } from 'react';
import { Waves, Sparkles, Play, Square } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import { BATH_PRESETS, getSoundBath } from '@/lib/soundBathEngine';

/**
 * SoundBathPanel — collapsible panel with 7 sound-bath presets. Each preset
 * fires the algorithmic scheduler in `soundBathEngine` — every click starts a
 * fresh randomised arrangement (different notes / timing / pan / velocity
 * pattern) while still adhering to the preset's scale + voice mix.
 */
export default function SoundBathPanel() {
  const bath = getSoundBath(audioEngine);
  const [snap, setSnap] = useState(() => bath.snapshot());

  useEffect(() => bath.on((s) => setSnap(s)), [bath]);

  // Cleanly stop the bath if the parent unmounts (e.g. logout).
  useEffect(() => () => { if (snap.active) bath.stop({ hardCancel: true }); }, []);

  const clickPreset = async (key) => {
    // Same-preset click while active = stop. Otherwise start (which
    // internally stops any prior bath before launching the new arrangement).
    if (snap.active && snap.preset === key) {
      bath.stop();
    } else {
      if (!audioEngine.playing) {
        // Sound bath is a stand-alone experience — make sure the audio
        // context is unlocked. Calling start() with no other sources set
        // will simply idle the main oscillator at zero gain.
        try { await audioEngine.start(); } catch (e) { /* graceful */ }
      }
      await bath.start(key);
    }
  };

  const entries = Object.entries(BATH_PRESETS);

  return (
    <div className="glass p-5" data-testid="sound-bath-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Waves size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Sound Bath</div>
        </div>
        {snap.active && (
          <button
            data-testid="sound-bath-stop-all"
            onClick={() => bath.stop()}
            className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono text-[#C4A67A] hover:text-[#E8B872] transition-colors"
          >
            <Square size={10} /> Stop
          </button>
        )}
      </div>
      <p className="text-[11px] text-[#8A9A92] leading-relaxed mb-3">
        Algorithmic washes of crystal bowls, chimes, and gongs. Every tap arranges the notes anew — no two sessions sound identical.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
    </div>
  );
}
