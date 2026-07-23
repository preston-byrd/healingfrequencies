import React, { useEffect, useRef, useState } from 'react';
import { Lock, Flower2, Circle, Wind, Sparkles, Play, Square } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import hapticEngine from '@/lib/hapticEngine';
import { getSoundBath } from '@/lib/soundBathEngine';

/**
 * MeditationSoundsPanel — Pro-only panel with three organised tabs:
 *   1. Presets  — 6 curated procedural meditation mixes (Om Drone, Chakra
 *                 Alignment, Heart Coherence, Zen Garden, Cosmic Void, Deep
 *                 Silence). Each is a full audioEngine configuration applied
 *                 in one tap; countdown arms to 10 min like Sound Bath.
 *   2. Chakras  — 7 chakra tones (root → crown) with traditional Solfeggio
 *                 frequencies + colour-coded chips. Golden Stack layered
 *                 on so each chakra "blooms" harmonically.
 *   3. Breath   — 3 guided breath pacers (4-7-8, Box, Coherent) with
 *                 synced haptics + a visual breathing orb. The audio is a
 *                 soft baseline tone; the pacing is delivered by the orb
 *                 + haptic pulses so the user is never staring at numbers.
 *
 * Props:
 *   isPro       — bool. When false the whole panel is Pro-locked (identical
 *                 pattern to SoundBathPanel / Specials / Soundscapes).
 *   onUnlock    — () => void, opens Account paywall.
 *   onSessionStart — (label) => void, notifies Dashboard to arm the timer.
 *   onSessionStop  — () => void, notifies Dashboard to clear the timer +
 *                 stop the audio engine.
 */

const PRESETS = {
  om: {
    label: 'Om Drone',
    desc: '108 Hz sacred drone with harmonic overtones.',
    Icon: Circle,
    apply: () => {
      audioEngine.setFrequency(108);
      audioEngine.setWaveform('sine');
      audioEngine.setBinaural(0);
      audioEngine.setIsochronic(0);
      audioEngine.setGoldenStack(true);
      audioEngine.setAmbient('bowls', 0.28);
      audioEngine.setAmbient('brown', 0.10);
    },
  },
  chakra: {
    label: 'Chakra Alignment',
    desc: '528 Hz Miracle tone with φ Golden Stack.',
    Icon: Flower2,
    apply: () => {
      audioEngine.setFrequency(528);
      audioEngine.setWaveform('sine');
      audioEngine.setBinaural(0);
      audioEngine.setIsochronic(0);
      audioEngine.setGoldenStack(true);
      audioEngine.setAmbient('bowls', 0.18);
    },
  },
  heart: {
    label: 'Heart Coherence',
    desc: '528 Hz with slow 5.5 bpm breath pacer.',
    Icon: Sparkles,
    apply: () => {
      audioEngine.setFrequency(528);
      audioEngine.setWaveform('sine');
      audioEngine.setBinaural(0);
      audioEngine.setIsochronic(0);
      audioEngine.setGoldenStack(false);
      audioEngine.setAmbient('bowls', 0.12);
      audioEngine.setAmbient('ocean', 0.15);
    },
  },
  zen: {
    label: 'Zen Garden',
    desc: '174 Hz Foundation · forest · bowls.',
    Icon: Flower2,
    apply: () => {
      audioEngine.setFrequency(174);
      audioEngine.setWaveform('sine');
      audioEngine.setBinaural(0);
      audioEngine.setIsochronic(0);
      audioEngine.setGoldenStack(false);
      audioEngine.setAmbient('forest', 0.28);
      audioEngine.setAmbient('wind', 0.14);
      audioEngine.setAmbient('bowls', 0.10);
    },
  },
  cosmic: {
    label: 'Cosmic Void',
    desc: '963 Hz Unity floating on brown noise.',
    Icon: Sparkles,
    apply: () => {
      audioEngine.setFrequency(963);
      audioEngine.setWaveform('sine');
      audioEngine.setBinaural(0);
      audioEngine.setIsochronic(0);
      audioEngine.setGoldenStack(false);
      audioEngine.setAmbient('brown', 0.32);
      audioEngine.setAmbient('bowls', 0.10);
    },
  },
  silence: {
    label: 'Deep Silence',
    desc: 'Low 60 Hz drone under ocean waves.',
    Icon: Circle,
    apply: () => {
      audioEngine.setFrequency(60);
      audioEngine.setWaveform('sine');
      audioEngine.setBinaural(0);
      audioEngine.setIsochronic(0);
      audioEngine.setGoldenStack(false);
      audioEngine.setAmbient('ocean', 0.30);
      audioEngine.setAmbient('brown', 0.18);
    },
  },
};

