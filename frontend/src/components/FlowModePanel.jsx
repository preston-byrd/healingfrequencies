import React, { useEffect, useState } from 'react';
import { Sunrise, Heart, Moon, Play, Square, Sparkles, Lock, Plus } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import { getFlowEngine, JOURNEYS, DURATION_OPTIONS } from '@/lib/flowEngine';
import { getSoundBath } from '@/lib/soundBathEngine';

/**
 * FlowModePanel — guided 3-stage frequency journey with even-time crossfades.
 *
 * Sits under the Solfeggio presets in the left column. Free tier can use the
 * three pre-built journeys (Morning Rise · Deep Restore · Night Drift); Pro
 * can additionally build a Custom Flow from any 3 Solfeggio frequencies +
 * pick their own duration.
 *
 * Props:
 *   isPro          — bool; unlocks the Custom Flow builder.
 *   solfeggioList  — [{hz, label, sub}] used to populate the Custom picker.
 *   onFlowStart    — (totalMin) => void, notifies Dashboard to arm the
 *                    countdown timer to totalMin minutes.
 *   onFlowStop     — () => void, notifies Dashboard to clear the timer +
 *                    stop the audio engine.
 *   onStageChange  — (stageIdx, stageMeta) => void, notifies Dashboard so
 *                    the central visualiser can display the current
 *                    frequency name + progress dots. Called on every stage
 *                    transition (0, 1, 2) plus when the flow ends.
 *   onUnlock       — () => void, opens the Account paywall for free users
 *                    who tap the Custom Flow builder.
 */
const PRESET_ICONS = {
  morning_rise: Sunrise,
  deep_restore: Heart,
  night_drift:  Moon,
};

