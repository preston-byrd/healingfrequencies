import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { Play, Pause, Save, Trash2, LogOut, Wind, Droplet, Waves, Trees, Volume2, Sparkles, UserCircle, Lock, Bug, CloudRain, Music, Moon, Brain, Layers, Sunrise, Cloud, Heart, Globe, Sun, Smartphone, HeartPulse, Mic, Ear, Flower2, Bell, Info, CircleDot } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import api, { formatApiError } from '@/lib/api';
import { applyIdealVolume, getIdealVolume, loadAdminOverrides, volumeDivergesFromIdeal } from '@/lib/frequencyDefaults';
import { useAuth } from '@/contexts/AuthContext';
import { useSubscription } from '@/contexts/SubscriptionContext';
import Visualizer from '@/components/Visualizer';
import Breathwork from '@/components/Breathwork';
import AmbientPulse from '@/components/AmbientPulse';
import WellnessCheckinCard from '@/components/WellnessCheckinCard';
import SessionShareCard from '@/components/SessionShareCard';
import SessionUnlockCard from '@/components/SessionUnlockCard';
import FlowModePanel from '@/components/FlowModePanel';
import StreakPanel from '@/components/StreakPanel';
import AIAgentSheet from '@/components/AIAgentSheet';
import InstallAppModal from '@/components/InstallAppModal';
import usePWAInstall from '@/lib/usePWAInstall';
import HapticsModal from '@/components/HapticsModal';
import haptic from '@/lib/hapticEngine';
import VoiceShortcutsModal from '@/components/VoiceShortcutsModal';
import CalibrationModal from '@/components/CalibrationModal';
import OnboardingTransitionCard from '@/components/OnboardingTransitionCard';
import detectHeadphones from '@/lib/detectHeadphones';
import SoundBathPanel from '@/components/SoundBathPanel';
import MeditationSoundsPanel from '@/components/MeditationSoundsPanel';
import HarmonicBlueprintCard from '@/components/HarmonicBlueprintCard';
import HarmonicBlueprintSheet from '@/components/HarmonicBlueprintSheet';
import NotificationBell from '@/components/NotificationBell';
import SessionImpactPrompt from '@/components/SessionImpactPrompt';
import MyMilestones from '@/components/MyMilestones';
import NotificationPreferencesModal from '@/components/NotificationPreferencesModal';
import PushOptInSlip, { requestPushOptInIfWarm } from '@/components/PushOptInSlip';
import ListeningGuide, { HeadphoneReminder, useHeadphoneReminder } from '@/components/ListeningGuide';
import { getSoundBath } from '@/lib/soundBathEngine';

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
// Sleep Mode duration chooser options (minutes). Module-level constant so the
// reference is stable across renders and useEffect deps don't churn.
const SLEEP_DURATIONS = [30, 60, 120, 240, 480];

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
  { label: 'Sound Baths', Icon: Waves },
  { label: 'Meditation Sounds', Icon: Flower2 },
  { label: 'Brainwave & Specials', Icon: Brain },
  { label: 'φ Golden Stack', Icon: Sparkles },
  { label: 'Sleep Mode', Icon: Moon },
  { label: 'Ambient Layers', Icon: Layers },
  { label: 'Soundscapes', Icon: Cloud },
];

// Sound Bath sessions always default to a fixed 10-minute contemplative
// window regardless of the user's saved session-length preference. The
// user can still let the timer expire naturally or tap Stop to end early.
const BATH_DEFAULT_MIN = 10;