// Traditional 7-chakra Solfeggio mapping. Colour hexes match the palette used
// across the app so we don't introduce a foreign palette (all warm-cool sage).
const CHAKRAS = [
  { key: 'root',     name: 'Root',         sanskrit: 'Muladhara',    hz: 396, color: '#D96C6C', desc: 'Grounding · safety · release fear' },
  { key: 'sacral',   name: 'Sacral',       sanskrit: 'Svadhisthana', hz: 417, color: '#E8B872', desc: 'Creativity · flow · undo change' },
  { key: 'solar',    name: 'Solar Plexus', sanskrit: 'Manipura',     hz: 528, color: '#F0D77A', desc: 'Confidence · DNA repair · miracles' },
  { key: 'heart',    name: 'Heart',        sanskrit: 'Anahata',      hz: 639, color: '#72C2AC', desc: 'Connection · love · relationships' },
  { key: 'throat',   name: 'Throat',       sanskrit: 'Vishuddha',    hz: 741, color: '#7AC5D6', desc: 'Expression · truth · awakening' },
  { key: 'thirdEye', name: 'Third Eye',    sanskrit: 'Ajna',         hz: 852, color: '#9BA5E8', desc: 'Intuition · insight · spiritual order' },
  { key: 'crown',    name: 'Crown',        sanskrit: 'Sahasrara',    hz: 963, color: '#C4A3E8', desc: 'Unity · pure being · connection' },
];

// Breath pacer definitions. Each pacer sets a soft baseline tone and picks
// the matching haptic pattern (respected only if the user has haptics on).
const BREATH_PACERS = {
  four_seven_eight: {
    label: '4-7-8 Breathing',
    desc: '4s inhale · 7s hold · 8s exhale. Deep relaxation.',
    hz: 174,
    pattern: 'breath478',
    cycleMs: 19000,
    // Orb radius keyframes (0..100 %). One full loop per cycle.
    orbKeyframes: [
      { at: 0.00, scale: 0.55 },
      { at: 0.21, scale: 1.00 },  // 4s inhale
      { at: 0.58, scale: 1.00 },  // 7s hold
      { at: 1.00, scale: 0.55 },  // 8s exhale
    ],
  },
  box: {
    label: 'Box Breathing',
    desc: '4-4-4-4. Navy SEAL focus cadence.',
    hz: 220,
    pattern: 'breathBox',
    cycleMs: 16000,
    orbKeyframes: [
      { at: 0.00, scale: 0.55 },
      { at: 0.25, scale: 1.00 },  // 4s inhale
      { at: 0.50, scale: 1.00 },  // 4s hold
      { at: 0.75, scale: 0.55 },  // 4s exhale
      { at: 1.00, scale: 0.55 },  // 4s hold empty
    ],
  },
  coherent: {
    label: 'Coherent Breathing',
    desc: '5.5 bpm — 5.5s in · 5.5s out. Boosts HRV.',
    hz: 285,
    pattern: 'breathCoherent',
    cycleMs: 11000,
    orbKeyframes: [
      { at: 0.00, scale: 0.55 },
      { at: 0.50, scale: 1.00 },  // 5.5s inhale
      { at: 1.00, scale: 0.55 },  // 5.5s exhale
    ],
  },
};

const TABS = [
  { key: 'presets',  label: 'Presets' },
  { key: 'chakras',  label: 'Chakras' },
  { key: 'breath',   label: 'Breath'  },
];

