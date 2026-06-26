import React, { useEffect, useMemo, useState } from 'react';
import { Play, Pause, Save, Trash2, LogOut, Wind, Droplet, Waves, Trees, Volume2, Sparkles, UserCircle, Lock, Bug, CloudRain, Music, Moon, Brain, Layers, Sunrise, Cloud, Heart, Globe } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import api, { formatApiError } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { useSubscription } from '@/contexts/SubscriptionContext';
import Visualizer from '@/components/Visualizer';
import Breathwork from '@/components/Breathwork';
import StreakPanel from '@/components/StreakPanel';

const SOLFEGGIO = [
  { hz: 174, name: 'Foundation', desc: 'Pain relief' },
  { hz: 285, name: 'Healing', desc: 'Tissue restore' },
  { hz: 396, name: 'Liberation', desc: 'Release fear' },
  { hz: 417, name: 'Renewal', desc: 'Undo change' },
  { hz: 432, name: 'Earth', desc: 'Natural tuning' },
  { hz: 528, name: 'Miracle', desc: 'DNA repair' },
  { hz: 639, name: 'Connection', desc: 'Relationships' },
  { hz: 741, name: 'Awakening', desc: 'Expression' },
  { hz: 852, name: 'Intuition', desc: 'Spiritual order' },
  { hz: 963, name: 'Unity', desc: 'Pure being' },
];

const SPECIALS = [
  { hz: 2,    name: 'Delta',     desc: 'Deep sleep' },
  { hz: 6,    name: 'Theta',     desc: 'Meditation' },
  { hz: 7.83, name: 'Schumann',  desc: 'Earth pulse' },
  { hz: 10,   name: 'Alpha',     desc: 'Relaxation' },
  { hz: 40,   name: 'Gamma',     desc: 'Focus' },
  { hz: 111,  name: '111 Hz',    desc: 'Cellular' },
  { hz: 222,  name: '222 Hz',    desc: 'Alignment' },
  { hz: 369,  name: 'Tesla',     desc: '3·6·9 key' },
  { hz: 444,  name: 'Angel',     desc: 'Higher self' },
  { hz: 1111, name: '1111 Hz',   desc: 'Manifestation' },
];

const WAVEFORMS = ['sine', 'triangle', 'square', 'sawtooth'];
const PHI = 1.6180339887;
const GOLDEN_BASE = 144; // Fibonacci number; 144 × φ ≈ 233 (Fib); × φ² ≈ 377 (Fib). Creates a pure golden chord.

const AMBIENT = [
  { key: 'rain', label: 'Rain', Icon: Droplet },
  { key: 'ocean', label: 'Ocean', Icon: Waves },
  { key: 'forest', label: 'Forest', Icon: Trees },
  { key: 'wind', label: 'Wind', Icon: Wind },
  { key: 'crickets', label: 'Crickets', Icon: Bug },
  { key: 'bowls', label: 'Singing Bowls', Icon: Music },
  { key: 'brown', label: 'Brown Noise', Icon: CloudRain },
  { key: 'white', label: 'White Noise', Icon: Sparkles },
];

// Curated multi-layer mixes — one tap applies frequency + ambient blend. Pro-gated.
const SOUNDSCAPES = [
  { key: 'forest-rain', name: 'Forest Rain', desc: 'Theta 6Hz · forest · rain', freq: 6, Icon: Trees,
    ambient: { forest: 0.6, rain: 0.35 } },
  { key: 'cosmic-drift', name: 'Cosmic Drift', desc: '963Hz Unity · ocean · bowls', freq: 963, Icon: Sparkles,
    ambient: { ocean: 0.4, bowls: 0.5 } },
  { key: 'deep-sleep', name: 'Deep Sleep', desc: 'Delta 2Hz · brown · ocean', freq: 2, Icon: Moon,
    ambient: { brown: 0.5, ocean: 0.25 } },
  { key: 'morning-focus', name: 'Morning Focus', desc: 'Gamma 40Hz · crickets · wind', freq: 40, Icon: Sunrise,
    ambient: { crickets: 0.2, wind: 0.25 } },
  { key: 'heart-open', name: 'Heart Open', desc: '528Hz Miracle · bowls · wind', freq: 528, Icon: Heart,
    ambient: { bowls: 0.35, wind: 0.2 } },
  { key: 'earth-pulse', name: 'Earth Pulse', desc: 'Schumann 7.83 · forest · brown', freq: 7.83, Icon: Globe,
    ambient: { forest: 0.4, brown: 0.3 } },
];

// Pro feature index for the "What's in Pro" banner row
const PRO_PREVIEW = [
  { label: 'Brainwave & Specials', Icon: Brain },
  { label: 'φ Golden Stack', Icon: Sparkles },
  { label: 'Sleep Mode', Icon: Moon },
  { label: 'Ambient Layers', Icon: Layers },
  { label: 'Soundscapes', Icon: Cloud },
];