function formatTime(secs) {
  const m = Math.floor(secs / 60).toString().padStart(2, '0');
  const s = Math.floor(secs % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

/**
 * Map a pattern's `cta` object to the suggestion shape the AIAgentSheet
 * knows how to render as a Start-there card in its first assistant message.
 * Returns null for CTAs we can't yet render (arm_preset — until the preset
 * dispatch API is exposed at Dashboard level).
 */
function _patternCtaToSuggestion(cta) {
  if (!cta || typeof cta !== 'object') return null;
  if (cta.action === 'arm_frequency' && Number.isFinite(cta.frequency) && cta.frequency > 0) {
    // Fidelity: preserve the exact stored Hz — Math.round would lose the
    // sub-Hz precision that Pro users can dial in (e.g. 528.3 Hz).
    const freq = Number(cta.frequency);
    return {
      kind: 'preset',
      label: `${freq.toFixed(1)} Hz — start where you left off`,
      frequency: freq,
      waveform: 'sine',
      duration_min: 15,
      pro_only: false,
    };
  }
  if (cta.action === 'arm_soundscape' && cta.soundscape) {
    return {
      kind: 'soundscape',
      label: `${String(cta.soundscape).charAt(0).toUpperCase()}${String(cta.soundscape).slice(1)} — a soft layer you haven't tried`,
      soundscape: cta.soundscape,
      pro_only: false,
    };
  }
  return null;
}



export default function Dashboard({ onOpenAccount }) {
  const { user, logout } = useAuth();
  const { isPro, refresh: refreshSub, sub } = useSubscription();
  const [state, setState] = useState(audioEngine.getState());
  const [duration, setDuration] = useState(10); // minutes
  // Restore `remaining` from `audioEngine.sessionEndAt` on mount so the
  // countdown survives Dashboard being unmounted (e.g. user visited
  // /account mid-session and came back). Without this, the timer
  // display renders 00:00 while the audio is still playing — the
  // singleton has kept going but React state was thrown away.
  const [remaining, setRemaining] = useState(() => {
    const endAt = audioEngine.sessionEndAt || 0;
    if (!endAt || endAt <= Date.now()) return 0;
    return Math.max(0, Math.round((endAt - Date.now()) / 1000));
  }); // seconds; 0 = not running
  const [breathwork, setBreathwork] = useState(false);
  // Ambient Pulse — subtle radial "breathing" glow behind the visualizer.
  // Persisted per-user via localStorage; default ON for Pro, OFF for free.
  const [ambientPulse, setAmbientPulse] = useState(false);
  const [ambientPulseGate, setAmbientPulseGate] = useState(''); // '' | 'pro-only'
  // Ambient Pulse: load persisted preference on mount / user change.
  // Default: ON for Pro, OFF for free (product spec). Stored per-user so
  // it survives across sessions and across devices when the user next
  // signs in.
  useEffect(() => {
    if (!user) return;
    const uid = user.id || user.email || 'anon';
    try {
      const raw = localStorage.getItem(`solar:ambientPulse:${uid}`);
      if (raw === '1' || raw === '0') {
        setAmbientPulse(raw === '1');
      } else {
        setAmbientPulse(!!isPro);
      }
    } catch (_) {
      setAmbientPulse(!!isPro);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, isPro]);

  const toggleAmbientPulse = () => {
    if (!isPro) {
      setAmbientPulseGate('pro-only');
      setTimeout(() => setAmbientPulseGate(''), 3500);
      return;
    }
    const next = !ambientPulse;
    setAmbientPulse(next);
    try {
      const uid = user?.id || user?.email || 'anon';
      localStorage.setItem(`solar:ambientPulse:${uid}`, next ? '1' : '0');
    } catch (_) { /* private mode / storage disabled */ }
  };


  const [sessions, setSessions] = useState([]);
  const [saveName, setSaveName] = useState('');
  const [err, setErr] = useState('');
  const [streakBump, setStreakBump] = useState(0);
  const [sessionStart, setSessionStart] = useState(null);
  const [checkedInThisRun, setCheckedInThisRun] = useState(false);
  const [sleepMode, setSleepMode] = useState(false);

  // Per-frequency ideal-default-volume tracking.
  //   userAdjustedVolumeRef.current = true → user has manually moved the tone
  //   slider during the current session. As long as this flag is true, no
  //   playback entry point may auto-apply the per-frequency ideal volume;
  //   the user's chosen level is respected until they explicitly stop the
  //   session or tap "Reset to recommended".
  // The flag is cleared on every fresh session start (togglePlay start,
  // stopSleepMode, timer expiry, etc.).
  const userAdjustedVolumeRef = React.useRef(false);
  // Fetch admin overrides once per app load. Silent on failure.
  useEffect(() => { loadAdminOverrides(); }, []);

  // Soundscape selection — set when the user taps a curated mix, cleared on
  // explicit reset (tap-toggle, swap, full stop, sleep mode, timer end).
  // Persisted through Pause/Resume so the visual indicator survives a pause.
  const [activeSoundscape, setActiveSoundscape] = useState(null);
  // Visual mode for the cymatic visualizer: 'rings' (default) | 'chladni' | 'ripples'
  const [visualMode, setVisualMode] = useState('rings');
  // AI Recommend panel state
  const [aiOpen, setAiOpen] = useState(false);
  const [aiIntent, setAiIntent] = useState('');
  const [aiMood, setAiMood] = useState('');
  const [aiGoal, setAiGoal] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiErr, setAiErr] = useState('');
  const [aiResult, setAiResult] = useState(null); // last reco for display
  const [sleepDurationMin, setSleepDurationMin] = useState(30);
  // Track restore lifecycle: restoreStartedRef prevents re-entry of the restore
  // effect (it's set synchronously when restore begins); prefsRestoredRef gates
  // the auto-save effect (set only when restore completes, so auto-save can't
  // fire and clobber prefs while restoration is in-flight).
  const restoreStartedRef = React.useRef(false);
  const prefsRestoredRef = React.useRef(false);
  const prefsSaveTimerRef = React.useRef(null);

  // Smart Fade Timer: any timer-based session smoothly tapers the master gain
  // to silence over the last SMART_FADE_SECONDS so sessions land softly. Fade
  // runs on this.master in audioEngine.js, which preserves the user's layered
  // mix balance automatically. Old Sleep-Mode-specific 60s fade is replaced
  // by this unified 300s path.
  const SMART_FADE_SECONDS = 300; // 5 minutes
  const fadeArmedRef = React.useRef(false);
  // Marks whether the CURRENT session was launched by the Wellness Assistant
  // (i.e. via `sf:agent:suggestion-taken`). When true, the timer-expiry
  // branch shows a soft "How are you feeling now?" check-in instead of
  // silently ending — giving the user the option to extend +10 min or close
  // the assistant flow gracefully.
  const assistantOwnedRef = React.useRef(false);
  const [checkinOpen, setCheckinOpen] = useState(false);
  // Post-session share card — soft, optional "Share your session" overlay
  // that surfaces below the check-in modal when the user taps the share
  // affordance. `shareSession` is the frozen snapshot of the session that
  // just wrapped (frequency, name, duration, visualizer, flow journey) so
  // the card can render even if the user changes settings after the fade.
  const [shareOpen, setShareOpen] = useState(false);
  const [shareSession, setShareSession] = useState(null);
  // Flow Mode current stage — {stageIdx: 0|1|2, meta: {hz, name, sub}} while
  // a journey is running; null otherwise. Powers the visualiser overlay.
  const [flowStage, setFlowStage] = useState(null);
  // Flow Mode journey descriptor — {key, label, ...} captured when a flow
  // starts so the post-session share card can label Pro shares with the
  // full journey name (e.g. "Deep Restore Journey · 60 min").
  const [activeFlowJourney, setActiveFlowJourney] = useState(null);
  // Saved-session lock — free / trial-expired users can still see their
  // saved sessions (safely stored, never deleted) but tapping any of them
  // opens the SessionUnlockCard upgrade prompt instead of loading. Data is
  // fully preserved so a Pro upgrade instantly restores access.
  const [sessionUnlockOpen, setSessionUnlockOpen] = useState(false);
  // Harmonic Blueprint (Pro) — full-screen voice-signature capture / results.
  const [hbOpen, setHbOpen] = useState(false);
  const [hbHasProfile, setHbHasProfile] = useState(false);
  // Phase 4 perf: cache the profile summary at the Dashboard level so the
  // sheet can render its results panel instantly on open — no round-trip
  // gate. The sheet still refreshes in the background to catch drift from
  // other tabs / journey saves.
  const [hbInitialData, setHbInitialData] = useState(null);

  // Phase 4 handoff: if the user clicked "Re-run analysis" from the Account
  // Harmonic Blueprint section, sessionStorage carries a one-shot flag that
  // auto-opens the sheet on this mount.
  useEffect(() => {
    try {
      if (sessionStorage.getItem('solarisound:open_hb') === '1') {
        sessionStorage.removeItem('solarisound:open_hb');
        setHbOpen(true);
      }
    } catch (_) { /* graceful */ }
  }, []);

  // Best-effort probe on mount (and after sub changes) so the Dashboard card
  // can show the "Captured" badge without opening the sheet — AND so the
  // sheet can open with zero round-trip when tapped. Parallelises the two
  // GETs the sheet needs (profile + latest journey). Silently no-ops for
  // free users — the endpoint returns 402 and we treat it as "no profile".
  useEffect(() => {
    let alive = true;
    (async () => {
      const [pRes, jRes] = await Promise.allSettled([
        api.get('/harmonic-blueprint/profile'),
        api.get('/harmonic-blueprint/journey'),
      ]);
      if (!alive) return;
      const profile = pRes.status === 'fulfilled' ? (pRes.value.data || null) : null;
      const journey = jRes.status === 'fulfilled' ? ((jRes.value.data || {}).journey || null) : null;
      setHbHasProfile(!!(profile && profile.profile));
      setHbInitialData({ profile, journey });
    })();
    return () => { alive = false; };
  }, [isPro, hbOpen]);

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
  // Re-runs when playback starts OR when the countdown is freshly armed
  // (remaining transitions 0 → positive). Using `remaining > 0` as a
  // boolean dep keeps every per-second tick from restarting the interval
  // (Object.is(true, true) === true so the effect doesn't re-fire).
  useEffect(() => {
    if (!state.playing || remaining <= 0) return;
    const endAt = Date.now() + remaining * 1000;
    const tick = () => {
      const secsLeft = Math.max(0, Math.round((endAt - Date.now()) / 1000));
      if (secsLeft <= 0) {
        // Timer expired: graceful stop with no resume snapshot, and clear any
        // soundscape selection so the next user action starts fresh. Sound
        // Bath (if active) is also stopped so it ends with the countdown.
        audioEngine._pendingAmbient = null;
        try { getSoundBath(audioEngine).stop(); } catch (e) { /* graceful */ }
        audioEngine.stop();
        setRemaining(0);
        setActiveSoundscape(null);
        fadeArmedRef.current = false;
        // Phase 6: post-session emotional reflection — request the soft
        // "How are you feeling now?" check-in for EVERY Smart-Fade timer
        // completion (previously assistant-owned only). The card is opened
        // once `logJourneyEntry` resolves so the reflection step has a
        // valid entry_id to attach to. Manual stops (no timer) still skip.
        pendingCheckinRef.current = true;
      } else {
        // Smart Fade — arm the master-bus taper once secsLeft crosses below
        // the 5-min threshold. The fade is scheduled with the EXACT remaining
        // time so it lands at 0 right when the timer hits zero. Idempotent
        // via the ref guard; the engine itself is also idempotent (a second
        // beginSessionFade call is a no-op while one is already active).
        if (!fadeArmedRef.current && secsLeft <= SMART_FADE_SECONDS) {
          audioEngine.beginSessionFade(secsLeft);
          fadeArmedRef.current = true;
        }
        setRemaining(secsLeft);
      }
    };
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.playing, remaining > 0]);

  // Persist the session's wall-clock end time on the audioEngine
  // singleton so the countdown can be restored when Dashboard remounts
  // (e.g. after the user detours through /account mid-session). The
  // singleton outlives React state; without this, `remaining` would
  // reset to 0 on remount while audio kept playing — the reported bug.
  // When `remaining` drops to 0 we clear the anchor so a fresh mount
  // doesn't see stale data.
  useEffect(() => {
    if (state.playing && remaining > 0) {
      audioEngine.sessionEndAt = Date.now() + remaining * 1000;
    } else if (!state.playing) {
      audioEngine.sessionEndAt = 0;
    }
  }, [state.playing, remaining]);

  // Reset the fade-arm flag whenever playback stops or a fresh session starts.
  // Without this, a session shorter than 5min would never re-arm because the
  // ref would still be true from the previous session's fade.
  useEffect(() => {
    if (!state.playing) {
      fadeArmedRef.current = false;
      // If we entered "stopped" mid-fade (user tapped stop / changed preset /
      // hit reset), cancel the master-bus ramp so the next session doesn't
      // inherit a tapered state. Safe to call when no fade is active.
      audioEngine.cancelSessionFade(true);
    }
  }, [state.playing]);

  // Background/foreground sync: when tab becomes visible, resume the AudioContext
  // (Chrome auto-suspends it when hidden) so playback continues cleanly.
  // Also freezes the isochronic LFO on hide + rebuilds it on show so the OS
  // suspend/resume cycle can't leave the square-wave gate at a click-y
  // position or introduce a phantom pulse artifact (Issue 1).
  //
  // Primary defence lives in audioEngine._onCtxStateChange (bound directly to
  // the AudioContext) — that's what fires on mobile screen-lock ahead of any
  // DOM event. These listeners are the belt-and-suspenders backup for
  // browsers where DOM events fire but ctx doesn't transition to 'suspended'
  // (desktop Firefox, some Android Chrome builds).
  useEffect(() => {
    const onHide = () => {
      try { audioEngine.freezeIsochronicForBackground(); } catch (_) { /* noop */ }
    };
    const onShow = () => {
      const revive = async () => {
        try { await audioEngine.ensureRunning(); } catch (_) { /* noop */ }
        try { audioEngine.resumeIsochronicAfterBackground(); } catch (_) { /* noop */ }
        if (audioEngine.playing) {
          try { audioEngine.reacquireWakeLockIfWanted(); } catch (_) { /* noop */ }
        }
      };
      revive();
    };
    const onVis = () => (document.hidden ? onHide() : onShow());
    // Multiple event families because different mobile browsers signal the
    // lock event through different channels:
    //   • visibilitychange — Android Chrome + most desktop
    //   • pagehide / pageshow — iOS Safari (fires on screen lock reliably)
    //   • blur / focus — some Android WebViews
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('pagehide', onHide);
    window.addEventListener('pageshow', onShow);
    window.addEventListener('blur', onHide);
    window.addEventListener('focus', onShow);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('pagehide', onHide);
      window.removeEventListener('pageshow', onShow);
      window.removeEventListener('blur', onHide);
      window.removeEventListener('focus', onShow);
    };
  }, []);

  // ---- Background playback: keep screen awake (opt-in) ---------------------
  const [keepAwake, setKeepAwake] = useState(false);
  const keepAwakeSupported = useMemo(
    () => typeof navigator !== 'undefined' && 'wakeLock' in navigator,
    []
  );
  const toggleKeepAwake = useCallback(async () => {
    if (!keepAwakeSupported) return;
    if (keepAwake) {
      await audioEngine.releaseWakeLock();
      setKeepAwake(false);
    } else {
      // Acquire immediately if currently playing; otherwise just remember the
      // preference and start() will acquire on next play.
      audioEngine._wakeLockWanted = true;
      if (audioEngine.playing) await audioEngine.requestWakeLock();
      setKeepAwake(true);
    }
  }, [keepAwake, keepAwakeSupported]);

  // Sleep Mode's previous bespoke 60-second per-source fade is now handled by
  // the unified Smart Fade Timer (master-bus, 5 min) above. Keep the auto-
  // clear effect so the Sleep Mode UI pill resets correctly on session end.

  // Auto-clear sleepMode only when the session has actually completed
  // (NOT when audio is briefly idle during startSleepMode's setTimeout window).
  useEffect(() => {
    if (sleepMode && !state.playing && remaining === 0) setSleepMode(false);
  }, [state.playing, sleepMode, remaining]);

  // Auto check-in: when user has been playing for >= 60s in this run, record it once.
  // Also captures a snapshot of the audio state at session-end and logs a
  // Wellness Journey entry (last 30 sessions per user, powers the LLM's
  // longitudinal memory + the "My Journey" timeline in Account).
  const journeyPlannedRef = React.useRef(null); // planned duration in seconds (null if no timer)
  const journeyLoggedThisRunRef = React.useRef(false);
  // Set by the timer-expiry branch — signals that we WANT to surface the
  // post-session reflection card once the journey log POST resolves (so
  // the card opens with a valid entry_id and the reflection can be
  // attached to the right row). Read + cleared inside `logJourneyEntry`.
  const pendingCheckinRef = React.useRef(false);
  // The most recently-logged journey entry id (returned by /me/journey/log).
  // Used by the post-session reflection card to attach the user's response
  // to the right row. Cleared when a new session starts.
  const [lastJourneyEntryId, setLastJourneyEntryId] = useState(null);
  const logJourneyEntry = React.useCallback(async (actualMs) => {
    let loggedId = null;
    try {
      const actualSec = Math.round(actualMs / 1000);
      if (actualSec < 60) return;
      const plannedSec = journeyPlannedRef.current;
      const ended_early = !!(plannedSec && actualSec < plannedSec * 0.6);
      const ambientClean = {};
      if (state.ambient && typeof state.ambient === 'object') {
        Object.entries(state.ambient).forEach(([k, v]) => {
          const n = Number(v);
          if (Number.isFinite(n) && n > 0.01) ambientClean[k] = Math.round(n * 1000) / 1000;
        });
      }
      // Mood — set by AIAgentSheet when the user picked a suggestion this
      // session. Consumed once so an unrelated later session doesn't
      // reuse a stale mood string.
      let mood = null;
      try {
        mood = localStorage.getItem('solar:last_agent_mood') || null;
        if (mood) localStorage.removeItem('solar:last_agent_mood');
      } catch (_) { /* graceful */ }
      const payload = {
        frequency: Number.isFinite(state.frequency) ? state.frequency : undefined,
        waveform: state.waveform || undefined,
        binaural: Number.isFinite(state.binaural) ? state.binaural : undefined,
        ambient: ambientClean,
        soundscape: activeSoundscape || undefined,
        duration_planned_seconds: plannedSec || undefined,
        duration_actual_seconds: actualSec,
        mood: mood || undefined,
        extended: false,           // wired for future Extend +5 chip
        ended_early,
        agent_initiated: !!assistantOwnedRef.current,
        // Phase 12c — mark sessions triggered by the Wellness Assistant as
        // HB-recommended when the user is a Pro (Assistant weighs HB gaps
        // in its suggestions). The 24-hour follow-up prompt reads this
        // flag to decide whether to ask the impact-rating question.
        hb_recommended: !!(assistantOwnedRef.current && isPro),
        hb_source: assistantOwnedRef.current ? 'assistant_gap' : undefined,
      };
      const { data } = await api.post('/me/journey/log', payload);
      if (data && data.ok && data.entry && data.entry.id) {
        loggedId = data.entry.id;
        setLastJourneyEntryId(loggedId);
      }
    } catch (e) {
      console.warn('[Dashboard] journey log failed', e);
    } finally {
      // Open the post-session reflection card AFTER the log resolves so the
      // card has a valid entry_id to attach the reflection to. Even if the
      // POST failed, we still surface step 1 ("How are you feeling now?");
      // step 2 gracefully falls back to closing when entry_id is null.
      if (pendingCheckinRef.current) {
        pendingCheckinRef.current = false;
        // Freeze the just-completed session's metadata so the optional
        // "Share your session" card renders consistently even if the user
        // changes frequency / visualizer between the check-in appearing
        // and tapping share. Look-ups mirror the "Now Tuning" label logic
        // (SOLFEGGIO/SPECIALS/soundscape/flow stage) so the card matches
        // what the user saw on-screen during playback.
        try {
          const hz = Number(state.frequency);
          const solf = SOLFEGGIO.find((p) => p.hz === Math.round(hz));
          const spec = SPECIALS.find((p) => Math.abs(p.hz - hz) < 0.05);
          const scape = activeSoundscape ? SOUNDSCAPES.find((s) => s.key === activeSoundscape) : null;
          const flowMeta = flowStage?.meta || null;
          const name = flowMeta?.name || scape?.name || solf?.name || spec?.name || `${hz.toFixed(1)} Hz`;
          const desc = flowMeta?.sub || solf?.desc || spec?.desc || null;
          const visLabelMap = { rings: 'Rings · Waves', chladni: 'Chladni · Cymatics', ripples: 'Ripples · Resonance' };
          const flowJourneyLabel = activeFlowJourney?.label
            ? `${activeFlowJourney.label} Journey`
            : null;
          setShareSession({
            frequencyHz: Number.isFinite(hz) ? hz : 0,
            frequencyName: name,
            frequencyDesc: desc,
            durationMin: Math.max(1, Math.round((actualMs || 0) / 60000)),
            visualLabel: visLabelMap[visualMode] || 'Rings · Waves',
            journeyLabel: flowJourneyLabel,
          });
        } catch (e) { console.warn('[Dashboard] share snapshot failed', e); }
        setCheckinOpen(true);
        // Also drop a post-session check-in nudge into the notification
        // center so users who dismiss the inline card can still find their
        // way back to reflection. Backend gates for prefs + quiet hours.
        try { api.post('/me/notifications/checkin-nudge', { trigger: 'post_session' }).catch(() => {}); } catch (_) {}
        // Warm-moment push opt-in: after a completed session (Smart Fade
        // ran to zero), invite the user to enable browser push. The slip
        // is silent if push is unsupported, already granted/denied, or
        // dismissed in the last 7/60 days.
        try { setTimeout(() => requestPushOptInIfWarm(), 4200); } catch (_) {}
      }
    }
  }, [state.frequency, state.waveform, state.binaural, state.ambient, activeSoundscape, visualMode, flowStage, activeFlowJourney]);

  useEffect(() => {
    if (state.playing && !sessionStart) {
      setSessionStart(Date.now());
      setCheckedInThisRun(false);
      journeyLoggedThisRunRef.current = false;
      // Fresh session — a stale reflection id from the previous session
      // must not persist, otherwise the next Smart-Fade completion would
      // attach the reflection to the wrong journey row.
      setLastJourneyEntryId(null);
      // Snapshot planned duration ONLY if a timer is currently running;
      // freeform (no-timer) sessions get `null` and never trigger the
      // ended_early flag.
      journeyPlannedRef.current = remaining > 0 ? remaining : null;
    }
    if (!state.playing && sessionStart) {
      const elapsedMs = Date.now() - sessionStart;
      const minutes = elapsedMs / 60000;
      if (minutes >= 1 && !checkedInThisRun) {
        checkIn(minutes);
        setCheckedInThisRun(true);
      }
      if (minutes >= 1 && !journeyLoggedThisRunRef.current) {
        journeyLoggedThisRunRef.current = true;
        logJourneyEntry(elapsedMs);
      }
      setSessionStart(null);
      journeyPlannedRef.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // ---- Wellness Assistant sheet (controlled by Dashboard) ------------------------
  // Greeting on each login is personalised ("Hello [Name], how are you feeling
  // right now?"). PRIOR_INSIGHTS (last 3 mood→suggestion check-ins) are
  // injected into the LLM prompt on the BACKEND, so a returning user who said
  // they were anxious last time may hear the agent reference 396 Hz again
  // naturally.
  //
  // Trigger model: opens exactly ONCE per React mount of the dashboard for an
  // authenticated user. Because the dashboard remounts on every login (and on
  // every page reload of an already-authed user), the user gets greeted again
  // on every fresh session start — which matches a wellness-companion UX
  // better than a one-time browser-session gate. A `greetedRef` prevents the
  // effect from re-opening the sheet if the `user` object reference changes
  // mid-mount (e.g. profile name edit).
  //
  // Manual open via the "Wellness Assistant" header button uses a neutral
  // "How can I help you?" greeting instead.
  //
  // Auto-greet — one soft "hello" per calendar day per user. Combines what
  // used to be two surfaces:
  //   1. the tiny top-of-page PatternGreetingChip (removed — was clipped by
  //      mobile Safari's URL bar and truncated its own message)
  //   2. the generic "How are you feeling right now?" greeting.
  //
  // When a live pattern exists, the pattern's warm callback becomes the
  // greeting text and its CTA (if any) surfaces as a Start-there suggestion
  // inside the sheet. When no pattern is present we fall back to the
  // generic greeting. Skipped entirely while audio is playing.
  //
  // localStorage key `solar:lastGreetedYmd:<userId>` records the last date
  // (YYYY-MM-DD) we greeted this user on THIS browser. Same day → skip.
  // Different day OR no entry → greet + record. Manual open via the header
  // button always uses the neutral "How can I help you?" greeting.
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentGreeting, setAgentGreeting] = useState('');
  const [agentInitialSuggestion, setAgentInitialSuggestion] = useState(null);
  useEffect(() => {
    if (!user) return;
    if (state.playing) return;
    const key = `solar:lastGreetedYmd:${user.id || user.email || 'anon'}`;
    const today = new Date().toISOString().slice(0, 10);
    let lastYmd = null;
    try { lastYmd = localStorage.getItem(key); } catch (_) { /* graceful */ }
    if (lastYmd === today) return;
    const name = (user.name || '').trim();
    // Fetch patterns first — pick the top undismissed one to shape the
    // greeting. Failure falls through to the generic version. Fire-and-
    // forget within a 900ms cap so mobile users never wait on an idle
    // network before the sheet appears.
    let cancelled = false;
    const stagger = setTimeout(async () => {
      if (audioEngine.playing) return;   // re-check at fire time
      let patternMsg = null;
      let patternCta = null;
      let patternKey = null;
      try {
        const pRes = await Promise.race([
          api.get('/me/patterns'),
          new Promise((resolve) => setTimeout(() => resolve(null), 900)),
        ]);
        if (pRes && pRes.data) {
          const dismissed = new Set(pRes.data.dismissed || []);
          const PRIORITY = { mood_at_time: 5, extension_favorite: 4, top_frequency: 3, preferred_time_of_day: 2, unused_soundscapes: 1 };
          const top = (pRes.data.patterns || [])
            .filter((p) => !dismissed.has(p.key))
            .sort((a, b) => (PRIORITY[b.kind] || 0) - (PRIORITY[a.kind] || 0))[0];
          if (top) {
            patternMsg = top.message;
            patternCta = top.cta || null;
            patternKey = top.key;
          }
        }
      } catch (_) { /* graceful — fall back to generic greeting */ }
      if (cancelled) return;
      try { localStorage.setItem(key, today); } catch (_) { /* graceful */ }
      // Compose the greeting. Pattern callbacks always start with the
      // user's name so it feels personal, not systemic.
      if (patternMsg) {
        setAgentGreeting(name ? `Hi ${name} — ${patternMsg}` : patternMsg);
      } else {
        setAgentGreeting(name ? `Hi ${name}, how are you feeling right now?` : 'Hi, how are you feeling right now?');
      }
      // If the pattern carried a CTA that maps to something the sheet knows
      // how to render (preset / soundscape / frequency), attach it as a
      // one-tap suggestion inside the first assistant message.
      if (patternCta) {
        const s = _patternCtaToSuggestion(patternCta);
        setAgentInitialSuggestion(s);
      } else {
        setAgentInitialSuggestion(null);
      }
      // If we surfaced a pattern, mark it dismissed server-side so it doesn't
      // reappear in another surface tomorrow — the greeting IS the surface.
      if (patternKey) {
        api.post(`/me/patterns/${encodeURIComponent(patternKey)}/dismiss`).catch(() => {});
      }
      setAgentOpen(true);
    }, 1200);
    return () => { cancelled = true; clearTimeout(stagger); };
  }, [user, state.playing]);
  const openCompanion = useCallback(() => {
    setAgentGreeting('How can I help you?');
    setAgentInitialSuggestion(null);
    setAgentOpen(true);
  }, []);

  // ---- PWA install -------------------------------------------------------
  // The hook exposes three signals: canPrompt (Android/Chromium ready to
  // install via beforeinstallprompt), isIOS (Safari → manual instructions),
  // and isInstalled (already running standalone). When isInstalled is true
  // the install affordance disappears entirely.
  const { canPrompt, isIOS: isInstallIOS, isInstalled, promptInstall } = usePWAInstall();
  const [installOpen, setInstallOpen] = useState(false);
  // Show the install affordance whenever the app is not already running
  // standalone. The modal itself routes to the platform-appropriate path
  // (native prompt / iOS share-sheet instructions / desktop fallback).
  const showInstall = !isInstalled;

  // ---- Pulsing Haptics --------------------------------------------------
  // Single subscription to the audio engine — hapticEngine then auto-starts
  // on play and stops on pause/sleep-end. Stays a no-op on unsupported
  // devices (iOS Safari / iOS standalone) so audio is never blocked.
  const [hapticsOpen, setHapticsOpen] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  // ---- Account dropdown menu --------------------------------------------
  // Consolidates Pulsing Haptics, Voice Shortcuts, Install Solarisound, and
  // Equalizer Calibration behind the Account icon to keep the header from
  // sprawling. Opens on click, closes on outside-click or Escape, and
  // collapses any item that doesn't apply (e.g. Install hides when the app
  // is already running standalone).
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = React.useRef(null);
  // ---- Notifications preferences modal ----------------------------------
  const [notifPrefsOpen, setNotifPrefsOpen] = useState(false);
  // Deferred ref so `handleNotificationNavigate` can call toggleBreathwork
  // without a TDZ error (the const is declared ~500 lines below).
  const toggleBreathworkRef = React.useRef(null);
  // Notification-tap → explicit breathwork START (never toggle). Deferred
  // ref for the same TDZ reason.
  const startBreathworkNotifRef = React.useRef(null);
  // Listening guide modal — accessed via the info icon in the player.
  const [listeningGuideOpen, setListeningGuideOpen] = useState(false);
  // Extended headphone reminder — fires once per 24h when binaural,
  // isochronic, OR Golden Stack is active on the current session.
  const headphoneReminder = useHeadphoneReminder({
    playing: state.playing,
    binaural: state.binaural,
    isochronic: state.isochronic,
    goldenStack: state.goldenStack,
  });
  // Handles taps on in-app notification cards (or lock-screen push clicks
  // relayed via `notification-click` postMessage from the Service Worker).
  // We interpret `destination` in an app-native way rather than reload the
  // page: /play?frequency=<hz> arms and starts that frequency;
  // /account switches to the Account view; hash routes open in-app sheets:
  //   #wellness-assistant → open the Assistant sheet
  //   #harmonic-blueprint → open the Harmonic Blueprint sheet
  //   #notification-preferences → open the notification prefs modal
  //   #account → same as /account
  const handleNotificationNavigate = React.useCallback((destination) => {
    if (!destination || typeof destination !== 'string') return;
    const raw = destination.trim();
    // Hash routes for in-app sheets
    if (raw.startsWith('#')) {
      const key = raw.slice(1).toLowerCase();
      if (key === 'wellness-assistant') { openCompanion && openCompanion(); return; }
      if (key === 'harmonic-blueprint') { setHbOpen && setHbOpen(true); return; }
      if (key === 'notification-preferences' || key === 'notifications') { setNotifPrefsOpen(true); return; }
      if (key === 'account') { onOpenAccount && onOpenAccount(); return; }
      if (key === 'breathwork') {
        // Accept the "take a breath together" invitation — explicit
        // start (not toggle) so a stuck-on-true breathwork state can't
        // silently turn it OFF. Audio start happens synchronously
        // inside the same click gesture to keep iOS Safari's
        // AudioContext.resume() gesture-eligible.
        try { startBreathworkNotifRef.current && startBreathworkNotifRef.current(); } catch (_) { /* graceful */ }
        return;
      }
      if (key === 'listening-guide') { setListeningGuideOpen(true); return; }
      return;
    }
    try {
      const isAbs = raw.startsWith('http');
      const u = isAbs ? new URL(raw) : new URL(raw, window.location.origin);
      const p = u.pathname;
      const params = u.searchParams;
      if (p.startsWith('/account') && onOpenAccount) { onOpenAccount(); return; }
      if (p.startsWith('/play')) {
        const hz = parseFloat(params.get('frequency') || '');
        if (!isNaN(hz) && hz > 0) {
          try {
            audioEngine.setFrequency(hz);
            audioEngine.start();
          } catch (_) { /* graceful */ }
        }
      }
    } catch (_) { /* invalid destination — no-op */ }
  }, [onOpenAccount, openCompanion]);
  // Listen for Service Worker relays (push notification taps while app is open).
  useEffect(() => {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return undefined;
    const onMsg = (evt) => {
      if (evt?.data?.type === 'notification-click') {
        const dest = evt.data.destination;
        if (dest && dest.startsWith('/play')) handleNotificationNavigate(dest);
        else if (dest && dest.startsWith('/account') && onOpenAccount) onOpenAccount();
      }
    };
    navigator.serviceWorker.addEventListener('message', onMsg);
    return () => navigator.serviceWorker.removeEventListener('message', onMsg);
  }, [handleNotificationNavigate, onOpenAccount]);
  // Inactivity-based check-in nudge — fires once per browser session after
  // 12 minutes of no user interaction, and only when audio is NOT actively
  // playing (never interrupts a session). Backend still gates on prefs +
  // quiet hours + daily cap, so pushy behaviour is impossible even if the
  // client misfires.
  const inactivityFiredRef = React.useRef(false);
  const inactivityTimerRef = React.useRef(null);
  useEffect(() => {
    const IDLE_MS = 12 * 60 * 1000;
    const reset = () => {
      if (inactivityFiredRef.current) return;
      if (inactivityTimerRef.current) clearTimeout(inactivityTimerRef.current);
      inactivityTimerRef.current = setTimeout(() => {
        if (inactivityFiredRef.current) return;
        // Guard: never fire mid-playback.
        try { if (audioEngine.getState && audioEngine.getState().playing) return; } catch (_) {}
        inactivityFiredRef.current = true;
        try { api.post('/me/notifications/checkin-nudge', { trigger: 'inactivity' }).catch(() => {}); } catch (_) {}
      }, IDLE_MS);
    };
    const activityEvents = ['mousemove', 'keydown', 'pointerdown', 'touchstart', 'scroll'];
    activityEvents.forEach((e) => window.addEventListener(e, reset, { passive: true }));
    reset();
    return () => {
      activityEvents.forEach((e) => window.removeEventListener(e, reset));
      if (inactivityTimerRef.current) clearTimeout(inactivityTimerRef.current);
    };
  }, []);
  useEffect(() => {
    if (!accountMenuOpen) return undefined;
    const onDocClick = (e) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(e.target)) {
        setAccountMenuOpen(false);
      }
    };
    const onKey = (e) => { if (e.key === 'Escape') setAccountMenuOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [accountMenuOpen]);
  // ---- Hearing-profile calibration --------------------------------------
  // On mount we fetch the user's saved profile (if any) and immediately
  // apply it to the audio engine so every subsequent tone goes through the
  // personal EQ chain. The calibration modal is NOT auto-opened on mount —
  // it's triggered by the OnboardingTransitionCard 30 s after the user
  // accepts a suggestion (so they're in a calm state before the test).
  const [calibrationOpen, setCalibrationOpen] = useState(false);
  const [hasProfile, setHasProfile] = useState(false);   // real calibration exists
  const [otcOpen, setOtcOpen] = useState(false);
  const [otcHeadphones, setOtcHeadphones] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get('/me/hearing-profile');
        if (cancelled) return;
        if (data && data.bands && data.bands.length > 0 && !data.skipped) {
          audioEngine.setHearingProfile(data);
          setHasProfile(true);
        } else {
          setHasProfile(false);
        }
      } catch (e) {
        console.warn('[Dashboard] hearing-profile fetch failed', e);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ---- Onboarding transition flow ---------------------------------------
  // Wired to the AIAgentSheet's `sf:agent:suggestion-taken` event. ~30 s
  // after a suggestion is tapped, we layer a soft uncalibrated 432 Hz
  // baseline tone under whatever the user is hearing, run a best-effort
  // headphone detection, and slide up the OnboardingTransitionCard with
  // the guidance + calibration pivot. The card is shown on EVERY login
  // (per the user's spec) — the copy adapts when the user is already
  // calibrated so it reads as a polite "fine-tune anytime" nudge rather
  // than nagging.
  const transitionTimerRef = React.useRef(null);
  useEffect(() => {
    const onSuggestion = (e) => {
      // Arm the player's countdown for a default 5-minute session when the
      // user accepts a playback-style suggestion. This is the auto-timer
      // the user asked for — it doesn't interfere with Sleep Mode (which
      // already has its own longer duration) or Wellness Prescription (which
      // just opens a panel rather than starting playback).
      const kind = e?.detail?.kind;
      const detail = e?.detail || {};
      const armsTimer = (
        kind === 'preset' ||
        kind === 'soundscape' ||
        kind === 'frequency' ||
        // haptic_combo only arms the 5-min timer when the LLM didn't supply a
        // duration_min that matches a known Sleep-Mode duration (those routes
        // through startSleepMode, which sets its own remaining).
        (kind === 'haptic_combo' && ![30, 60, 120, 240, 480].includes(detail.duration_min))
      );
      if (armsTimer) {
        // Product spec: assistant-triggered sessions default to 10 minutes
        // so the user has enough time for the recommendation to actually
        // land. Users can still manually adjust up/down mid-session via
        // the normal timer controls.
        const DEFAULT_MIN = 10;
        setDuration(DEFAULT_MIN);
        setRemaining(DEFAULT_MIN * 60);
        // Also stamp the wall-clock end on the audioEngine singleton so
        // the countdown is authoritative even if state.playing hasn't yet
        // propagated by the time this event handler runs. Without this,
        // a fast user tap or a slow React commit can leave `remaining`
        // set but `state.playing=false`, so the countdown effect returns
        // early and the timer visibly sticks at 00:00. Wall-clock stamp
        // makes the countdown survive that race and any subsequent
        // Dashboard remount.
        try { audioEngine.sessionEndAt = Date.now() + DEFAULT_MIN * 60 * 1000; } catch (_) {}
        // Assistant-triggered session is a fresh start → clear the
        // divergence flag and apply the per-frequency ideal default
        // volume for the frequency the assistant just loaded.
        userAdjustedVolumeRef.current = false;
        const targetHz = (typeof detail.frequency === 'number' && detail.frequency > 0)
          ? detail.frequency
          : audioEngine.frequency;
        try { applyIdealVolume(audioEngine, targetHz); } catch (_) { /* graceful */ }
        // Mark this session as Wellness-Assistant-owned so the timer-expiry
        // branch knows to surface the "How are you feeling now?" check-in
        // once the fade completes.
        assistantOwnedRef.current = true;
      }
      // Reset any prior pending transition timer (e.g. user picked twice in a session).
      if (transitionTimerRef.current) clearTimeout(transitionTimerRef.current);
      transitionTimerRef.current = setTimeout(async () => {
        try {
          // Best-effort headphone detection; default false on unsupported.
          const hp = await detectHeadphones();
          setOtcHeadphones(!!hp);
          // Soft uncalibrated 432 Hz baseline. Volume kept low so it sits
          // gently under the user's chosen session.
          try { audioEngine.setBaseline(432, 0.05); } catch (err) { /* graceful */ }
          setOtcOpen(true);
        } catch (err) {
          console.warn('[Dashboard] onboarding transition failed', err);
        }
      }, 30000);
    };
    window.addEventListener('sf:agent:suggestion-taken', onSuggestion);
    // Warm-moment push opt-in: peak value moment for a first-time user is
    // right after they've asked the Assistant for guidance and tapped an
    // audio suggestion. 8s delay so the tone has actually landed + they've
    // heard it, not the moment they tapped a button.
    const onSuggestionForPush = () => {
      setTimeout(() => { try { requestPushOptInIfWarm(); } catch (_) {} }, 8000);
    };
    window.addEventListener('sf:agent:suggestion-taken', onSuggestionForPush);
    return () => {
      window.removeEventListener('sf:agent:suggestion-taken', onSuggestion);
      window.removeEventListener('sf:agent:suggestion-taken', onSuggestionForPush);
      if (transitionTimerRef.current) clearTimeout(transitionTimerRef.current);
    };
  }, []);

  const dismissTransitionCard = useCallback(() => {
    setOtcOpen(false);
    // Fade out the baseline tone so the user is left with their original
    // session. Engine handles the fade internally.
    try { audioEngine.setBaseline(null); } catch (e) { /* */ }
  }, []);

  const startCalibrationFromTransition = useCallback(() => {
    setOtcOpen(false);
    // Leave the baseline running while the modal is open — it's part of
    // the calm-cooperative state the user is in. Modal close handler will
    // tear it down.
    setCalibrationOpen(true);
  }, []);

  const closeCalibration = useCallback(() => {
    setCalibrationOpen(false);
    try { audioEngine.setBaseline(null); } catch (e) { /* */ }
  }, []);

  // ---- Wellness Assistant post-session check-in --------------------------
  // Handlers for the "How are you feeling now?" card that surfaces after a
  // Wellness-Assistant-triggered 5-minute session fully fades to silence.
  const dismissCheckin = useCallback(() => {
    setCheckinOpen(false);
    assistantOwnedRef.current = false;
  }, []);

  const continueCheckin = useCallback(() => {
    // Extend the session by 10 more minutes: re-arm the audio engine with
    // the SAME configuration the user was hearing (nothing was mutated on
    // stop besides the transport), arm the countdown, and keep the
    // assistantOwnedRef flag so a subsequent natural expiry surfaces another
    // check-in if the user wants to keep extending.
    setCheckinOpen(false);
    const EXTEND_MIN = 10;
    (async () => {
      try {
        if (!audioEngine.playing) await audioEngine.start();
        setDuration(EXTEND_MIN);
        setRemaining(EXTEND_MIN * 60);
      } catch (e) { console.warn('[Dashboard] checkin continue failed', e); }
    })();
  }, []);
  useEffect(() => {
    haptic.attachToAudio();
    return () => { haptic.stop(); };
  }, []);
  // Sleep-mode hint into the haptic engine so the heartbeat pattern can
  // slow to 50 bpm when the user is winding down for the night.
  useEffect(() => { haptic.setSleepHint(sleepMode); }, [sleepMode]);

  // ---- AI Agent Sheet → Sleep Mode bridge -----------------------------------
  // The AIAgentSheet dispatches a window event when the user taps a "sleep"
  // suggestion. We re-route it here so the agent can leverage the existing
  // startSleepMode logic (Pro gating, fade orchestration, prefs persistence).
  // Using a ref keeps the listener stable while always invoking the freshest
  // closure of startSleepMode + setSleepDurationMin.
  const sleepHandoffRef = React.useRef(null);
  useEffect(() => {
    const onAgentSleep = (e) => {
      const d = e?.detail?.duration_min;
      if (typeof d === 'number' && SLEEP_DURATIONS.includes(d)) {
        setSleepDurationMin(d);
      }
      // Defer one tick so React commits the duration before we read it inside startSleepMode
      setTimeout(() => { sleepHandoffRef.current && sleepHandoffRef.current(); }, 0);
    };
    window.addEventListener('sf:agent:sleep', onAgentSleep);
    return () => window.removeEventListener('sf:agent:sleep', onAgentSleep);
  }, []);
  // Wellness Prescription pre-fill handoff: AIAgentSheet calls this to open the
  // AI panel with the user's intent already typed in.
  const triggerAIPrescription = useCallback((intent) => {
    setAiOpen(true);
    if (typeof intent === 'string' && intent.trim()) {
      setAiIntent(intent.trim());
    }
    // Scroll the right column so the user sees the pre-filled panel.
    setTimeout(() => {
      const el = document.querySelector('[data-testid="ai-recommend-panel"]');
      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 50);
  }, []);

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
          if (typeof data.isochronic === 'number') audioEngine.setIsochronic(data.isochronic);
          if (typeof data.golden_stack === 'boolean') audioEngine.setGoldenStack(data.golden_stack);
          if (typeof data.breathwork === 'boolean') setBreathwork(data.breathwork);
          if (typeof data.visual_mode === 'string' && ['rings', 'chladni', 'ripples'].includes(data.visual_mode)) {
            setVisualMode(data.visual_mode);
          }
          if (typeof data.sleep_duration_min === 'number' && SLEEP_DURATIONS.includes(data.sleep_duration_min)) {
            setSleepDurationMin(data.sleep_duration_min);
          }
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
        payload.isochronic = state.isochronic || 0;
        payload.golden_stack = !!state.goldenStack;
        payload.breathwork = !!breathwork;
        payload.ambient = state.ambient && { ...state.ambient };
        payload.visual_mode = visualMode;
        payload.sleep_duration_min = sleepDurationMin;
      }
      api.put('/me/prefs', payload).catch((e) => console.warn('[Dashboard] prefs save failed', e));
    }, 1200);
    return () => { if (prefsSaveTimerRef.current) clearTimeout(prefsSaveTimerRef.current); };
  }, [state.frequency, state.waveform, state.binaural, state.isochronic, state.toneVolume, state.goldenStack, state.ambient, duration, breathwork, visualMode, sleepDurationMin, isPro]);

  const togglePlay = () => {
    if (!audioEngine.playing) {
      // Fresh session start → clear the "user has diverged" flag so the
      // per-frequency ideal default volume applies to this session. If the
      // user hasn't diverged from the ideal (or the flag is already clear),
      // apply the ideal now so the level matches whatever frequency is
      // currently loaded on the engine.
      userAdjustedVolumeRef.current = false;
      try { applyIdealVolume(audioEngine, audioEngine.frequency); } catch (_) { /* graceful */ }
      setRemaining(duration * 60);
      audioEngine.start();
    } else {
      // Stop any active Sound Bath alongside the main engine so a Pause tap
      // from the player halts EVERYTHING the user was hearing.
      try { getSoundBath(audioEngine).stop(); } catch (e) { /* graceful */ }
      audioEngine.stop();
      setRemaining(0);
      // Manual stop bypasses the natural fade path, so don't queue a
      // "how are you feeling now?" check-in on the next fade completion.
      assistantOwnedRef.current = false;
      // Explicit stop ends the session → next start re-applies the ideal.
      userAdjustedVolumeRef.current = false;
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
    // Per-frequency ideal default volume: apply for this new selection when
    // (a) the engine isn't currently playing (fresh session about to start)
    //     — always apply ideal for the new hz.
    // (b) engine IS playing but user hasn't diverged yet — retune to the
    //     new frequency's ideal so higher/lower tones don't feel harsh.
    // If the user has manually adjusted volume mid-session, respect it.
    if (!audioEngine.playing || !userAdjustedVolumeRef.current) {
      // Starting fresh clears the flag; retuning mid-session leaves it as-is.
      if (!audioEngine.playing) userAdjustedVolumeRef.current = false;
      try { applyIdealVolume(audioEngine, hz); } catch (_) { /* graceful */ }
    }
    if (!audioEngine.playing) {
      setRemaining(duration * 60);
      audioEngine.start();
    }
  };

  const toggleGoldenStack = () => {
    if (!isPro) { onOpenAccount(); return; }
    audioEngine.setGoldenStack(!state.goldenStack);
  };

  // (Pattern-CTA arming used to live here for the removed
  // PatternGreetingChip. The pattern-driven greeting now surfaces
  // inside the AIAgentSheet where suggestions are applied via
  // applySuggestion — so this Dashboard-level handler is no longer
  // needed. If a future surface reintroduces the CTA elsewhere, mirror
  // the same logic used by AIAgentSheet.applySuggestion.)

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
    setDuration(sleepDurationMin);
    setRemaining(sleepDurationMin * 60);
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

  // Keep the agent-sleep handoff ref pointing at the latest startSleepMode.
  // Reassigned every render so the listener always invokes the freshest closure
  // (which captures the current sleepDurationMin / isPro).
  sleepHandoffRef.current = startSleepMode;

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
    // Fresh soundscape session → clear divergence flag and apply the
    // per-frequency ideal default volume. Preserves relative ambient mix
    // (which is set by the soundscape's own ambient dict below).
    userAdjustedVolumeRef.current = false;
    try { applyIdealVolume(audioEngine, s.freq); } catch (_) { /* graceful */ }
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
    setBreathwork((prev) => {
      const next = !prev;
      if (next) {
        // Turning ON: start the engine synchronously *inside the click
        // gesture* so iOS Safari doesn't lose the user-gesture privilege
        // for AudioContext.resume(). Avoid the async IIFE / await here —
        // any microtask hop between the click and start() can cause
        // silent audio failure on iOS.
        if (!audioEngine.playing) {
          try { audioEngine.start(); } catch (e) { console.warn('[Dashboard] breathwork start failed', e); }
          setRemaining(duration * 60);
        }
      } else {
        // Turning OFF: single-tap stop should undo everything the ON-tap
        // started — silence audio, halt any sound-bath, clear the timer.
        try { getSoundBath(audioEngine).stop(); } catch (e) { /* graceful */ }
        try { audioEngine.stop(); } catch (e) { /* graceful */ }
        setRemaining(0);
      }
      return next;
    });
  };

  // Explicit "start breathwork now" — used by the notification tap
  // ("Take a breath together") where the user has clearly asked to
  // begin. Guarantees breathwork=true and audio playing rather than
  // toggling relative to whatever state Dashboard happened to be in.
  const startBreathworkFromNotification = React.useCallback(() => {
    if (!isPro) { onOpenAccount(); return; }
    // Sync audio start FIRST — inside the click gesture chain from the
    // notification CTA — so iOS Safari accepts the AudioContext.resume().
    if (!audioEngine.playing) {
      try { audioEngine.start(); } catch (e) { console.warn('[Dashboard] breathwork notif-start failed', e); }
      setRemaining(duration * 60);
    }
    setBreathwork(true);
  }, [isPro, onOpenAccount, duration]);

  const onCustomFreqChange = (e) => {
    if (!isPro) { onOpenAccount(); return; }
    audioEngine.setFrequency(parseFloat(e.target.value));
  };

  // Publish toggleBreathwork through the ref so hash-route destinations
  // pointing at `#breathwork` (fired from the notification reader) can
  // invoke it without a TDZ reference issue.
  useEffect(() => {
    toggleBreathworkRef.current = toggleBreathwork;
    startBreathworkNotifRef.current = startBreathworkFromNotification;
  });

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
    // Non-Pro users can see their saved sessions but tapping any locked row
    // opens the calm upgrade prompt instead — sessions themselves are never
    // deleted or modified, so a Pro upgrade instantly restores access.
    if (!isPro) { setSessionUnlockOpen(true); return; }
    // Stop any current playback so a saved session always starts from a
    // clean slate (no residual soundscape / bath / smart-fade state).
    try { getSoundBath(audioEngine).stop(); } catch (e) { /* graceful */ }
    if (audioEngine.playing) audioEngine.stop();

    audioEngine.setFrequency(s.frequency);
    audioEngine.setWaveform(s.waveform);
    audioEngine.setBinaural(s.binaural || 0);
    // Loading a saved session is a fresh session start. If the saved
    // session stored a specific tone volume via prefs (handled elsewhere
    // via /me/prefs restoration), that will take precedence when the
    // engine's next state emits; otherwise the per-frequency ideal
    // default applies here.
    userAdjustedVolumeRef.current = false;
    try { applyIdealVolume(audioEngine, s.frequency); } catch (_) { /* graceful */ }
    const mins = s.duration_minutes || 10;
    setDuration(mins);
    setBreathwork(!!s.breathwork);
    Object.entries(s.ambient || {}).forEach(([k, v]) => audioEngine.setAmbient(k, v));

    // Auto-start playback and arm the countdown to THIS session's saved
    // duration — the player transport (Play/Pause icon + countdown widget)
    // becomes the single source of truth for the loaded session. Stopping
    // from the player halts everything, exactly like a fresh session.
    (async () => {
      try {
        // Small delay so the previous stop()'s toneGain fade has room to
        // settle before we spin up the new oscillator — avoids brief overlap.
        await new Promise((r) => setTimeout(r, s.sound_bath ? 0 : 120));
        if (!audioEngine.playing) await audioEngine.start();
        setRemaining(mins * 60);
        // If this session bookmarked a Sound Bath preset, relaunch it now.
        // Random note stream isn't seed-replayable, but the preset key is — so
        // a fresh arrangement of the same bath fires alongside the base tone.
        if (s.sound_bath && s.sound_bath.preset_key && isPro) {
          const bath = getSoundBath(audioEngine);
          await bath.start(s.sound_bath.preset_key);
        }
      } catch (e) { console.warn('[Dashboard] loadSession start failed', e); }
    })();
  };

  // Save the currently-playing Sound Bath as a bookmarkable arrangement.
  const saveBathArrangement = async (presetKey, label) => {
    const stamp = new Date().toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    await api.post('/sessions', {
      name: `${label} · ${stamp}`,
      frequency: state.frequency,
      waveform: state.waveform,
      binaural: 0,
      duration_minutes: duration,
      ambient: {},
      breathwork: false,
      sound_bath: { preset_key: presetKey, label },
    });
    refreshSessions();
  };

  const deleteSession = async (id) => {
    try { await api.delete(`/sessions/${id}`); refreshSessions(); } catch (e) { console.warn('[Dashboard] delete session failed', e); }
  };

  // ---- AI Frequency Recommendation (Pro) -----------------------------------
  // Calls /api/me/ai-recommend with the user's intent + optional mood/goal.
  // Applies the returned prescription to the audio engine immediately so the
  // user hears the result the moment they tap Play (or right now if a session
  // is already in progress).
  const runAiRecommend = async () => {
    if (!isPro) {
      setAiErr('AI prescriptions are a Pro feature.');
      return;
    }
    const intent = aiIntent.trim();
    if (intent.length < 2) {
      setAiErr('Tell me how you want to feel.');
      return;
    }
    setAiErr('');
    setAiLoading(true);
    try {
      const { data } = await api.post('/me/ai-recommend', {
        intent,
        mood: aiMood.trim() || undefined,
        goal: aiGoal.trim() || undefined,
        duration_min: duration,
      });
      // Apply to engine — order matters: clear opposing modulation first so
      // the new prescription isn't fighting stale state from a prior session.
      audioEngine.setBinaural(0);
      audioEngine.setIsochronic(0);
      audioEngine.setFrequency(data.frequency);
      audioEngine.setWaveform(data.waveform || 'sine');
      // AI prescriptions are always a fresh session → apply the per-freq
      // ideal default volume and clear the divergence flag.
      userAdjustedVolumeRef.current = false;
      try { applyIdealVolume(audioEngine, data.frequency); } catch (_) { /* graceful */ }
      if (data.binaural) audioEngine.setBinaural(data.binaural);
      if (data.isochronic) audioEngine.setIsochronic(data.isochronic);
      if (data.golden_stack) audioEngine.setGoldenStack(true);
      else audioEngine.setGoldenStack(false);
      // Reset all ambient layers to 0, then apply the recommended mix so we
      // don't accumulate leftovers from a previous prescription.
      ['rain', 'ocean', 'forest', 'wind', 'crickets', 'bowls', 'brown', 'white']
        .forEach((k) => audioEngine.setAmbient(k, 0));
      Object.entries(data.ambient || {}).forEach(([k, v]) => audioEngine.setAmbient(k, v));
      setActiveSoundscape(null);
      if (data.duration_min) setDuration(data.duration_min);
      setAiResult(data);
      // PLAYER CONTRACT: hand control over to the player so the UI shows the
      // correct Pause icon and a single tap can stop everything. If the user
      // is already mid-session, start() is a no-op — the new prescription was
      // hot-swapped into the running engine by the setFrequency / setBinaural
      // / setIsochronic / setAmbient calls above.
      if (!audioEngine.playing) {
        try { await audioEngine.start(); } catch (e) { console.warn('[Dashboard] AI start failed', e); }
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'AI prescription failed';
      setAiErr(typeof msg === 'string' ? msg : 'AI prescription failed');
    } finally {
      setAiLoading(false);
    }
  };

  const activePreset = useMemo(
    () => SOLFEGGIO.find((p) => p.hz === Math.round(state.frequency)),
    [state.frequency]
  );

  // Push live "now playing" metadata to the Media Session so the device's
  // lock-screen / notification controls show what the user is tuning to.
  useEffect(() => {
    const special = SPECIALS.find((p) => Math.abs(p.hz - state.frequency) < 0.05);
    const scape = activeSoundscape
      ? SOUNDSCAPES.find((s) => s.key === activeSoundscape)
      : null;
    const label = scape
      ? scape.name
      : (activePreset?.name || special?.name || `${state.frequency.toFixed(1)} Hz`);
    const subtitle = scape ? `${scape.name}` : `${state.frequency.toFixed(1)} Hz · ${label}`;
    audioEngine.setMediaInfo({
      title: 'Healing Frequencies',
      subtitle,
    });
  }, [state.frequency, activePreset, activeSoundscape]);

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
          {/* Header card uses `z-30` so the Account dropdown menu (rendered
              inside this card with z-50) escapes the stacking context of
              the sibling Solfeggio Presets card below — otherwise the
              dropdown renders BEHIND that card because the `glass` blur on
              each sibling creates its own stacking context. */}
          <div className="glass p-6 relative z-30">
            <div className="flex items-center justify-between mb-1">
              <div className="label-tiny">Healing Frequencies</div>
              <div className="flex items-center gap-3">
                <NotificationBell
                  testid="notification-bell"
                  onNavigate={handleNotificationNavigate}
                  onOpenPreferences={() => setNotifPrefsOpen(true)}
                />
                <button
                  data-testid="wellness-assistant-button"
                  onClick={openCompanion}
                  className="text-[#C4A67A] hover:text-[#E8B872] transition-colors"
                  title="Wellness Assistant"
                  aria-label="Open Wellness Assistant"
                >
                  <Sparkles size={16} />
                </button>
                <div className="relative" ref={accountMenuRef}>
                  <button
                    data-testid="account-button"
                    onClick={() => setAccountMenuOpen((o) => !o)}
                    aria-haspopup="menu"
                    aria-expanded={accountMenuOpen}
                    className="text-[#8A9A92] hover:text-[#72C2AC] transition-colors flex items-center"
                    title="Account menu"
                  >
                    <UserCircle size={18} />
                  </button>
                  {accountMenuOpen && (
                    <div
                      data-testid="account-menu"
                      role="menu"
                      className="absolute right-0 mt-2 w-56 z-50 bg-[#0A1612] border border-[#5C9E8C]/40 rounded-xl shadow-[0_18px_40px_-8px_rgba(0,0,0,0.85)] py-1.5 overflow-hidden"
                    >
                      <button
                        data-testid="account-menu-account"
                        onClick={() => { setAccountMenuOpen(false); onOpenAccount && onOpenAccount(); }}
                        className="w-full flex items-center gap-3 px-3 py-2 text-sm text-[#E8E3D9] hover:bg-[#5C9E8C]/15 transition-colors"
                      >
                        <UserCircle size={14} className="text-[#72C2AC]" />
                        Account
                      </button>
                      <button
                        data-testid="account-menu-haptics"
                        onClick={() => { setAccountMenuOpen(false); setHapticsOpen(true); }}
                        className="w-full flex items-center gap-3 px-3 py-2 text-sm text-[#E8E3D9] hover:bg-[#5C9E8C]/15 transition-colors"
                      >
                        <HeartPulse size={14} className="text-[#72C2AC]" />
                        Pulsing Haptics
                      </button>
                      <button
                        data-testid="account-menu-voice"
                        onClick={() => { setAccountMenuOpen(false); setVoiceOpen(true); }}
                        className="w-full flex items-center gap-3 px-3 py-2 text-sm text-[#E8E3D9] hover:bg-[#5C9E8C]/15 transition-colors"
                      >
                        <Mic size={14} className="text-[#72C2AC]" />
                        Voice Shortcuts
                      </button>
                      {showInstall && (
                        <button
                          data-testid="account-menu-install"
                          onClick={() => { setAccountMenuOpen(false); setInstallOpen(true); }}
                          className="w-full flex items-center gap-3 px-3 py-2 text-sm text-[#E8E3D9] hover:bg-[#5C9E8C]/15 transition-colors"
                        >
                          <Smartphone size={14} className="text-[#72C2AC]" />
                          Install Solarisound
                        </button>
                      )}
                      <button
                        data-testid="account-menu-notifications"
                        onClick={() => { setAccountMenuOpen(false); setNotifPrefsOpen(true); }}
                        className="w-full flex items-center gap-3 px-3 py-2 text-sm text-[#E8E3D9] hover:bg-[#5C9E8C]/15 transition-colors"
                      >
                        <Bell size={14} className="text-[#72C2AC]" />
                        Notifications
                      </button>
                      <button
                        data-testid="account-menu-calibration"
                        onClick={() => { setAccountMenuOpen(false); setCalibrationOpen(true); }}
                        className="w-full flex items-center gap-3 px-3 py-2 text-sm text-[#E8E3D9] hover:bg-[#5C9E8C]/15 transition-colors"
                      >
                        <Ear size={14} className="text-[#72C2AC]" />
                        Equalizer Calibration
                      </button>
                    </div>
                  )}
                </div>
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

          {/* Harmonic Blueprint — Pro-only voice signature capture. Sits at
              the top of the left column so the personal "know thyself"
              moment introduces every other preset below it. */}
          <HarmonicBlueprintCard
            isPro={isPro}
            hasProfile={hbHasProfile}
            onOpen={() => setHbOpen(true)}
            onUnlock={onOpenAccount}
          />

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
                  {sleepMode
                    ? `Drifting · smooth fade · ${sleepDurationMin >= 60 ? `${(sleepDurationMin / 60).toFixed(sleepDurationMin % 60 === 0 ? 0 : 1)}h` : `${sleepDurationMin}min`}`
                    : `4 Hz · brown noise · ${sleepDurationMin >= 60 ? `${(sleepDurationMin / 60).toFixed(sleepDurationMin % 60 === 0 ? 0 : 1)}h` : `${sleepDurationMin}min`} · 5min fade`}
                </div>
              </div>
              {!isPro && <Lock size={12} className="text-[#C4A67A]" />}
            </button>

            {/* Sleep Mode duration chooser — replaces hard-coded 30 min.
                Disabled while Sleep Mode is active so the timer doesn't get
                yanked mid-session. Click-toggle pattern keeps the UI dense
                on mobile (no extra dropdown chrome). */}
            <div className="mt-2 grid grid-cols-5 gap-1.5" data-testid="sleep-duration-chooser">
              {SLEEP_DURATIONS.map((m) => {
                const active = sleepDurationMin === m;
                const label = m >= 60 ? `${m / 60}h` : `${m}m`;
                return (
                  <button
                    key={m}
                    data-testid={`sleep-duration-${m}`}
                    disabled={sleepMode}
                    onClick={() => setSleepDurationMin(m)}
                    className={`py-1.5 rounded-full text-[10px] font-mono tracking-wider border transition-colors ${
                      active
                        ? 'border-[#72C2AC]/60 bg-[#5C9E8C]/20 text-[#72C2AC]'
                        : 'border-[#5C9E8C]/15 text-[#8A9A92] hover:text-[#E8E3D9]'
                    } ${sleepMode ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
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

          {/* Sound Bath — algorithmic crystal-bowl / chime / gong washes.
              Pro-only alongside Specials & Soundscapes. Every bath tap
              force-arms the session timer to 10 min (baths are designed
              for a fixed contemplative window regardless of the user's
              last session-length preference). The Duration slider itself
              is intentionally NOT touched so the user's saved pref for
              non-bath sessions is preserved. Tapping an active preset —
              or the header Stop button — silences EVERYTHING: the bath
              scheduler, the base audio-engine oscillator, and the timer. */}
          <SoundBathPanel
            isPro={isPro}
            onUnlock={onOpenAccount}
            onSaveBath={saveBathArrangement}
            onBathStart={() => { setRemaining(BATH_DEFAULT_MIN * 60); }}
            onBathStop={() => {
              setRemaining(0);
              // Full silence — kill the audioEngine base oscillator too so
              // there's no residual "hum" after the bath fades out.
              try { audioEngine.stop(); } catch (e) { /* graceful */ }
            }}
          />

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
                  <button
                    data-testid={`load-session-${s.id}`}
                    onClick={() => loadSession(s)}
                    className="text-left flex-1 min-w-0 flex items-center gap-2"
                  >
                    {!isPro && (
                      <Lock
                        size={12}
                        className="text-[#C4A67A] shrink-0"
                        data-testid={`session-lock-${s.id}`}
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-[#E8E3D9] truncate">{s.name}</div>
                      <div className="text-[11px] font-mono text-[#72C2AC]">{s.frequency}Hz · {s.duration_minutes}m</div>
                    </div>
                  </button>
                  {/* Delete is hidden for non-Pro users so trial-expired
                      accounts can't accidentally lose their saved data
                      before deciding whether to upgrade. Sessions remain
                      recoverable the moment they subscribe. */}
                  {isPro && (
                    <button
                      data-testid={`delete-session-${s.id}`}
                      onClick={() => deleteSession(s.id)}
                      className="text-[#8A9A92] hover:text-[#D96C6C] transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        </aside>

        {/* CENTER — Visualizer + transport */}
        <main className="flex-1 relative rounded-3xl overflow-hidden border border-[rgba(92,158,140,0.15)] bg-black/30 min-h-[560px] lg:min-h-0">
          {/* Bump `min-h` on mobile from 480 → 560 so the visualizer has
              real breathing room between the "Now Tuning" frequency label
              (top) and the bottom transport (timer + play + chips). At
              480 px the vertical middle band was ~110 px — barely enough
              for the Breathwork orb (112 px) and forced a permanent
              collision with either the frequency name above OR the timer
              below on portrait mobile. Above `lg` the sidebars appear so
              min-h reverts to 0 (flex-1 handles the growth). */}
          {/* Ambient Pulse — sits BEHIND the visualizer so the glow radiates
              outward from the center of the frequency ring. Absolutely
              positioned, pointer-events:none, aria-hidden, z-[1]. */}
          <AmbientPulse
            enabled={ambientPulse && isPro}
            playing={state.playing}
            frequencyHz={flowStage?.meta?.hz || state.frequency}
            breathwork={breathwork}
            sleepMode={activeSoundscape === 'sleep'}
            sleepProgress={duration > 0 ? Math.max(0, Math.min(1, 1 - (remaining / (duration * 60)))) : 0}
          />
          <Visualizer playing={state.playing} frequency={state.frequency} mode={visualMode} />
          <Breathwork active={breathwork && state.playing} />
          <WellnessCheckinCard
            open={checkinOpen}
            onContinue={continueCheckin}
            onDone={dismissCheckin}
            journeyEntryId={lastJourneyEntryId}
            onShare={shareSession ? () => setShareOpen(true) : undefined}
          />
          <SessionShareCard
            open={shareOpen}
            onClose={() => setShareOpen(false)}
            isPro={isPro}
            onUpgrade={onOpenAccount}
            session={shareSession}
          />
          {/* Ambient Pulse — free-tier upgrade prompt. Appears when a free
              user taps the toggle; auto-dismisses after 3.5s. Includes the
              accessibility note about slow gentle transitions being safe. */}
          {ambientPulseGate === 'pro-only' && (
            <div
              data-testid="ambient-pulse-gate"
              className="absolute left-1/2 -translate-x-1/2 top-24 z-30 max-w-xs pointer-events-auto"
            >
              <div className="rounded-xl border border-[#C4A67A]/50 bg-[#0B1814]/95 backdrop-blur-sm p-4 shadow-[0_10px_30px_rgba(0,0,0,0.55)]">
                <div className="text-[11px] uppercase tracking-[0.2em] text-[#C4A67A] font-mono mb-1">
                  Pro feature
                </div>
                <div className="text-[14px] text-[#E8E3D9] leading-relaxed mb-2">
                  Ambient Pulse is a Pro feature. Upgrade to experience fully immersive sessions.
                </div>
                <div className="text-[11px] text-[#8A9A92] leading-relaxed mb-3">
                  Ambient Pulse uses very slow, gentle light transitions designed to be safe for all users. If you're sensitive to light changes, keep it off.
                </div>
                <button
                  data-testid="ambient-pulse-upgrade"
                  onClick={() => { setAmbientPulseGate(''); onOpenAccount && onOpenAccount(); }}
                  className="w-full py-2 rounded-full bg-[#C4A67A]/20 hover:bg-[#C4A67A]/35 border border-[#C4A67A]/50 hover:border-[#C4A67A] text-[#C4A67A] text-[12px] tracking-wide transition-colors"
                >
                  See Pro plans →
                </button>
              </div>
            </div>
          )}
          <SessionUnlockCard
            open={sessionUnlockOpen}
            onUpgrade={() => { setSessionUnlockOpen(false); onOpenAccount(); }}
            onDismiss={() => setSessionUnlockOpen(false)}
          />

          {/* Visual-mode chips — Rings / Chladni / Ripples.
              Desktop (sm+): top-right horizontal row of the visualizer.
              Mobile (<sm): stacked VERTICALLY in the top-right corner so
              they clear the centered "Now Tuning" header AND don't sit on
              top of the breathing-orb / ripple visual feature, and they
              don't collide with the bottom-centered "Keep screen on"
              transport. */}
          <div
            className="absolute z-10 flex gap-1.5 flex-col top-4 right-3 sm:flex-row sm:top-4 sm:right-4"
            data-testid="visual-mode-chips"
          >
            {[
              { key: 'rings', label: 'Rings' },
              { key: 'chladni', label: 'Chladni' },
              { key: 'ripples', label: 'Ripples' },
            ].map((m) => (
              <button
                key={m.key}
                data-testid={`visual-mode-${m.key}`}
                onClick={() => setVisualMode(m.key)}
                className={`px-2.5 py-1 rounded-full text-[10px] tracking-[0.18em] uppercase font-mono border transition-colors ${
                  visualMode === m.key
                    ? 'border-[#C4A67A]/60 bg-[#C4A67A]/15 text-[#C4A67A]'
                    : 'border-[#5C9E8C]/25 bg-black/30 text-[#8A9A92] hover:text-[#E8E3D9]'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>

          {/* Frequency label (top) — while a Flow journey is running the
              central label shows the CURRENT stage's frequency + name plus
              a 3-dot progress row. Smooth transitions between stages come
              for free because the label re-renders when flowStage changes. */}
          <div className="absolute top-6 left-1/2 -translate-x-1/2 text-center z-10">
            <div className="label-tiny">
              {flowStage ? `Flow · Stage ${flowStage.stageIdx + 1} of 3` : 'Now Tuning'}
            </div>
            <div
              data-testid="current-frequency"
              className="font-mono text-4xl text-[#72C2AC] tracking-widest mt-1 transition-opacity duration-700"
            >
              {(flowStage ? flowStage.meta.hz : state.frequency).toFixed(1)}<span className="text-base text-[#8A9A92] ml-1">Hz</span>
            </div>
            {(flowStage || activePreset) && (
              <div className="font-display text-xl text-[#E8E3D9] mt-1 transition-opacity duration-700">
                {flowStage ? flowStage.meta.name : activePreset.name}
              </div>
            )}
            {flowStage && flowStage.meta.sub && (
              <div className="text-[11px] text-[#8A9A92] mt-0.5">{flowStage.meta.sub}</div>
            )}
            {flowStage && (
              <div className="flex items-center gap-1.5 mt-3 justify-center" data-testid="visualizer-flow-progress">
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className={`h-1 w-8 rounded-full transition-colors duration-500 ${
                      i < flowStage.stageIdx
                        ? 'bg-[#72C2AC]'
                        : i === flowStage.stageIdx
                        ? 'bg-[#72C2AC]/60'
                        : 'bg-[#5C9E8C]/20'
                    }`}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Transport (bottom) */}
          <div className="absolute bottom-6 left-1/2 -translate-x-1/2 flex flex-col items-center gap-4 z-10">
            <div className="flex items-center gap-2 flex-wrap justify-center max-w-[calc(100vw-2rem)]">
              <div data-testid="timer-display" className="font-mono text-2xl text-[#E8E3D9] tracking-widest">
                {formatTime(state.playing ? remaining : duration * 60)}
              </div>
              {state.playing && state.sessionFadeActive && (
                <div
                  data-testid="smart-fade-pill"
                  className="text-[9px] tracking-[0.18em] uppercase font-mono px-2 py-0.5 rounded-full border border-[#C4A67A]/40 bg-[#C4A67A]/10 text-[#C4A67A] whitespace-nowrap"
                  title="Smoothly fading to silence over the last 5 minutes"
                  style={{ animation: 'landing-cta-breath-anim 3.5s ease-in-out infinite' }}
                >
                  Fading
                </div>
              )}
            </div>
            <button
              data-testid="play-pause-button"
              onClick={togglePlay}
              className="w-20 h-20 rounded-full border border-[#72C2AC]/50 bg-[#5C9E8C]/15 hover:bg-[#5C9E8C]/35 backdrop-blur-md flex items-center justify-center transition-all duration-300 hover:scale-105 active:scale-95"
              style={{ boxShadow: '0 0 40px rgba(114,194,172,0.25)' }}
            >
              {state.playing ? <Pause size={28} className="text-[#E8E3D9]" /> : <Play size={28} className="text-[#E8E3D9] ml-1" />}
            </button>
            {/* Keep-screen-awake toggle — opt-in. Hidden on browsers without the
                Wake Lock API (older Safari < 16.4). On supported browsers this
                prevents the screen from auto-locking during a session. Background
                audio continues regardless via the MediaStream sink + Media Session
                bound in audioEngine.start(). */}
            {keepAwakeSupported && (
              <button
                data-testid="keep-awake-toggle"
                onClick={toggleKeepAwake}
                aria-pressed={keepAwake}
                title={keepAwake ? 'Screen will stay awake during playback' : 'Allow screen to lock during playback'}
                className={`group inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-[10px] tracking-[0.18em] uppercase transition-colors ${
                  keepAwake
                    ? 'border-[#C4A67A]/60 bg-[#C4A67A]/15 text-[#C4A67A]'
                    : 'border-[#5C9E8C]/30 bg-black/20 text-[#8A9A92] hover:text-[#E8E3D9]'
                }`}
              >
                <Sun size={12} className={keepAwake ? 'text-[#C4A67A]' : 'text-[#8A9A92]'} />
                <span>{keepAwake ? 'Screen on' : 'Keep screen on'}</span>
              </button>
            )}
            {/* Ambient Pulse — subtle radial breathing glow. Free users see
                the toggle for discovery but tapping it surfaces an upgrade
                nudge instead of enabling. Preference persists per-user via
                localStorage. */}
            <button
              data-testid="ambient-pulse-toggle"
              onClick={toggleAmbientPulse}
              aria-pressed={ambientPulse && isPro}
              title={!isPro ? 'Ambient Pulse — Pro feature' : (ambientPulse ? 'The screen breathes with your session — tap to turn off' : 'Turn on Ambient Pulse — the room breathes with you')}
              className={`group inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-[10px] tracking-[0.18em] uppercase transition-colors ${
                (ambientPulse && isPro)
                  ? 'border-[#72C2AC]/60 bg-[#72C2AC]/15 text-[#72C2AC]'
                  : 'border-[#5C9E8C]/30 bg-black/20 text-[#8A9A92] hover:text-[#E8E3D9]'
              } ${!isPro ? 'opacity-70' : ''}`}
            >
              <CircleDot size={12} className={(ambientPulse && isPro) ? 'text-[#72C2AC]' : 'text-[#8A9A92]'} />
              <span>Ambient Pulse{!isPro ? ' · Pro' : ''}</span>
            </button>
            {/* For-best-results listening guide — subtle, non-clinical set of
                recommendations (wired headphones, moderate volume, quiet
                environment). Opens a soft glass card, never blocks playback. */}
            <button
              data-testid="listening-guide-open"
              onClick={() => setListeningGuideOpen(true)}
              title="For best results"
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-[#5C9E8C]/30 bg-black/20 text-[10px] tracking-[0.18em] uppercase text-[#8A9A92] hover:text-[#E8E3D9] hover:border-[#72C2AC]/60 transition-colors"
            >
              <Info size={12} />
              <span>For best results</span>
            </button>
          </div>
        </main>

        {/* RIGHT — Custom + Ambient + Save */}
        <aside className="w-full lg:w-[360px] flex flex-col gap-4 lg:gap-6 lg:h-full lg:overflow-y-auto custom-scrollbar">
          {/* Wellness Prescription (Pro). Click the header to expand; free-text
              intent + optional mood/goal → backend Claude Sonnet 4.5 →
              prescription applied to the engine in one shot. */}
          <div className="glass p-5" data-testid="ai-recommend-panel">
            <button
              data-testid="ai-recommend-toggle"
              onClick={() => setAiOpen((v) => !v)}
              className="w-full flex items-center justify-between gap-2 text-left"
            >
              <div className="flex items-center gap-2">
                <Sparkles size={14} className="text-[#C4A67A]" />
                <div className="label-tiny">Wellness Prescription</div>
                {!isPro && <Lock size={11} className="text-[#C4A67A]" />}
              </div>
              <div className="text-[10px] text-[#8A9A92] font-mono tracking-wider">
                {aiOpen ? '−' : '+'}
              </div>
            </button>

            {aiOpen && (
              <div className="mt-4 space-y-3">
                <div>
                  <label className="text-[10px] text-[#8A9A92] tracking-widest uppercase">How do you want to feel?</label>
                  <textarea
                    data-testid="ai-intent-input"
                    value={aiIntent}
                    onChange={(e) => setAiIntent(e.target.value)}
                    placeholder="e.g. I need to focus deeply for the next hour, but I'm jittery from coffee."
                    rows={3}
                    disabled={!isPro || aiLoading}
                    className="mt-1 w-full bg-black/30 border border-[#5C9E8C]/20 rounded-lg px-3 py-2 text-sm text-[#E8E3D9] placeholder-[#5A6B65] focus:outline-none focus:border-[#72C2AC]/50 resize-none"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    data-testid="ai-mood-input"
                    type="text"
                    value={aiMood}
                    onChange={(e) => setAiMood(e.target.value)}
                    placeholder="Mood (optional)"
                    disabled={!isPro || aiLoading}
                    className="bg-black/30 border border-[#5C9E8C]/20 rounded-lg px-3 py-2 text-xs text-[#E8E3D9] placeholder-[#5A6B65] focus:outline-none focus:border-[#72C2AC]/50"
                  />
                  <input
                    data-testid="ai-goal-input"
                    type="text"
                    value={aiGoal}
                    onChange={(e) => setAiGoal(e.target.value)}
                    placeholder="Goal (optional)"
                    disabled={!isPro || aiLoading}
                    className="bg-black/30 border border-[#5C9E8C]/20 rounded-lg px-3 py-2 text-xs text-[#E8E3D9] placeholder-[#5A6B65] focus:outline-none focus:border-[#72C2AC]/50"
                  />
                </div>
                <button
                  data-testid="ai-generate-button"
                  onClick={runAiRecommend}
                  disabled={!isPro || aiLoading || aiIntent.trim().length < 2}
                  className={`w-full py-2.5 rounded-full border text-xs tracking-[0.18em] uppercase font-mono transition-colors flex items-center justify-center gap-2 ${
                    isPro && !aiLoading && aiIntent.trim().length >= 2
                      ? 'border-[#C4A67A]/60 bg-[#C4A67A]/15 text-[#C4A67A] hover:bg-[#C4A67A]/25'
                      : 'border-[#5C9E8C]/15 text-[#5A6B65] cursor-not-allowed'
                  }`}
                >
                  <Sparkles size={12} />
                  {aiLoading ? 'Tuning…' : 'Prescribe my frequency'}
                </button>

                {aiErr && (
                  <div data-testid="ai-error" className="text-[11px] text-[#E07A5F]">{aiErr}</div>
                )}
                {aiResult && !aiErr && (
                  <div data-testid="ai-result" className="mt-1 p-3 rounded-lg bg-[#0E1F18]/60 border border-[#5C9E8C]/20">
                    <div className="text-[#C4A67A] font-mono text-xs tracking-wider">{aiResult.name}</div>
                    <div className="text-[#E8E3D9] text-[11px] mt-1 leading-snug">{aiResult.description}</div>
                    <div className="text-[10px] text-[#8A9A92] mt-2 font-mono">
                      {aiResult.frequency.toFixed(1)} Hz · {aiResult.waveform}
                      {aiResult.binaural > 0 ? ` · binaural ${aiResult.binaural}Hz` : ''}
                      {aiResult.isochronic > 0 ? ` · iso ${aiResult.isochronic}Hz` : ''}
                      {aiResult.golden_stack ? ' · φ' : ''}
                    </div>
                    {aiResult.harmonic_note && (
                      <div
                        data-testid="ai-result-harmonic-note"
                        className="mt-2 text-[11px] leading-snug italic text-[#98C1B0]"
                      >
                        {aiResult.harmonic_note}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

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

            {/* Isochronic pulse — Pro only. Mutually exclusive with binaural
                (engine accepts both, but they fight perceptually; we soft-disable
                one when the other is non-zero so the UI mirrors what's actually
                audible). 0 = off; 1-40 Hz pulse the carrier on/off (true
                isochronic entrainment, no headphones required). */}
            <label className="text-xs text-[#8A9A92] flex justify-between mt-5 mb-1">
              <span className="flex items-center gap-1">
                {!isPro && <Lock size={11} className="text-[#C4A67A]" />}
                Isochronic pulse
              </span>
              <span className="font-mono text-[#72C2AC]">
                {state.isochronic > 0 ? `${state.isochronic} Hz` : 'Off'}
              </span>
            </label>
            <input
              data-testid="isochronic-slider"
              type="range" min="0" max="40" step="0.5"
              value={state.isochronic || 0}
              disabled={!isPro}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                // Soft mutual exclusion with binaural — if user pulls iso up,
                // drop binaural to 0 so the carrier isn't being modulated by
                // two competing schemes at once.
                if (v > 0 && state.binaural > 0) audioEngine.setBinaural(0);
                audioEngine.setIsochronic(v);
              }}
              className={`slider ${!isPro ? 'opacity-50 cursor-not-allowed' : ''}`}
              style={{ '--v': `${((state.isochronic || 0) / 40) * 100}%` }}
            />
            <div className="text-[10px] text-[#8A9A92] mt-1 leading-snug">
              Square-wave gates the tone on/off at the chosen Hz for true
              brainwave entrainment — no headphones required.
            </div>

            <label className="text-xs text-[#8A9A92] flex justify-between mt-5 mb-1">
              <span><Volume2 size={12} className="inline mr-1" />Tone volume</span>
              <span className="font-mono text-[#72C2AC]" data-testid="tone-volume-readout">{Math.round(state.toneVolume * 100)}%</span>
            </label>
            <input
              data-testid="tone-volume-slider"
              type="range" min="0" max="1" step="0.01"
              value={state.toneVolume}
              onChange={(e) => {
                // A manual slider move counts as the user asserting their own
                // preferred level for the rest of this session. From this
                // point on, no auto-apply path (soundscape / assistant /
                // AI prescription / etc) may override the user's chosen
                // level until they either stop the session or tap
                // "recommended" to opt back into the per-freq default.
                userAdjustedVolumeRef.current = true;
                audioEngine.setToneVolume(parseFloat(e.target.value));
              }}
              className="slider"
              style={{ '--v': `${state.toneVolume * 100}%` }}
            />
            {volumeDivergesFromIdeal(state.toneVolume, state.frequency) && (
              <button
                type="button"
                data-testid="reset-recommended-volume-btn"
                onClick={() => {
                  try { applyIdealVolume(audioEngine, state.frequency); } catch (_) { /* graceful */ }
                  userAdjustedVolumeRef.current = false;
                }}
                className="mt-1 text-[10px] text-[#72C2AC] hover:text-[#8FD9C1] transition-colors inline-flex items-center gap-1"
                title={`Return to ${Math.round(getIdealVolume(state.frequency) * 100)}% — the ideal level tuned for ${state.frequency} Hz`}
              >
                <CircleDot size={10} />
                Reset to recommended ({Math.round(getIdealVolume(state.frequency) * 100)}%)
              </button>
            )}
          </div>

          {/* Flow Mode — 3-stage guided frequency journeys. Sits in the right
              column between Custom Generator and Ambient Layers so it lives
              in the "shape the sound" tools area. Free users get the 3
              pre-built journeys; Pro unlocks the Custom Flow builder for
              picking any 3 Solfeggio frequencies. */}
          <FlowModePanel
            isPro={isPro}
            solfeggioList={SOLFEGGIO.map((p) => ({ hz: p.hz, label: p.name, sub: p.desc }))}
            onFlowStart={(mins, journey) => { setDuration(mins); setRemaining(mins * 60); setActiveFlowJourney(journey || null); }}
            onFlowStop={() => {
              setRemaining(0);
              try { audioEngine.stop(); } catch (e) { /* graceful */ }
              setFlowStage(null);
              setActiveFlowJourney(null);
            }}
            onStageChange={(stageIdx, meta) => {
              setFlowStage(meta ? { stageIdx, meta } : null);
            }}
            onUnlock={onOpenAccount}
          />

          {/* Meditation Sounds — Pro-only. Three tabs (Presets · Chakras ·
              Breath). Sits between Flow Mode and Ambient Layers in the
              right column so contemplative tools cluster together. Timer
              arms to a fixed 10-min contemplative window per tap; the user
              can stop from the player transport or by re-tapping the tile. */}
          <MeditationSoundsPanel
            isPro={isPro}
            onUnlock={onOpenAccount}
            onSessionStart={() => { setRemaining(BATH_DEFAULT_MIN * 60); }}
            onSessionStop={() => {
              setRemaining(0);
              try { audioEngine.stop(); } catch (e) { /* graceful */ }
            }}
          />

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

      {/* Conversational check-in agent. Controlled by Dashboard so both
          the once-per-session auto-open AND the manual "Wellness Assistant"
          header button can use the same sheet with different greetings. */}
      <AIAgentSheet
        open={agentOpen}
        greeting={agentGreeting}
        initialSuggestion={agentInitialSuggestion}
        isPro={isPro}
        onClose={() => setAgentOpen(false)}
        onOpenAccount={onOpenAccount}
        onTriggerAIPrescription={triggerAIPrescription}
      />

      {/* PWA install affordance — native prompt on Android/Chromium, share-
          sheet instructions on iOS Safari, generic browser-bar hint on
          desktop fallback. Hidden entirely when already installed. */}
      <InstallAppModal
        open={installOpen}
        onClose={() => setInstallOpen(false)}
        canPrompt={canPrompt}
        isIOS={isInstallIOS}
        promptInstall={promptInstall}
      />

      {/* Pulsing Haptics — optional vibration sync. Engine auto-attaches to
          audio playback on mount; this modal is just the toggle / pattern UI. */}
      <HapticsModal open={hapticsOpen} onClose={() => setHapticsOpen(false)} />

      {/* Harmonic Blueprint — full-screen voice-signature capture + FFT map. */}
      <HarmonicBlueprintSheet
        open={hbOpen}
        onClose={() => setHbOpen(false)}
        isPro={isPro}
        onOpenAccount={onOpenAccount}
        initialData={hbInitialData}
      />

      {/* Phase 12c — Session Impact Rating prompt. Fires on app open when
          the user has HB-recommended sessions from ≥24h ago without a
          rating. Self-fetches, self-dismisses, silent when nothing pending. */}
      <SessionImpactPrompt isPro={isPro} />

      {/* Phase 12e — Milestone celebrations. `showInlineCard={false}` so we
          only mount the overlay here; the inline "My Milestones" card lives
          exclusively in the Harmonic Blueprint profile section. */}
      {isPro && <MyMilestones showInlineCard={false} />}

      {/* Voice Shortcuts — Siri / Google Assistant setup instructions plus
          copyable deep-link URLs (/play?preset=…) for hands-free playback. */}
      <VoiceShortcutsModal open={voiceOpen} onClose={() => setVoiceOpen(false)} />

      {/* Equalizer Calibration — 30s onboarding hearing test that builds a
          per-user EQ profile (peaking biquads applied between master gain
          and ctx.destination). Auto-opens once on first login; manually
          accessible from the header Ear icon at any time. */}
      <CalibrationModal
        open={calibrationOpen}
        onClose={closeCalibration}
        onComplete={(res) => {
          // Successful real calibration → switch the copy on next visit.
          if (res && !res.skipped) setHasProfile(true);
        }}
      />

      {/* Onboarding Transition Card — slides up 30 s after a suggestion is
          tapped (per the onboarding strategy). Composes the Step 2 guidance
          line + Step 3 calibration pivot. Copy adapts when the user is
          already calibrated so it reads as a fine-tune nudge. */}
      <OnboardingTransitionCard
        open={otcOpen}
        headphonesDetected={otcHeadphones}
        alreadyCalibrated={hasProfile}
        onStart={startCalibrationFromTransition}
        onSkip={dismissTransitionCard}
      />

      {/* Notification preferences — accessed via the bell panel footer OR the
          Account dropdown. Owns push subscription lifecycle + per-category
          toggles + quiet hours + daily-cap. */}
      <NotificationPreferencesModal
        open={notifPrefsOpen}
        onClose={() => setNotifPrefsOpen(false)}
      />

      {/* Warm-moment push opt-in slip — silently invited by
          `requestPushOptInIfWarm()` after Smart-Fade completion OR after a
          Wellness Assistant suggestion tap. Never shown mid-session; snoozes
          for 7 days on "Later" or 60 days on "No thanks". */}
      <PushOptInSlip />

      {/* Fidelity: "For best results" listening guide — wired headphones,
          moderate volume, quiet environment. Accessed from the player card. */}
      <ListeningGuide open={listeningGuideOpen} onClose={() => setListeningGuideOpen(false)} />

      {/* Fidelity: gentle headphone reminder when binaural / isochronic /
          Golden Stack is active. Auto-hides after 12s, snoozes 24h on dismiss. */}
      <HeadphoneReminder
        visible={headphoneReminder.visible}
        reason={headphoneReminder.reason}
        onDismiss={headphoneReminder.dismiss}
      />
    </div>
  );
}