export default function MeditationSoundsPanel({ isPro = true, onUnlock, onSessionStart, onSessionStop }) {
  const [tab, setTab] = useState('presets');
  const [active, setActive] = useState(null); // { kind: 'preset'|'chakra'|'breath', key }
  const [orbScale, setOrbScale] = useState(0.55);
  const orbRafRef = useRef(null);
  const orbStartRef = useRef(0);
  const prevHapticPatternRef = useRef(null);

  const stopAll = () => {
    setActive(null);
    // Restore user's haptic pattern if we overrode it for a breath pacer.
    if (prevHapticPatternRef.current) {
      try { hapticEngine.setPattern(prevHapticPatternRef.current); } catch (e) { /* graceful */ }
      prevHapticPatternRef.current = null;
    }
    // Cancel orb animation.
    if (orbRafRef.current) {
      cancelAnimationFrame(orbRafRef.current);
      orbRafRef.current = null;
    }
    setOrbScale(0.55);
    onSessionStop && onSessionStop();
  };

  // Cleanup on unmount so a logout / navigation doesn't leave audio or haptics
  // in a stale state.
  useEffect(() => () => {
    if (active) {
      try { audioEngine.stop(); } catch (e) { /* graceful */ }
      if (prevHapticPatternRef.current) {
        try { hapticEngine.setPattern(prevHapticPatternRef.current); } catch (e) { /* graceful */ }
      }
      if (orbRafRef.current) cancelAnimationFrame(orbRafRef.current);
    }
  }, []);

  const startFresh = async () => {
    // Clean slate: kill any current bath + audio so meditation sounds don't
    // stack on top of a prior session.
    try { getSoundBath(audioEngine).stop(); } catch (e) { /* graceful */ }
    if (audioEngine.playing) audioEngine.stop();
    // Reset ambient layers to zero before applying new preset so we don't
    // accumulate rain from a prior soundscape into a Zen Garden mix.
    ['rain', 'ocean', 'forest', 'wind', 'crickets', 'bowls', 'brown', 'white'].forEach((k) => {
      try { audioEngine.setAmbient(k, 0); } catch (e) { /* graceful */ }
    });
    // Give the previous stop() 120 ms to let its toneGain fade finish so we
    // don't overlap oscillator starts.
    await new Promise((r) => setTimeout(r, 120));
  };

  const clickPreset = async (key) => {
    if (!isPro) { onUnlock && onUnlock(); return; }
    if (active && active.kind === 'preset' && active.key === key) {
      // Same-tile re-tap → stop everything.
      try { audioEngine.stop(); } catch (e) { /* graceful */ }
      stopAll();
      return;
    }
    await startFresh();
    PRESETS[key].apply();
    try { await audioEngine.start(); } catch (e) { /* graceful */ }
    setActive({ kind: 'preset', key });
    onSessionStart && onSessionStart(PRESETS[key].label);
  };

  const clickChakra = async (chakra) => {
    if (!isPro) { onUnlock && onUnlock(); return; }
    if (active && active.kind === 'chakra' && active.key === chakra.key) {
      try { audioEngine.stop(); } catch (e) { /* graceful */ }
      stopAll();
      return;
    }
    await startFresh();
    audioEngine.setFrequency(chakra.hz);
    audioEngine.setWaveform('sine');
    audioEngine.setBinaural(0);
    audioEngine.setIsochronic(0);
    audioEngine.setGoldenStack(true);
    audioEngine.setAmbient('bowls', 0.14);
    try { await audioEngine.start(); } catch (e) { /* graceful */ }
    setActive({ kind: 'chakra', key: chakra.key });
    onSessionStart && onSessionStart(`${chakra.name} Chakra`);
  };

  const clickBreath = async (pacerKey) => {
    if (!isPro) { onUnlock && onUnlock(); return; }
    const pacer = BREATH_PACERS[pacerKey];
    if (!pacer) return;
    if (active && active.kind === 'breath' && active.key === pacerKey) {
      try { audioEngine.stop(); } catch (e) { /* graceful */ }
      stopAll();
      return;
    }
    await startFresh();
    audioEngine.setFrequency(pacer.hz);
    audioEngine.setWaveform('sine');
    audioEngine.setBinaural(0);
    audioEngine.setIsochronic(0);
    audioEngine.setGoldenStack(false);
    audioEngine.setAmbient('bowls', 0.08);
    try { await audioEngine.start(); } catch (e) { /* graceful */ }

    // Swap the user's haptic pattern to the pacer's breath cadence — only if
    // the user already has haptics ON. Their existing preference is captured
    // so we can restore it when the pacer stops.
    const snap = hapticEngine.snapshot();
    if (snap.enabled && snap.supported) {
      prevHapticPatternRef.current = snap.pattern;
      try { hapticEngine.setPattern(pacer.pattern); } catch (e) { /* graceful */ }
    }

    // Start the visual breathing orb animation.
    orbStartRef.current = performance.now();
    const animate = (now) => {
      const elapsed = (now - orbStartRef.current) % pacer.cycleMs;
      const t = elapsed / pacer.cycleMs;
      // Piecewise-linear interpolation across the keyframe array.
      let s = pacer.orbKeyframes[0].scale;
      for (let i = 0; i < pacer.orbKeyframes.length - 1; i++) {
        const k0 = pacer.orbKeyframes[i];
        const k1 = pacer.orbKeyframes[i + 1];
        if (t >= k0.at && t <= k1.at) {
          const span = k1.at - k0.at || 1;
          const localT = (t - k0.at) / span;
          s = k0.scale + (k1.scale - k0.scale) * localT;
          break;
        }
      }
      setOrbScale(s);
      orbRafRef.current = requestAnimationFrame(animate);
    };
    orbRafRef.current = requestAnimationFrame(animate);

    setActive({ kind: 'breath', key: pacerKey });
    onSessionStart && onSessionStart(pacer.label);
  };

  return (
    <div className={`glass p-5 relative ${!isPro ? 'overflow-hidden' : ''}`} data-testid="meditation-sounds-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Flower2 size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Meditation Sounds</div>
          {!isPro && <Lock size={11} className="text-[#C4A67A]" />}
        </div>
        {!isPro ? (
          <span
            data-testid="meditation-pro-badge"
            className="text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-0.5 rounded-full"
          >
            PRO
          </span>
        ) : active ? (
          <button
            data-testid="meditation-stop-all"
            onClick={() => { try { audioEngine.stop(); } catch (e) { /* graceful */ } stopAll(); }}
            className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono text-[#C4A67A] hover:text-[#E8B872] transition-colors whitespace-nowrap"
          >
            <Square size={10} /> Stop
          </button>
        ) : null}
      </div>

      {/* Tab strip */}
      <div className={`flex gap-1 mb-3 p-1 rounded-lg bg-black/25 border border-[#5C9E8C]/15 ${!isPro ? 'opacity-45 pointer-events-none' : ''}`}>
        {TABS.map((t) => (
          <button
            key={t.key}
            data-testid={`meditation-tab-${t.key}`}
            onClick={() => setTab(t.key)}
            className={`flex-1 text-[10px] uppercase tracking-widest font-mono py-1.5 rounded-md transition-colors ${
              tab === t.key ? 'bg-[#5C9E8C]/25 text-[#72C2AC]' : 'text-[#8A9A92] hover:text-[#E8E3D9]'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className={`transition-opacity ${!isPro ? 'opacity-45 pointer-events-none select-none' : ''}`}>
        {tab === 'presets' && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {Object.entries(PRESETS).map(([key, p]) => {
              const isActive = active && active.kind === 'preset' && active.key === key;
              const Icon = p.Icon;
              return (
                <button
                  key={key}
                  data-testid={`meditation-preset-${key}`}
                  onClick={() => clickPreset(key)}
                  className={`text-left p-3 rounded-xl border transition-colors ${
                    isActive
                      ? 'border-[#72C2AC]/60 bg-[#5C9E8C]/15'
                      : 'border-[#5C9E8C]/20 bg-black/30 hover:border-[#72C2AC]/40 hover:bg-[#5C9E8C]/10'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    {isActive ? <Sparkles size={12} className="text-[#72C2AC] shrink-0" /> : <Icon size={12} className="text-[#8A9A92] shrink-0" />}
                    <div className={`text-sm ${isActive ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>{p.label}</div>
                    {isActive && (
                      <span
                        className="ml-auto text-[9px] uppercase tracking-widest font-mono text-[#72C2AC]"
                        data-testid={`meditation-active-${key}`}
                      >
                        Playing
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-[#8A9A92] leading-relaxed">{p.desc}</div>
                </button>
              );
            })}
          </div>
        )}

        {tab === 'chakras' && (
          <div className="space-y-2">
            {CHAKRAS.map((c) => {
              const isActive = active && active.kind === 'chakra' && active.key === c.key;
              return (
                <button
                  key={c.key}
                  data-testid={`meditation-chakra-${c.key}`}
                  onClick={() => clickChakra(c)}
                  className={`w-full text-left p-3 rounded-xl border transition-colors flex items-center gap-3 ${
                    isActive
                      ? 'border-[#72C2AC]/60 bg-[#5C9E8C]/15'
                      : 'border-[#5C9E8C]/20 bg-black/30 hover:border-[#72C2AC]/40 hover:bg-[#5C9E8C]/10'
                  }`}
                >
                  <span
                    className="shrink-0 w-3 h-3 rounded-full"
                    style={{ backgroundColor: c.color, boxShadow: `0 0 10px ${c.color}80` }}
                    aria-hidden
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2">
                      <div className={`text-sm ${isActive ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>{c.name}</div>
                      <div className="text-[10px] font-mono text-[#8A9A92]">{c.sanskrit}</div>
                      <div className="ml-auto text-[11px] font-mono text-[#C4A67A]">{c.hz} Hz</div>
                    </div>
                    <div className="text-[11px] text-[#8A9A92] leading-relaxed">{c.desc}</div>
                  </div>
                  {isActive && (
                    <Sparkles size={12} className="text-[#72C2AC] shrink-0" data-testid={`meditation-active-${c.key}`} />
                  )}
                </button>
              );
            })}
          </div>
        )}

        {tab === 'breath' && (
          <div className="space-y-3">
            {/* Breathing orb — only visible while a pacer is active */}
            {active && active.kind === 'breath' && (
              <div
                data-testid="meditation-breath-orb"
                className="flex items-center justify-center h-32 relative"
              >
                <div
                  className="rounded-full transition-all"
                  style={{
                    width: `${Math.round(orbScale * 96)}px`,
                    height: `${Math.round(orbScale * 96)}px`,
                    background: 'radial-gradient(circle, #72C2AC 0%, #5C9E8C 55%, transparent 80%)',
                    boxShadow: `0 0 ${Math.round(orbScale * 40)}px #72C2AC80`,
                    // Snap CSS transition off — the JS RAF loop drives the value.
                    transition: 'width 60ms linear, height 60ms linear, box-shadow 60ms linear',
                  }}
                />
                <div
                  className="absolute bottom-1 text-[9px] tracking-widest uppercase font-mono text-[#8A9A92]"
                  data-testid="meditation-breath-cue"
                >
                  {orbScale > 0.85 ? 'Hold / In' : orbScale < 0.65 ? 'Exhale' : 'Follow the orb'}
                </div>
              </div>
            )}
            {Object.entries(BREATH_PACERS).map(([key, b]) => {
              const isActive = active && active.kind === 'breath' && active.key === key;
              return (
                <button
                  key={key}
                  data-testid={`meditation-breath-${key}`}
                  onClick={() => clickBreath(key)}
                  className={`w-full text-left p-3 rounded-xl border transition-colors ${
                    isActive
                      ? 'border-[#72C2AC]/60 bg-[#5C9E8C]/15'
                      : 'border-[#5C9E8C]/20 bg-black/30 hover:border-[#72C2AC]/40 hover:bg-[#5C9E8C]/10'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    {isActive ? <Sparkles size={12} className="text-[#72C2AC] shrink-0" /> : <Wind size={12} className="text-[#8A9A92] shrink-0" />}
                    <div className={`text-sm ${isActive ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>{b.label}</div>
                    {isActive && (
                      <span
                        className="ml-auto text-[9px] uppercase tracking-widest font-mono text-[#72C2AC]"
                        data-testid={`meditation-active-${key}`}
                      >
                        Playing
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-[#8A9A92] leading-relaxed">{b.desc}</div>
                </button>
              );
            })}
            <div className="text-[10px] text-[#8A9A92] leading-relaxed pt-1">
              Tip: enable Pulsing Haptics (Account → Haptics) for a synced buzz on each breath phase.
            </div>
          </div>
        )}
      </div>

      {!isPro && (
        <button
          data-testid="meditation-unlock-cta"
          onClick={() => onUnlock && onUnlock()}
          className="absolute inset-0 flex items-end justify-center pb-5 px-5 cursor-pointer group"
        >
          <div className="glass-soft px-4 py-3 border border-[#C4A67A]/40 hover:border-[#C4A67A] hover:-translate-y-0.5 transition-all text-center w-full max-w-[280px]">
            <div className="flex items-center justify-center gap-2 text-[#C4A67A] text-xs font-medium">
              <Lock size={12} /> Included in Pro
            </div>
            <div className="text-[10px] text-[#8A9A92] mt-1">
              Curated meditations, 7 chakras &amp; guided breath pacers
            </div>
          </div>
        </button>
      )}
    </div>
  );
}
