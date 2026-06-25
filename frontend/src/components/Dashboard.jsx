import React, { useEffect, useMemo, useState } from 'react';
import { Play, Pause, Save, Trash2, LogOut, Wind, Droplet, Waves, Trees, Volume2, Sparkles } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import api, { formatApiError } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
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

const WAVEFORMS = ['sine', 'triangle', 'square', 'sawtooth'];
const PHI = 1.6180339887;
const GOLDEN_BASE = 144; // Fibonacci number; 144 × φ ≈ 233 (Fib); × φ² ≈ 377 (Fib). Creates a pure golden chord.

const AMBIENT = [
  { key: 'rain', label: 'Rain', Icon: Droplet },
  { key: 'ocean', label: 'Ocean', Icon: Waves },
  { key: 'forest', label: 'Forest', Icon: Trees },
];

function formatTime(secs) {
  const m = Math.floor(secs / 60).toString().padStart(2, '0');
  const s = Math.floor(secs % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

export default function Dashboard() {
  const { user, logout } = useAuth();
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

  const checkIn = async (minutes) => {
    try {
      await api.post('/streak/checkin', { minutes });
      setStreakBump((n) => n + 1);
    } catch (e) { /* ignore */ }
  };

  useEffect(() => audioEngine.on(setState), []);

  // Timer
  useEffect(() => {
    if (!state.playing || remaining <= 0) return;
    const id = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) { audioEngine.stop(); return 0; }
        return r - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [state.playing, remaining]);

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
    try { const { data } = await api.get('/sessions'); setSessions(data); } catch (e) { /* ignore */ }
  };

  const togglePlay = () => {
    if (!state.playing) {
      setRemaining(duration * 60);
      audioEngine.start();
    } else {
      audioEngine.stop();
      setRemaining(0);
    }
  };

  const selectFrequency = (hz, opts = {}) => {
    const wantGolden = !!opts.golden;
    const isSame =
      Math.round(state.frequency) === hz && state.goldenStack === wantGolden;
    if (state.playing && isSame) {
      audioEngine.stop();
      setRemaining(0);
      return;
    }
    audioEngine.setFrequency(hz);
    audioEngine.setGoldenStack(wantGolden);
    if (!state.playing) {
      setRemaining(duration * 60);
      audioEngine.start();
    }
  };

  const toggleGoldenStack = () => audioEngine.setGoldenStack(!state.goldenStack);

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
    } catch (e) { setErr(formatApiError(e)); }
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
    try { await api.delete(`/sessions/${id}`); refreshSessions(); } catch (e) { /* ignore */ }
  };

  const activePreset = useMemo(
    () => SOLFEGGIO.find((p) => p.hz === Math.round(state.frequency)),
    [state.frequency]
  );

  return (
    <div className="relative min-h-screen w-full overflow-hidden">
      <div className="aurora-bg" />
      <div className="grain" />

      <div className="relative z-10 h-screen w-screen flex flex-col lg:flex-row p-4 lg:p-6 gap-4 lg:gap-6">

        {/* LEFT — Solfeggio + Saved */}
        <aside className="w-full lg:w-80 flex flex-col gap-4 lg:gap-6 lg:h-full lg:overflow-y-auto custom-scrollbar">
          <div className="glass p-6">
            <div className="flex items-center justify-between mb-1">
              <div className="label-tiny">Healing Frequencies</div>
              <button
                data-testid="logout-button"
                onClick={logout}
                className="text-[#8A9A92] hover:text-[#72C2AC] transition-colors"
                title="Sign out"
              >
                <LogOut size={16} />
              </button>
            </div>
            <h2 className="font-display text-2xl text-[#E8E3D9] font-light">Hello, {user.name}</h2>
            <p className="text-xs text-[#8A9A92] mt-1">Choose a tone or craft your own.</p>
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
        <main className="flex-1 relative rounded-3xl overflow-hidden border border-[rgba(92,158,140,0.15)] bg-black/30 min-h-[420px]">
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
              type="range" min="20" max="1200" step="0.5"
              value={state.frequency}
              onChange={(e) => audioEngine.setFrequency(parseFloat(e.target.value))}
              className="slider"
              style={{ '--v': `${((state.frequency - 20) / 1180) * 100}%` }}
            />

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

          {/* Ambient Mixer */}
          <div className="glass p-6">
            <div className="label-tiny mb-4">Ambient Layers</div>
            <div className="space-y-4">
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
                      onChange={(e) => audioEngine.setAmbient(key, parseFloat(e.target.value))}
                      className="slider amber"
                      style={{ '--v': `${v * 100}%` }}
                    />
                  </div>
                );
              })}
            </div>
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
              onClick={() => setBreathwork((b) => !b)}
              className={`mt-5 w-full py-2.5 rounded-full border transition-colors duration-300 flex items-center justify-center gap-2 ${
                breathwork
                  ? 'bg-[#5C9E8C]/25 border-[#72C2AC]/50 text-[#72C2AC]'
                  : 'border-[#5C9E8C]/20 text-[#8A9A92] hover:text-[#E8E3D9]'
              }`}
            >
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
            {err && <div className="text-[#D96C6C] text-xs mt-2">{err}</div>}
          </div>
        </aside>
      </div>
    </div>
  );
}