export default function FlowModePanel({
  isPro = true,
  solfeggioList = [],
  onFlowStart,
  onFlowStop,
  onStageChange,
  onUnlock,
}) {
  const flow = getFlowEngine(audioEngine);
  const [snap, setSnap] = useState(() => flow.snapshot());
  const [duration, setDuration] = useState(30); // minutes
  const [customBuilding, setCustomBuilding] = useState(false);
  const [customPicks, setCustomPicks] = useState([]); // [{hz, label, sub}]

  useEffect(() => flow.on((s) => {
    setSnap(s);
    onStageChange && onStageChange(s.stageIdx, s.journey?.stages?.[s.stageIdx] || null);
  }), [flow, onStageChange]);

  const isActive = snap.active;
  const activeKey = snap.journey?.key;

  const startJourney = async (journey, mins) => {
    // Stop any Sound Bath / Meditation session so the flow starts clean.
    try { getSoundBath(audioEngine).stop(); } catch (e) { /* graceful */ }
    if (audioEngine.playing) audioEngine.stop();
    // 120 ms breather so the previous stop-ramp settles before the flow's
    // entry-fade kicks in.
    await new Promise((r) => setTimeout(r, 120));
    await flow.start(journey, mins);
    // Pass the journey descriptor along so Dashboard can label the
    // post-session share card with the exact flow the user just completed
    // (e.g. "Deep Restore Journey · 60 min"). Older call-sites that only
    // consume `mins` remain compatible thanks to positional arity.
    onFlowStart && onFlowStart(mins, journey);
  };

  const clickJourney = (key) => {
    const journey = JOURNEYS[key];
    if (!journey) return;
    if (isActive && activeKey === key) {
      flow.stop();
      onFlowStop && onFlowStop();
      return;
    }
    startJourney(journey, duration);
  };

  const openCustomBuilder = () => {
    if (!isPro) { onUnlock && onUnlock(); return; }
    setCustomBuilding(true);
    setCustomPicks([]);
  };

  const togglePick = (item) => {
    setCustomPicks((prev) => {
      const idx = prev.findIndex((p) => p.hz === item.hz);
      if (idx >= 0) return prev.filter((_, i) => i !== idx);
      if (prev.length >= 3) return prev; // hard cap at 3
      return [...prev, item];
    });
  };

  const startCustom = () => {
    if (customPicks.length !== 3) return;
    const journey = {
      key: 'custom',
      label: 'Custom Flow',
      description: 'Your personalised 3-stage journey.',
      stages: customPicks.map((p) => ({ hz: p.hz, name: p.label, sub: p.sub || '' })),
    };
    startJourney(journey, duration);
    setCustomBuilding(false);
  };

  return (
    <div className="glass p-4" data-testid="flow-mode-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Flow Mode</div>
        </div>
        {isActive ? (
          <button
            data-testid="flow-stop"
            onClick={() => { flow.stop(); onFlowStop && onFlowStop(); }}
            className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono text-[#C4A67A] hover:text-[#E8B872] transition-colors"
          >
            <Square size={10} /> Stop
          </button>
        ) : (
          <div className="flex items-center gap-1 text-[10px] font-mono" data-testid="flow-duration-picker">
            {DURATION_OPTIONS.map((m) => (
              <button
                key={m}
                data-testid={`flow-duration-${m}`}
                onClick={() => setDuration(m)}
                className={`px-2 py-0.5 rounded-md tracking-widest transition-colors ${
                  duration === m
                    ? 'bg-[#5C9E8C]/25 text-[#72C2AC] border border-[#5C9E8C]/40'
                    : 'text-[#8A9A92] hover:text-[#E8E3D9] border border-transparent'
                }`}
              >
                {m}m
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Progress dots — only when active. Three horizontal pills; the current
          stage fills to the current progress within that stage. */}
      {isActive && (
        <div className="flex items-center gap-1.5 mb-3" data-testid="flow-progress">
          {[0, 1, 2].map((i) => {
            const done = i < snap.stageIdx;
            const current = i === snap.stageIdx;
            return (
              <div
                key={i}
                className={`h-1 flex-1 rounded-full transition-colors ${
                  done ? 'bg-[#72C2AC]' : current ? 'bg-[#72C2AC]/60' : 'bg-[#5C9E8C]/15'
                }`}
                data-testid={`flow-progress-${i}`}
              />
            );
          })}
        </div>
      )}
      {isActive && snap.journey && (
        <div className="text-[10px] font-mono text-[#8A9A92] mb-3 text-center">
          {snap.journey.label} · stage {(snap.stageIdx || 0) + 1} of 3
        </div>
      )}

      {!customBuilding && (
        <div className="space-y-2">
          {Object.values(JOURNEYS).map((j) => {
            const Icon = PRESET_ICONS[j.key] || Sparkles;
            const active = isActive && activeKey === j.key;
            return (
              <button
                key={j.key}
                data-testid={`flow-journey-${j.key}`}
                onClick={() => clickJourney(j.key)}
                className={`w-full text-left p-3 rounded-xl border transition-colors ${
                  active
                    ? 'border-[#72C2AC]/60 bg-[#5C9E8C]/15'
                    : 'border-[#5C9E8C]/20 bg-black/30 hover:border-[#72C2AC]/40 hover:bg-[#5C9E8C]/10'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Icon size={13} className={active ? 'text-[#72C2AC]' : 'text-[#8A9A92]'} />
                  <div className={`text-sm ${active ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>{j.label}</div>
                  {active && (
                    <span
                      className="ml-auto text-[9px] uppercase tracking-widest font-mono text-[#72C2AC]"
                      data-testid={`flow-active-${j.key}`}
                    >
                      Playing
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-[#8A9A92] leading-snug mb-1">{j.description}</div>
                <div className="text-[10px] font-mono text-[#5A6B65] flex gap-1.5 items-center">
                  <span>{j.stages[0].hz}Hz</span>
                  <span className="opacity-50">→</span>
                  <span>{j.stages[1].hz}Hz</span>
                  <span className="opacity-50">→</span>
                  <span>{j.stages[2].hz}Hz</span>
                </div>
              </button>
            );
          })}

          {/* Custom Flow — Pro only. Free users see the row but tap routes to paywall. */}
          <button
            data-testid="flow-open-custom"
            onClick={openCustomBuilder}
            className="w-full text-left p-3 rounded-xl border border-dashed border-[#C4A67A]/30 bg-black/25 hover:border-[#C4A67A]/60 hover:bg-[#C4A67A]/5 transition-colors"
          >
            <div className="flex items-center gap-2 mb-1">
              {isPro ? <Plus size={13} className="text-[#C4A67A]" /> : <Lock size={13} className="text-[#C4A67A]" />}
              <div className="text-sm text-[#E8E3D9]">Custom Flow</div>
              {!isPro && (
                <span
                  className="ml-auto text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-0.5 rounded-full"
                  data-testid="flow-custom-pro-badge"
                >
                  PRO
                </span>
              )}
            </div>
            <div className="text-[11px] text-[#8A9A92] leading-snug">
              Pick any 3 Solfeggio frequencies for your own journey.
            </div>
          </button>
        </div>
      )}

      {customBuilding && (
        <div data-testid="flow-custom-builder">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[11px] font-mono text-[#C4A67A]">Pick 3 frequencies · {customPicks.length}/3</div>
            <button
              data-testid="flow-custom-cancel"
              onClick={() => { setCustomBuilding(false); setCustomPicks([]); }}
              className="text-[10px] uppercase tracking-wider font-mono text-[#8A9A92] hover:text-[#E8E3D9] transition-colors"
            >
              Cancel
            </button>
          </div>
          <div className="grid grid-cols-2 gap-1.5 mb-3 max-h-52 overflow-y-auto custom-scrollbar pr-1">
            {solfeggioList.map((item) => {
              const pickedIdx = customPicks.findIndex((p) => p.hz === item.hz);
              const picked = pickedIdx >= 0;
              return (
                <button
                  key={item.hz}
                  data-testid={`flow-custom-pick-${item.hz}`}
                  onClick={() => togglePick(item)}
                  disabled={!picked && customPicks.length >= 3}
                  className={`text-left p-2 rounded-lg border transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    picked
                      ? 'border-[#72C2AC]/60 bg-[#5C9E8C]/15'
                      : 'border-[#5C9E8C]/20 bg-black/30 hover:border-[#72C2AC]/40'
                  }`}
                >
                  <div className={`text-[13px] font-mono ${picked ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>
                    {item.hz}<span className="text-[9px] ml-0.5">Hz</span>
                    {picked && (
                      <span className="ml-2 text-[9px] uppercase tracking-widest text-[#72C2AC]">
                        {pickedIdx + 1}
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-[#8A9A92] truncate">{item.label}</div>
                </button>
              );
            })}
          </div>
          <button
            data-testid="flow-custom-start"
            onClick={startCustom}
            disabled={customPicks.length !== 3}
            className="w-full py-2.5 rounded-lg bg-[#5C9E8C]/25 hover:bg-[#5C9E8C]/40 border border-[#72C2AC]/50 hover:border-[#72C2AC] text-[#72C2AC] text-sm font-medium tracking-wide transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Play size={12} className="inline mr-1" /> Start {duration}-min Custom Flow
          </button>
        </div>
      )}
    </div>
  );
}