function formatTime(secs) {
  const m = Math.floor(secs / 60).toString().padStart(2, '0');
  const s = Math.floor(secs % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

export default function Dashboard({ onOpenAccount }) {
  const { user, logout } = useAuth();
  const { isPro, refresh: refreshSub, sub } = useSubscription();
  const [state, setState] = useState(audioEngine.getState());
  const [duration, setDuration] = useState(10); // minutes
  const [remaining, setRemaining] = useState(0); // seconds; 0 = not running
  const [breathwork, setBreathwork] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [saveName, setSaveName] = useState('');
  const [err, setErr] = useState('');
  const [streakBump, setStreakBump] = useState(0);
  const [sessionStart, setSessionStart] = useState(null);
  const [checkedInThisRun, setCheckedInThisRun] = useState(false);
  const [sleepMode, setSleepMode] = useState(false);
  // Soundscape selection — set when the user taps a curated mix, cleared on
  // explicit reset (tap-toggle, swap, full stop, sleep mode, timer end).
  // Persisted through Pause/Resume so the visual indicator survives a pause.
  const [activeSoundscape, setActiveSoundscape] = useState(null);
  // Track restore lifecycle: restoreStartedRef prevents re-entry of the restore
  // effect (it's set synchronously when restore begins); prefsRestoredRef gates
  // the auto-save effect (set only when restore completes, so auto-save can't
  // fire and clobber prefs while restoration is in-flight).
  const restoreStartedRef = React.useRef(false);
  const prefsRestoredRef = React.useRef(false);
  const prefsSaveTimerRef = React.useRef(null);

  const SLEEP_FADE_SECONDS = 60;
  const SLEEP_DURATION_MIN = 30;

  const checkIn = async (minutes) => {
    try {
      await api.post('/streak/checkin', { minutes });
      setStreakBump((n) => n + 1);
    } catch (e) { console.warn('[Dashboard] streak check-in failed', e); }
  };

  useEffect(() => audioEngine.on(setState), []);

  // Test-only hook: expose the audioEngine singleton so Playwright/E2E can
  // introspect engine state (playing, ambient gains, _pendingAmbient) for
  // soundscape fade verification. Harmless in production.
  useEffect(() => { try { window.__audioEngine = audioEngine; } catch (e) { /* noop */ } }, []);

  // Unlock AudioContext on the very first user gesture anywhere on the dashboard.
  // Essential for iOS Safari which keeps the context suspended until a tap.
  // Listens for the broadest set of "first gesture" events to catch every device.
  useEffect(() => {
    const unlock = () => audioEngine.unlock();
    const opts = { once: true, passive: true, capture: true };
    const events = ['pointerdown', 'touchstart', 'touchend', 'click', 'keydown'];
    events.forEach((ev) => window.addEventListener(ev, unlock, opts));
    return () => {
      events.forEach((ev) => window.removeEventListener(ev, unlock, opts));
    };
  }, []);

  // Drift-resistant timer: derive remaining from wall-clock end timestamp instead of
  // Pre-built timer effect: wall-clock-based to survive background-tab throttling.
  // Runs once per playback session; the deps capture only state.playing because
  // decrementing — survives background-tab throttling and JS interval jitter.
  useEffect(() => {
    if (!state.playing || remaining <= 0) return;
    const endAt = Date.now() + remaining * 1000;
    const tick = () => {
      const secsLeft = Math.max(0, Math.round((endAt - Date.now()) / 1000));
      if (secsLeft <= 0) {
        // Timer expired: graceful stop with no resume snapshot, and clear any
        // soundscape selection so the next user action starts fresh.
        audioEngine._pendingAmbient = null;
        audioEngine.stop();
        setRemaining(0);
        setActiveSoundscape(null);
      } else {
        setRemaining(secsLeft);
      }
    };
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [state.playing]);

  // Background/foreground sync: when tab becomes visible, resume the AudioContext
  // (Chrome auto-suspends it when hidden) so playback continues cleanly.
  useEffect(() => {
    const onVis = () => {
      if (!document.hidden && audioEngine.ctx && audioEngine.ctx.state !== 'running') {
        audioEngine.ctx.resume().catch(() => { /* noop */ });
      }
    };
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, []);

  // Sleep Mode: fade everything out over SLEEP_FADE_SECONDS at the end
  useEffect(() => {
    if (sleepMode && state.playing && remaining === SLEEP_FADE_SECONDS) {
      audioEngine.fadeOutAll(SLEEP_FADE_SECONDS);
    }
  }, [remaining, sleepMode, state.playing]);

  // Auto-clear sleepMode only when the session has actually completed
  // (NOT when audio is briefly idle during startSleepMode's setTimeout window).
  useEffect(() => {
    if (sleepMode && !state.playing && remaining === 0) setSleepMode(false);
  }, [state.playing, sleepMode, remaining]);

  // Auto check-in: when user has been playing for >= 60s in this run, record it once.
  useEffect(() => {
    if (state.playing && !sessionStart) {
      setSessionStart(Date.now());
      setCheckedInThisRun(false);
    }
    if (!state.playing && sessionStart) {
      const minutes = (Date.now() - sessionStart) / 60000;
      if (minutes >= 1 && !checkedInThisRun) {
        checkIn(minutes);
        setCheckedInThisRun(true);
      }
      setSessionStart(null);
    }
  }, [state.playing]);

  // Continuous check (covers timer auto-stop at 0): also check-in once threshold crossed mid-run
  useEffect(() => {
    if (!state.playing || !sessionStart || checkedInThisRun) return;
    const id = setTimeout(() => {
      const minutes = (Date.now() - sessionStart) / 60000;
      if (minutes >= 1) { checkIn(minutes); setCheckedInThisRun(true); }
    }, 60_000);
    return () => clearTimeout(id);
  }, [state.playing, sessionStart, checkedInThisRun]);

  // Fetch saved sessions
  useEffect(() => { refreshSessions(); }, []);
  const refreshSessions = async () => {
    try { const { data } = await api.get('/sessions'); setSessions(data); } catch (e) { console.warn('[Dashboard] sessions fetch failed', e); }
  };

  // Restore last-used config on mount — frequency, duration, ambient mix, etc.
  // Waits for /me/subscription to resolve (sub !== null) so the isPro check is
  // correct for the user (otherwise a Pro user who just logged in would lose
  // their golden/ambient/breathwork because isPro is still false during the
  // brief window between auth and subscription resolution).
  useEffect(() => {
    if (sub === null) return;                // wait until subscription has loaded
    if (restoreStartedRef.current) return;   // run exactly once per session
    restoreStartedRef.current = true;
    (async () => {
      try {
        const { data } = await api.get('/me/prefs');
        if (!data || typeof data !== 'object') return;
        if (typeof data.frequency === 'number') audioEngine.setFrequency(data.frequency);
        if (typeof data.duration_minutes === 'number') setDuration(data.duration_minutes);
        if (typeof data.waveform === 'string') audioEngine.setWaveform(data.waveform);
        if (typeof data.tone_volume === 'number') audioEngine.setToneVolume(data.tone_volume);
        if (isPro) {
          if (typeof data.binaural === 'number') audioEngine.setBinaural(data.binaural);
          if (typeof data.golden_stack === 'boolean') audioEngine.setGoldenStack(data.golden_stack);
          if (typeof data.breathwork === 'boolean') setBreathwork(data.breathwork);
          if (data.ambient && typeof data.ambient === 'object') {
            Object.entries(data.ambient).forEach(([k, v]) => {
              if (typeof v === 'number') audioEngine.setAmbient(k, v);
            });
          }
        }
      } catch (e) {
        console.warn('[Dashboard] prefs restore failed', e);
      } finally {
        // Open the gate for the debounced auto-save effect.
        prefsRestoredRef.current = true;
      }
    })();
  }, [sub, isPro]);

  // Debounced auto-save of prefs whenever the user changes a config knob.
  // 1.2s window — coalesces rapid slider drags into a single PUT.
  useEffect(() => {
    if (!prefsRestoredRef.current) return;
    if (prefsSaveTimerRef.current) clearTimeout(prefsSaveTimerRef.current);
    prefsSaveTimerRef.current = setTimeout(() => {
      // Always-allowed (non-Pro) fields.
      const payload = {
        frequency: state.frequency,
        duration_minutes: duration,
        waveform: state.waveform,
        tone_volume: state.toneVolume,
      };
      // Pro-only fields: only include in the PUT when the user is Pro. If they
      // aren't, the backend partial-merge (PUT /me/prefs uses model_dump
      // exclude_none + dotted-path $set) preserves whatever the user had saved
      // when they WERE Pro — so prefs survive a downgrade or trial-expiry round-trip.
      if (isPro) {
        payload.binaural = state.binaural;
        payload.golden_stack = !!state.goldenStack;
        payload.breathwork = !!breathwork;
        payload.ambient = state.ambient && { ...state.ambient };
      }
      api.put('/me/prefs', payload).catch((e) => console.warn('[Dashboard] prefs save failed', e));
    }, 1200);
    return () => { if (prefsSaveTimerRef.current) clearTimeout(prefsSaveTimerRef.current); };
  }, [state.frequency, state.waveform, state.binaural, state.toneVolume, state.goldenStack, state.ambient, duration, breathwork, isPro]);

  const togglePlay = () => {
    if (!audioEngine.playing) {
      setRemaining(duration * 60);
      audioEngine.start();
    } else {
      audioEngine.stop();
      setRemaining(0);
    }
  };

  const selectFrequency = (hz, opts = {}) => {
    const wantGolden = !!opts.golden;
    const wantSpecial = !!opts.special;
    if ((wantGolden || wantSpecial) && !isPro) { onOpenAccount(); return; }
    // Tap-to-toggle: if the user taps the SAME selection that's currently playing
    // (same frequency + same golden-stack mode), stop the session. Otherwise start
    // or live-retune to the new frequency.
    const sameFreq = Math.abs(audioEngine.frequency - hz) < 0.05;
    const sameMode = wantGolden === !!audioEngine.goldenStack;
    if (audioEngine.playing && sameFreq && sameMode) {
      audioEngine._pendingAmbient = null;
      audioEngine.stop();
      setRemaining(0);
      setSleepMode(false);
      setActiveSoundscape(null);
      return;
    }
    audioEngine.setFrequency(hz);
    audioEngine.setGoldenStack(wantGolden);
    setActiveSoundscape(null); // new manual selection invalidates any active soundscape
    if (!audioEngine.playing) {
      setRemaining(duration * 60);
      audioEngine.start();
    }
  };

  const toggleGoldenStack = () => {
    if (!isPro) { onOpenAccount(); return; }
    audioEngine.setGoldenStack(!state.goldenStack);
  };

  const startSleepMode = () => {
    if (!isPro) { onOpenAccount(); return; }
    // If something is playing, stop it cleanly first. audioEngine.stop schedules
    // a fade-out internally, but the new oscillator we'll create with start() will
    // also be brand-new (we always create fresh on each start), so there's no clash.
    audioEngine._pendingAmbient = null;
    if (audioEngine.playing) audioEngine.stop();
    audioEngine.setGoldenStack(false);
    audioEngine.setWaveform('sine');
    audioEngine.setBinaural(0);
    audioEngine.setToneVolume(0.22);
    audioEngine.setFrequency(4);
    // Clear any currently-playing ambient, then layer brown noise gently
    Object.keys(audioEngine.ambient || {}).forEach((k) => audioEngine.setAmbient(k, 0));
    audioEngine.setAmbient('brown', 0.45);
    setBreathwork(false);
    setActiveSoundscape(null);
    setDuration(SLEEP_DURATION_MIN);
    setRemaining(SLEEP_DURATION_MIN * 60);
    setSleepMode(true);
    // Start in the SAME gesture frame (no setTimeout) — required for iOS audio unlock
    audioEngine.start();
  };

  const stopSleepMode = () => {
    setSleepMode(false);
    audioEngine._pendingAmbient = null;
    audioEngine.stop();
    setRemaining(0);
    Object.keys(audioEngine.ambient || {}).forEach((k) => audioEngine.setAmbient(k, 0));
    setActiveSoundscape(null);
  };

  const selectSoundscape = (s) => {
    if (!isPro) { onOpenAccount(); return; }
    // Tap-to-toggle: if the user taps the currently-active soundscape while it's
    // playing, stop the session gracefully (engine fades both tone & ambient
    // to 0 over ~0.8s before stopping oscillators). The activeSoundscape state
    // is cleared so a subsequent tap on the same card re-starts fresh rather
    // than resuming a snapshot.
    if (activeSoundscape === s.key && audioEngine.playing) {
      audioEngine._pendingAmbient = null; // don't keep a snapshot since this is an explicit stop
      audioEngine.stop();
      setActiveSoundscape(null);
      setRemaining(0);
      setSleepMode(false);
      return;
    }

    // Resume of the SAME soundscape from a paused state — just hit start; the
    // engine's _pendingAmbient snapshot will restore the original mix.
    if (activeSoundscape === s.key && !audioEngine.playing) {
      setRemaining(duration * 60);
      audioEngine.start();
      return;
    }

    // New / different soundscape: configure the engine, apply the mix, start.
    // setAmbient(k, 0) now smoothly fades any layers not in the new mix; the
    // new layers ramp up via setTargetAtTime — graceful cross-blend.
    audioEngine._pendingAmbient = null; // discard any previous snapshot
    audioEngine.setGoldenStack(false);
    audioEngine.setWaveform('sine');
    audioEngine.setBinaural(0);
    audioEngine.setFrequency(s.freq);
    Object.keys(audioEngine.ambient || {}).forEach((k) => {
      if (!(k in s.ambient)) audioEngine.setAmbient(k, 0);
    });
    Object.entries(s.ambient).forEach(([k, v]) => audioEngine.setAmbient(k, v));
    setBreathwork(false);
    setSleepMode(false);
    setActiveSoundscape(s.key);
    if (!audioEngine.playing) {
      setRemaining(duration * 60);
      audioEngine.start();
    }
  };

  const setAmbient = (key, v) => {
    if (!isPro && v > 0) { onOpenAccount(); return; }
    // Manual ambient adjustment → the user is customising; the curated mix
    // is no longer "untouched", so clear the active-soundscape badge.
    if (activeSoundscape) setActiveSoundscape(null);
    audioEngine.setAmbient(key, v);
  };

  const toggleBreathwork = () => {
    if (!isPro) { onOpenAccount(); return; }
    setBreathwork((b) => !b);
  };

  const onCustomFreqChange = (e) => {
    if (!isPro) { onOpenAccount(); return; }
    audioEngine.setFrequency(parseFloat(e.target.value));
  };

  const saveSession = async () => {
    setErr('');
    if (!saveName.trim()) { setErr('Give your session a name'); return; }
    try {
      await api.post('/sessions', {
        name: saveName.trim(),
        frequency: state.frequency,
        waveform: state.waveform,
        binaural: state.binaural,
        duration_minutes: duration,
        ambient: state.ambient,
        breathwork,
      });
      setSaveName('');
      refreshSessions();
    } catch (e) {
      const msg = formatApiError(e);
      if (e?.response?.status === 402) {
        setErr(msg + ' →');
        refreshSub();
      } else { setErr(msg); }
    }
  };

  const loadSession = (s) => {
    audioEngine.setFrequency(s.frequency);
    audioEngine.setWaveform(s.waveform);
    audioEngine.setBinaural(s.binaural || 0);
    setDuration(s.duration_minutes || 10);
    setBreathwork(!!s.breathwork);
    Object.entries(s.ambient || {}).forEach(([k, v]) => audioEngine.setAmbient(k, v));
  };

  const deleteSession = async (id) => {
    try { await api.delete(`/sessions/${id}`); refreshSessions(); } catch (e) { console.warn('[Dashboard] delete session failed', e); }
  };

  const activePreset = useMemo(
    () => SOLFEGGIO.find((p) => p.hz === Math.round(state.frequency)),
    [state.frequency]
  );

  return (
    <div className="relative min-h-screen w-full overflow-x-hidden">
      <div className="aurora-bg" />
      <div className="grain" />

      {/* Pro preview banner — only for free/Basic users */}
      {!isPro && (
        <div
          data-testid="pro-preview-banner"
          className="relative z-10 mx-4 mt-4 lg:mx-6 lg:mt-6 glass-soft px-4 py-2.5 flex items-center justify-between gap-3 flex-wrap border border-[#C4A67A]/25"
        >
          <div className="flex items-center gap-3 flex-wrap">
            <span className="label-tiny text-[#C4A67A] flex items-center gap-1 whitespace-nowrap">
              <Sparkles size={11} /> Pro includes
            </span>
            <div className="flex items-center gap-3 flex-wrap">
              {PRO_PREVIEW.map(({ label, Icon }) => (
                <span key={label} className="flex items-center gap-1 text-[11px] text-[#E8E3D9]/90 whitespace-nowrap">
                  <Icon size={11} className="text-[#72C2AC]" /> {label}
                </span>
              ))}
            </div>
          </div>
          <button
            data-testid="pro-preview-cta"
            onClick={onOpenAccount}
            className="text-[11px] tracking-wider font-medium text-[#08120F] bg-[#C4A67A] hover:bg-[#d6b88c] px-3 py-1.5 rounded-full transition-colors whitespace-nowrap"
          >
            Try free for 7 days →
          </button>
        </div>
      )}

      <div className="relative z-10 min-h-screen lg:h-screen w-full flex flex-col lg:flex-row p-4 lg:p-6 gap-4 lg:gap-6">

        {/* LEFT — Solfeggio + Saved */}
        <aside className="w-full lg:w-80 flex flex-col gap-4 lg:gap-6 lg:h-full lg:overflow-y-auto custom-scrollbar">
          <div className="glass p-6">
            <div className="flex items-center justify-between mb-1">
              <div className="label-tiny">Healing Frequencies</div>
              <div className="flex items-center gap-3">
                <button
                  data-testid="account-button"
                  onClick={onOpenAccount}
                  className="text-[#8A9A92] hover:text-[#72C2AC] transition-colors"
                  title="Account"
                >
                  <UserCircle size={18} />
                </button>
                <button
                  data-testid="logout-button"
                  onClick={logout}
                  className="text-[#8A9A92] hover:text-[#72C2AC] transition-colors"
                  title="Sign out"
                >
                  <LogOut size={16} />
                </button>
              </div>
            </div>
            <h2 className="font-display text-2xl text-[#E8E3D9] font-light">Hello, {user.name}</h2>
            <p className="text-xs text-[#8A9A92] mt-1">
              {isPro ? (
                <span className="text-[#C4A67A] inline-flex items-center gap-1">
                  <Sparkles size={11} /> Pro · all features unlocked
                </span>
              ) : (
                <>Choose a tone or <button onClick={onOpenAccount} className="text-[#72C2AC] hover:text-[#C4A67A] underline-offset-2 hover:underline">upgrade for Pro features</button>.</>
              )}
            </p>
          </div>

          <div className="glass p-5">
            <div className="label-tiny mb-3">Solfeggio Presets</div>
            <div className="grid grid-cols-2 gap-2">
              {SOLFEGGIO.map((p) => {
                const active = Math.round(state.frequency) === p.hz;
                return (
                  <button
                    key={p.hz}
                    data-testid={`solfeggio-freq-${p.hz}`}
                    onClick={() => selectFrequency(p.hz)}
                    className={`glass-soft p-3 text-left transition-all duration-300 hover:-translate-y-0.5 ${
                      active ? 'border-[#72C2AC]/60 bg-[#1A332A]/60' : ''
                    }`}
                  >
                    <div className={`font-mono text-base ${active ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>
                      {p.hz}<span className="text-[10px] ml-1 text-[#8A9A92]">Hz</span>
                    </div>
                    <div className="text-[11px] text-[#E8E3D9]/80 mt-0.5">{p.name}</div>
                    <div className="text-[10px] text-[#8A9A92]">{p.desc}</div>
                  </button>
                );
              })}
            </div>

            {/* Golden Ratio preset */}
            <button
              data-testid="golden-preset"
              onClick={() => selectFrequency(GOLDEN_BASE, { golden: true })}
              className={`mt-3 w-full glass-soft p-3 flex items-center gap-3 transition-all duration-300 hover:-translate-y-0.5 ${
                state.goldenStack ? 'border-[#C4A67A]/60 bg-[#1A332A]/60' : ''
              }`}
            >
              <Sparkles
                size={20}
                className={state.goldenStack ? 'text-[#C4A67A]' : 'text-[#8A9A92]'}
                style={state.goldenStack ? { filter: 'drop-shadow(0 0 8px rgba(196,166,122,0.6))' } : {}}
              />
              <div className="flex-1 text-left">
                <div className={`font-mono text-base ${state.goldenStack ? 'text-[#C4A67A]' : 'text-[#E8E3D9]'}`}>
                  φ Golden Stack
                </div>
                <div className="text-[10px] text-[#8A9A92]">
                  {GOLDEN_BASE} · {Math.round(GOLDEN_BASE * PHI)} · {Math.round(GOLDEN_BASE * PHI * PHI)} Hz
                </div>
              </div>
            </button>

            {/* Sleep Mode preset */}
            <button
              data-testid="sleep-mode-preset"
              onClick={sleepMode ? stopSleepMode : startSleepMode}
              className={`mt-2 w-full glass-soft p-3 flex items-center gap-3 transition-all duration-300 hover:-translate-y-0.5 ${
                sleepMode ? 'border-[#72C2AC]/60 bg-[#0E1F18]/80' : ''
              }`}
            >
              <Moon
                size={20}
                className={sleepMode ? 'text-[#72C2AC]' : 'text-[#8A9A92]'}
                style={sleepMode ? { filter: 'drop-shadow(0 0 10px rgba(114,194,172,0.5))' } : {}}
              />
              <div className="flex-1 text-left">
                <div className={`font-mono text-base ${sleepMode ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>
                  Sleep Mode
                </div>
                <div className="text-[10px] text-[#8A9A92]">
                  {sleepMode ? 'Drifting · fades at 60s' : '4 Hz · brown noise · 30 min fade'}
                </div>
              </div>
              {!isPro && <Lock size={12} className="text-[#C4A67A]" />}
            </button>
          </div>

          {/* Brainwave & Specials — Pro only */}
          <div className={`glass p-5 relative ${!isPro ? 'overflow-hidden' : ''}`} data-testid="specials-section">
            <div className="flex items-center justify-between mb-3">
              <div className="label-tiny flex items-center gap-2">
                Brainwave &amp; Specials
                {!isPro && <Lock size={11} className="text-[#C4A67A]" />}
              </div>
              {!isPro && (
                <span
                  data-testid="specials-pro-badge"
                  className="text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-0.5 rounded-full"
                >
                  PRO
                </span>
              )}
            </div>

            <div className={`grid grid-cols-2 gap-2 transition-opacity ${!isPro ? 'opacity-45 pointer-events-none select-none' : ''}`}>
              {SPECIALS.map((p) => {
                const active = Math.abs(audioEngine.frequency - p.hz) < 0.05 && !state.goldenStack && isPro;
                return (
                  <button
                    key={p.hz}
                    data-testid={`special-freq-${p.hz}`}
                    onClick={() => selectFrequency(p.hz, { special: true })}
                    className={`glass-soft p-3 text-left transition-all duration-300 hover:-translate-y-0.5 ${
                      active ? 'border-[#72C2AC]/60 bg-[#1A332A]/60' : ''
                    }`}
                  >
                    <div className={`font-mono text-sm ${active ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>
                      {p.hz}<span className="text-[10px] ml-1 text-[#8A9A92]">Hz</span>
                    </div>
                    <div className="text-[11px] text-[#E8E3D9]/80 mt-0.5">{p.name}</div>
                    <div className="text-[10px] text-[#8A9A92]">{p.desc}</div>
                  </button>
                );
              })}
            </div>

            {!isPro && (
              <button
                data-testid="specials-unlock-cta"
                onClick={onOpenAccount}
                className="absolute inset-0 flex items-end justify-center pb-5 px-5 cursor-pointer group"
              >
                <div className="glass-soft px-4 py-3 border border-[#C4A67A]/40 hover:border-[#C4A67A] hover:-translate-y-0.5 transition-all text-center w-full max-w-[260px]">
                  <div className="flex items-center justify-center gap-2 text-[#C4A67A] text-xs font-medium">
                    <Lock size={12} /> Included in Pro
                  </div>
                  <div className="text-[10px] text-[#8A9A92] mt-1">
                    Unlock 10 brainwave &amp; sacred frequencies
                  </div>
                </div>
              </button>
            )}
          </div>

          {/* Soundscapes — curated multi-layer mixes, Pro only */}
          <div className={`glass p-5 relative ${!isPro ? 'overflow-hidden' : ''}`} data-testid="soundscapes-section">
            <div className="flex items-center justify-between mb-3">
              <div className="label-tiny flex items-center gap-2">
                <Cloud size={11} /> Soundscapes
                {!isPro && <Lock size={11} className="text-[#C4A67A]" />}
              </div>
              {!isPro && (
                <span
                  data-testid="soundscapes-pro-badge"
                  className="text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-0.5 rounded-full"
                >
                  PRO
                </span>
              )}
            </div>

            <div className={`grid grid-cols-1 gap-2 transition-opacity ${!isPro ? 'opacity-45 pointer-events-none select-none' : ''}`}>
              {SOUNDSCAPES.map((s) => {
                const Icon = s.Icon;
                const isActive = activeSoundscape === s.key;
                const isActivePlaying = isActive && state.playing;
                return (
                  <button
                    key={s.key}
                    data-testid={`soundscape-${s.key}`}
                    data-active={isActive ? 'true' : 'false'}
                    data-playing={isActivePlaying ? 'true' : 'false'}
                    aria-pressed={isActive}
                    onClick={() => selectSoundscape(s)}
                    className={`glass-soft p-3 flex items-center gap-3 text-left transition-all duration-300 hover:-translate-y-0.5 ${
                      isActive
                        ? 'border border-[#72C2AC]/70 ring-1 ring-[#72C2AC]/30 bg-[#5C9E8C]/10'
                        : 'hover:border-[#72C2AC]/40'
                    }`}
                  >
                    <Icon
                      size={18}
                      className={`flex-shrink-0 ${isActive ? 'text-[#C4A67A]' : 'text-[#72C2AC]'}`}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-[#E8E3D9] flex items-center gap-2 flex-wrap">
                        <span>{s.name}</span>
                        {isActivePlaying && (
                          <span
                            data-testid={`soundscape-${s.key}-playing-badge`}
                            className="text-[8px] tracking-widest text-[#72C2AC] bg-[#72C2AC]/15 px-1.5 py-0.5 rounded inline-flex items-center gap-1"
                          >
                            <span className="w-1 h-1 rounded-full bg-[#72C2AC] animate-pulse" />
                            PLAYING
                          </span>
                        )}
                        {isActive && !state.playing && (
                          <span
                            data-testid={`soundscape-${s.key}-paused-badge`}
                            className="text-[8px] tracking-widest text-[#8A9A92] bg-[#8A9A92]/15 px-1.5 py-0.5 rounded"
                          >
                            PAUSED
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] text-[#8A9A92] truncate">
                        {isActivePlaying ? 'Tap to stop' : (isActive ? 'Tap to resume' : s.desc)}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>

            {!isPro && (
              <button
                data-testid="soundscapes-unlock-cta"
                onClick={onOpenAccount}
                className="absolute inset-0 flex items-end justify-center pb-5 px-5 cursor-pointer group"
              >
                <div className="glass-soft px-4 py-3 border border-[#C4A67A]/40 hover:border-[#C4A67A] hover:-translate-y-0.5 transition-all text-center w-full max-w-[260px]">
                  <div className="flex items-center justify-center gap-2 text-[#C4A67A] text-xs font-medium">
                    <Lock size={12} /> Included in Pro
                  </div>
                  <div className="text-[10px] text-[#8A9A92] mt-1">
                    6 curated multi-layer mixes
                  </div>
                </div>
              </button>
            )}
          </div>

          <StreakPanel refreshKey={streakBump} />

          <div className="glass p-5">
            <div className="label-tiny mb-3 flex items-center justify-between">
              <span>Saved Sessions</span>
              <span className="text-[#72C2AC]">{sessions.length}</span>
            </div>
            <div className="space-y-2">
              {sessions.length === 0 && (
                <div className="text-xs text-[#8A9A92]">No sessions saved yet.</div>
              )}
              {sessions.map((s) => (
                <div key={s.id} data-testid={`saved-session-${s.id}`} className="glass-soft p-3 flex items-center justify-between gap-2">
                  <button onClick={() => loadSession(s)} className="text-left flex-1 min-w-0">
                    <div className="text-sm text-[#E8E3D9] truncate">{s.name}</div>
                    <div className="text-[11px] font-mono text-[#72C2AC]">{s.frequency}Hz · {s.duration_minutes}m</div>
                  </button>
                  <button
                    data-testid={`delete-session-${s.id}`}
                    onClick={() => deleteSession(s.id)}
                    className="text-[#8A9A92] hover:text-[#D96C6C] transition-colors"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </aside>

        {/* CENTER — Visualizer + transport */}
        <main className="flex-1 relative rounded-3xl overflow-hidden border border-[rgba(92,158,140,0.15)] bg-black/30 min-h-[480px] lg:min-h-0">
          <Visualizer playing={state.playing} frequency={state.frequency} />
          <Breathwork active={breathwork && state.playing} />

          {/* Frequency label (top) */}
          <div className="absolute top-6 left-1/2 -translate-x-1/2 text-center z-10">
            <div className="label-tiny">Now Tuning</div>
            <div data-testid="current-frequency" className="font-mono text-4xl text-[#72C2AC] tracking-widest mt-1">
              {state.frequency.toFixed(1)}<span className="text-base text-[#8A9A92] ml-1">Hz</span>
            </div>
            {activePreset && (
              <div className="font-display text-xl text-[#E8E3D9] mt-1">{activePreset.name}</div>
            )}
          </div>

          {/* Transport (bottom) */}
          <div className="absolute bottom-6 left-1/2 -translate-x-1/2 flex flex-col items-center gap-4 z-10">
            <div data-testid="timer-display" className="font-mono text-2xl text-[#E8E3D9] tracking-widest">
              {formatTime(state.playing ? remaining : duration * 60)}
            </div>
            <button
              data-testid="play-pause-button"
              onClick={togglePlay}
              className="w-20 h-20 rounded-full border border-[#72C2AC]/50 bg-[#5C9E8C]/15 hover:bg-[#5C9E8C]/35 backdrop-blur-md flex items-center justify-center transition-all duration-300 hover:scale-105 active:scale-95"
              style={{ boxShadow: '0 0 40px rgba(114,194,172,0.25)' }}
            >
              {state.playing ? <Pause size={28} className="text-[#E8E3D9]" /> : <Play size={28} className="text-[#E8E3D9] ml-1" />}
            </button>
          </div>
        </main>

        {/* RIGHT — Custom + Ambient + Save */}
        <aside className="w-full lg:w-[360px] flex flex-col gap-4 lg:gap-6 lg:h-full lg:overflow-y-auto custom-scrollbar">
          {/* Custom Generator */}
          <div className="glass p-6">
            <div className="label-tiny mb-4">Custom Generator</div>

            <label className="text-xs text-[#8A9A92] flex justify-between mb-1">
              <span>Frequency</span><span className="font-mono text-[#72C2AC]">{state.frequency.toFixed(1)} Hz</span>
            </label>
            <input
              data-testid="custom-freq-slider"
              type="range" min="1" max="1200" step="0.1"
              value={state.frequency}
              onChange={onCustomFreqChange}
              disabled={!isPro}
              className="slider disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ '--v': `${((state.frequency - 1) / 1199) * 100}%` }}
            />
            {!isPro && (
              <button
                onClick={onOpenAccount}
                data-testid="custom-freq-locked"
                className="mt-2 text-[10px] text-[#C4A67A] hover:text-[#72C2AC] flex items-center gap-1"
              >
                <Lock size={10} /> Custom frequency is a Pro feature — unlock
              </button>
            )}

            <div className="mt-4">
              <div className="label-tiny mb-2">Waveform</div>
              <div className="grid grid-cols-4 gap-1">
                {WAVEFORMS.map((w) => (
                  <button
                    key={w}
                    data-testid={`waveform-${w}`}
                    onClick={() => audioEngine.setWaveform(w)}
                    className={`text-xs py-2 rounded-md transition-colors duration-200 capitalize ${
                      state.waveform === w
                        ? 'bg-[#5C9E8C]/30 text-[#72C2AC] border border-[#72C2AC]/40'
                        : 'border border-[#5C9E8C]/15 text-[#8A9A92] hover:text-[#E8E3D9]'
                    }`}
                  >
                    {w}
                  </button>
                ))}
              </div>
            </div>

            <button
              data-testid="golden-stack-toggle"
              onClick={toggleGoldenStack}
              className={`mt-5 w-full py-2.5 rounded-full border transition-colors duration-300 flex items-center justify-center gap-2 ${
                state.goldenStack
                  ? 'bg-[#C4A67A]/15 border-[#C4A67A]/50 text-[#C4A67A]'
                  : 'border-[#5C9E8C]/20 text-[#8A9A92] hover:text-[#E8E3D9]'
              }`}
              title={`Stacks tones at f × φ¹ and f × φ² (φ ≈ ${PHI.toFixed(4)})`}
            >
              {!isPro && <Lock size={12} className="text-[#C4A67A]" />}
              <Sparkles size={14} /> Golden Stack φ {state.goldenStack ? 'On' : 'Off'}
            </button>

            <label className="text-xs text-[#8A9A92] flex justify-between mt-5 mb-1">
              <span>Binaural offset</span><span className="font-mono text-[#72C2AC]">{state.binaural} Hz</span>
            </label>
            <input
              data-testid="binaural-slider"
              type="range" min="0" max="40" step="0.5"
              value={state.binaural}
              onChange={(e) => audioEngine.setBinaural(parseFloat(e.target.value))}
              className="slider"
              style={{ '--v': `${(state.binaural / 40) * 100}%` }}
            />

            <label className="text-xs text-[#8A9A92] flex justify-between mt-5 mb-1">
              <span><Volume2 size={12} className="inline mr-1" />Tone volume</span>
              <span className="font-mono text-[#72C2AC]">{Math.round(state.toneVolume * 100)}%</span>
            </label>
            <input
              data-testid="tone-volume-slider"
              type="range" min="0" max="1" step="0.01"
              value={state.toneVolume}
              onChange={(e) => audioEngine.setToneVolume(parseFloat(e.target.value))}
              className="slider"
              style={{ '--v': `${state.toneVolume * 100}%` }}
            />
          </div>

          {/* Ambient Mixer — Pro only */}
          <div className={`glass p-6 relative ${!isPro ? 'overflow-hidden' : ''}`} data-testid="ambient-section">
            <div className="flex items-center justify-between mb-4">
              <div className="label-tiny flex items-center gap-2">
                Ambient Layers
                {!isPro && <Lock size={11} className="text-[#C4A67A]" />}
              </div>
              {!isPro && (
                <span
                  data-testid="ambient-pro-badge"
                  className="text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-0.5 rounded-full"
                >
                  PRO
                </span>
              )}
            </div>
            <div className={`space-y-4 transition-opacity ${!isPro ? 'opacity-45 pointer-events-none select-none' : ''}`}>
              {AMBIENT.map(({ key, label, Icon }) => {
                const v = state.ambient[key] || 0;
                return (
                  <div key={key}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm text-[#E8E3D9] flex items-center gap-2">
                        <Icon size={14} className="text-[#C4A67A]" /> {label}
                      </span>
                      <span className="font-mono text-xs text-[#C4A67A]">{Math.round(v * 100)}%</span>
                    </div>
                    <input
                      data-testid={`ambient-${key}-slider`}
                      type="range" min="0" max="1" step="0.01"
                      value={v}
                      onChange={(e) => setAmbient(key, parseFloat(e.target.value))}
                      disabled={!isPro}
                      className="slider amber disabled:opacity-50 disabled:cursor-not-allowed"
                      style={{ '--v': `${v * 100}%` }}
                    />
                  </div>
                );
              })}
            </div>
            {!isPro && (
              <button
                data-testid="ambient-unlock-cta"
                onClick={onOpenAccount}
                className="absolute inset-0 flex items-end justify-center pb-6 px-6 cursor-pointer group"
              >
                <div className="glass-soft px-4 py-3 border border-[#C4A67A]/40 hover:border-[#C4A67A] hover:-translate-y-0.5 transition-all text-center w-full max-w-[280px]">
                  <div className="flex items-center justify-center gap-2 text-[#C4A67A] text-xs font-medium">
                    <Lock size={12} /> Included in Pro
                  </div>
                  <div className="text-[10px] text-[#8A9A92] mt-1">
                    Layer 8 ambient soundscapes — rain, ocean, forest, wind &amp; more
                  </div>
                </div>
              </button>
            )}
          </div>

          {/* Timer + Breathwork */}
          <div className="glass p-6">
            <div className="label-tiny mb-3">Session</div>
            <label className="text-xs text-[#8A9A92] flex justify-between mb-1">
              <span>Duration</span><span className="font-mono text-[#72C2AC]">{duration} min</span>
            </label>
            <input
              data-testid="duration-slider"
              type="range" min="1" max="60" step="1"
              value={duration}
              onChange={(e) => setDuration(parseInt(e.target.value))}
              className="slider"
              style={{ '--v': `${((duration - 1) / 59) * 100}%` }}
            />

            <button
              data-testid="breathwork-toggle"
              onClick={toggleBreathwork}
              className={`mt-5 w-full py-2.5 rounded-full border transition-colors duration-300 flex items-center justify-center gap-2 ${
                breathwork
                  ? 'bg-[#5C9E8C]/25 border-[#72C2AC]/50 text-[#72C2AC]'
                  : 'border-[#5C9E8C]/20 text-[#8A9A92] hover:text-[#E8E3D9]'
              }`}
            >
              {!isPro && <Lock size={12} className="text-[#C4A67A]" />}
              <Wind size={14} /> Breathwork {breathwork ? 'On' : 'Off'}
            </button>
          </div>

          {/* Save session */}
          <div className="glass p-6">
            <div className="label-tiny mb-3">Save This Session</div>
            <div className="flex gap-2">
              <input
                data-testid="session-name-input"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                placeholder="Evening calm…"
                className="flex-1 bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] text-sm"
              />
              <button
                data-testid="save-session-button"
                onClick={saveSession}
                className="px-4 py-2 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] text-sm font-medium transition-colors flex items-center gap-1"
              >
                <Save size={14} /> Save
              </button>
            </div>
            {err && (
              <div className="text-[#D96C6C] text-xs mt-2">
                {err}{' '}
                {!isPro && (
                  <button onClick={onOpenAccount} className="text-[#C4A67A] hover:text-[#72C2AC] underline ml-1">
                    Upgrade
                  </button>
                )}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
