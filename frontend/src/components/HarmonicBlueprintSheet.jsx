import React, { useEffect, useRef, useState } from 'react';
import { X, Mic, Upload, RotateCcw, Waves, Check, AlertTriangle, Sparkles } from 'lucide-react';
import api, { formatApiError } from '@/lib/api';
import {
  validateBuffer, analyseBuffer,
  decodeBlobToBuffer, decodeFileToBuffer,
} from '@/lib/harmonicBlueprintEngine';

/**
 * Full-screen Harmonic Blueprint experience — voice capture, FFT analysis,
 * and visual resonance map. Rendered as an overlay on top of the Dashboard.
 *
 * Steps: onboarding → permission → capture → analysing → results (or error).
 *
 * The raw audio blob is NEVER uploaded. `handleAnalyse` runs FFT locally and
 * only the derived profile JSON is POSTed to /api/harmonic-blueprint/profile.
 */
const MIN_SECONDS = 10;
const MAX_SECONDS = 30;

export default function HarmonicBlueprintSheet({ open, onClose }) {
  const [existing, setExisting] = useState(null);   // saved profile or null
  const [loading, setLoading] = useState(true);
  const [step, setStep] = useState('intro');        // intro | capture | analysing | results | error
  const [error, setError] = useState('');
  const [profile, setProfile] = useState(null);

  // Recording state
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);            // 0..1 live meter
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const ctxRef = useRef(null);
  const analyserRef = useRef(null);
  const rafRef = useRef(null);
  const startedAtRef = useRef(0);

  // Load any existing profile when the sheet opens.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setError('');
    (async () => {
      try {
        const { data } = await api.get('/harmonic-blueprint/profile');
        if (!alive) return;
        setExisting(data.profile || null);
        setProfile(data.profile || null);
        setStep(data.profile ? 'results' : 'intro');
      } catch (e) {
        if (!alive) return;
        setError(formatApiError(e));
        setStep('error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [open]);

  useEffect(() => () => stopStream(), []);
  useEffect(() => { if (!open) stopStream(); }, [open]);

  function stopStream() {
    try { rafRef.current && cancelAnimationFrame(rafRef.current); } catch (_) {}
    try { recorderRef.current && recorderRef.current.state === 'recording' && recorderRef.current.stop(); } catch (_) {}
    try { streamRef.current && streamRef.current.getTracks().forEach((t) => t.stop()); } catch (_) {}
    try { ctxRef.current && ctxRef.current.close(); } catch (_) {}
    streamRef.current = null;
    ctxRef.current = null;
    analyserRef.current = null;
    rafRef.current = null;
  }

  async function beginRecording() {
    setError('');
    setElapsed(0);
    setLevel(0);
    chunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
      });
      streamRef.current = stream;
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      ctxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      analyserRef.current = analyser;

      const rec = new MediaRecorder(stream);
      recorderRef.current = rec;
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      rec.onstop = handleRecordingStopped;
      rec.start();
      startedAtRef.current = performance.now();
      setRecording(true);

      const buf = new Uint8Array(analyser.fftSize);
      const tick = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteTimeDomainData(buf);
        let peak = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = Math.abs(buf[i] - 128) / 128;
          if (v > peak) peak = v;
        }
        setLevel(peak);
        const secs = (performance.now() - startedAtRef.current) / 1000;
        setElapsed(secs);
        if (secs >= MAX_SECONDS) {
          finishRecording();
          return;
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
    } catch (e) {
      let msg = 'We couldn\'t access your microphone. Please check browser permissions and try again.';
      if (e && e.name === 'NotAllowedError') {
        msg = 'Microphone permission was denied. Grant access in your browser settings and try again.';
      } else if (e && e.name === 'NotFoundError') {
        msg = 'No microphone was detected. Connect a mic or try uploading an audio file instead.';
      }
      setError(msg);
    }
  }

  function finishRecording() {
    try { recorderRef.current && recorderRef.current.stop(); } catch (_) {}
    setRecording(false);
    try { rafRef.current && cancelAnimationFrame(rafRef.current); } catch (_) {}
    rafRef.current = null;
  }

  async function handleRecordingStopped() {
    try { streamRef.current && streamRef.current.getTracks().forEach((t) => t.stop()); } catch (_) {}
    const blob = new Blob(chunksRef.current, { type: chunksRef.current[0]?.type || 'audio/webm' });
    await analyseBlob(blob);
  }

  async function analyseBlob(source) {
    setStep('analysing');
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const buffer = source instanceof File
        ? await decodeFileToBuffer(source, ctx)
        : await decodeBlobToBuffer(source, ctx);
      const check = validateBuffer(buffer, { minSeconds: MIN_SECONDS, maxSeconds: MAX_SECONDS });
      if (!check.ok) {
        try { ctx.close(); } catch (_) {}
        setError(check.message);
        setStep('capture');
        return;
      }
      const derived = analyseBuffer(buffer, { maxSeconds: MAX_SECONDS });
      try { ctx.close(); } catch (_) {}
      // Persist server-side (Pro-only endpoint).
      const { data } = await api.post('/harmonic-blueprint/profile', derived);
      const saved = data.profile || derived;
      setProfile(saved);
      setExisting(saved);
      setStep('results');
    } catch (e) {
      setError(formatApiError(e) || 'Could not analyse audio — please try again.');
      setStep('capture');
    }
  }

  const onFilePicked = async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    setError('');
    await analyseBlob(f);
    // Reset the file input so the same file can be re-selected if needed.
    try { e.target.value = ''; } catch (_) {}
  };

  const resetToCapture = () => {
    setError('');
    setProfile(null);
    setStep('capture');
  };

  const clearProfile = async () => {
    try {
      await api.delete('/harmonic-blueprint/profile');
      setExisting(null);
      setProfile(null);
      setStep('intro');
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  if (!open) return null;

  return (
    <div
      data-testid="harmonic-blueprint-sheet"
      className="fixed inset-0 z-50 flex items-stretch justify-stretch"
    >
      <div className="absolute inset-0 bg-[#04080B]/85 backdrop-blur-md" />
      <div className="relative flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 pt-10 pb-24 sm:px-10">
          <div className="flex items-start justify-between mb-8">
            <div>
              <div className="label-tiny text-[#C4A67A] mb-2 flex items-center gap-2">
                <Sparkles size={12} /> Harmonic Blueprint
              </div>
              <h1 className="font-display text-4xl sm:text-5xl font-light tracking-tight text-[#E8E3D9]">
                {step === 'results' ? 'Your resonance profile' : 'Discover your signature'}
              </h1>
            </div>
            <button
              data-testid="harmonic-blueprint-close-button"
              type="button"
              onClick={onClose}
              className="rounded-full p-2 text-[#8A9A92] hover:text-[#E8E3D9] hover:bg-white/5 transition-colors"
              aria-label="Close"
            >
              <X size={22} />
            </button>
          </div>

          {loading && <div className="text-[#8A9A92] mt-10">Loading…</div>}

          {!loading && step === 'intro' && (
            <IntroPanel
              existing={existing}
              onBegin={() => { setError(''); setStep('capture'); }}
            />
          )}

          {!loading && step === 'capture' && (
            <CapturePanel
              recording={recording}
              elapsed={elapsed}
              level={level}
              error={error}
              onStart={beginRecording}
              onStop={finishRecording}
              onFile={onFilePicked}
              onBack={() => setStep(existing ? 'results' : 'intro')}
            />
          )}

          {!loading && step === 'analysing' && (
            <div
              data-testid="harmonic-blueprint-analysing"
              className="glass p-10 text-center"
            >
              <Waves className="mx-auto text-[#72C2AC] animate-pulse" size={36} />
              <div className="font-display text-2xl text-[#E8E3D9] mt-6">Analysing your signature…</div>
              <div className="text-[#8A9A92] text-sm mt-2">Running FFT and mapping your resonance</div>
            </div>
          )}

          {!loading && step === 'results' && profile && (
            <ResultsPanel
              profile={profile}
              onRecordAgain={resetToCapture}
              onReset={clearProfile}
            />
          )}

          {!loading && step === 'error' && (
            <div className="glass p-8">
              <div className="flex items-center gap-3 text-[#D96C6C]">
                <AlertTriangle size={20} />
                <div>{error || 'Something went wrong.'}</div>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="mt-6 px-5 py-2.5 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] text-sm font-medium transition-colors"
              >
                Close
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- Sub-panels -------------------------------------------------------

function IntroPanel({ existing, onBegin }) {
  return (
    <div className="space-y-8" data-testid="harmonic-blueprint-intro">
      <div className="glass p-8 leading-relaxed">
        <div className="text-[#C6CDCA] text-base">
          Everyone has a unique harmonic signature — a set of natural resonant
          frequencies your system gravitates toward. Life, stress, and
          environment can pull you away from that natural tuning.
        </div>
        <div className="text-[#C6CDCA] text-base mt-4">
          <span className="text-[#72C2AC]">Harmonic Blueprint</span> captures
          your voice's frequency signature, identifies where you may have
          drifted, and creates a personalised sound journey to help guide you
          back.
        </div>
      </div>

      <div className="glass p-6">
        <div className="label-tiny mb-3 text-[#C4A67A]">Privacy first</div>
        <div className="text-[#8A9A92] text-sm leading-relaxed">
          Your voice sample is <span className="text-[#E8E3D9]">processed locally</span> and
          never stored on our servers. Only your resonance profile data is saved.
          Your browser will ask for microphone permission when you continue.
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          data-testid="harmonic-blueprint-begin-button"
          type="button"
          onClick={onBegin}
          className="px-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors"
        >
          {existing ? 'Record again' : 'Begin'}
        </button>
        {existing && (
          <span className="text-[#8A9A92] text-sm">
            You already have a saved profile — recording again will replace it.
          </span>
        )}
      </div>
    </div>
  );
}

function CapturePanel({ recording, elapsed, level, error, onStart, onStop, onFile, onBack }) {
  const secondsLeft = Math.max(0, MAX_SECONDS - elapsed);
  const readyToStop = elapsed >= MIN_SECONDS;
  return (
    <div className="space-y-6" data-testid="harmonic-blueprint-capture">
      <div className="glass p-8">
        <div className="text-[#8A9A92] text-sm tracking-widest uppercase">Guided prompt</div>
        <div className="font-display text-3xl text-[#E8E3D9] mt-3 leading-tight">
          Hum a comfortable, steady <span className="italic text-[#72C2AC]">"aahhh"</span> sound.
        </div>
        <div className="text-[#8A9A92] text-sm mt-3 leading-relaxed">
          Any sustained tone works — a hum, a vowel, or a soft "om". Aim for {MIN_SECONDS}–{MAX_SECONDS} seconds
          in a quiet room, mic about a hand's width from your mouth.
        </div>
      </div>

      <div className="glass p-8 flex flex-col items-center">
        {/* Level ring */}
        <div className="relative w-40 h-40 flex items-center justify-center mb-6">
          <div className="absolute inset-0 rounded-full border border-[rgba(114,194,172,0.25)]" />
          <div
            className="absolute inset-0 rounded-full border-2 transition-all duration-100"
            style={{
              transform: `scale(${1 + Math.min(0.35, level * 0.5)})`,
              borderColor: recording ? '#72C2AC' : '#3A4A45',
              opacity: recording ? 0.75 : 0.25,
            }}
          />
          <div className="relative text-center">
            <div className="font-display text-4xl text-[#E8E3D9]">
              {elapsed.toFixed(1)}<span className="text-lg text-[#8A9A92]">s</span>
            </div>
            <div className="text-[10px] tracking-widest text-[#8A9A92] mt-1 uppercase">
              {recording ? (readyToStop ? 'Ready to stop' : `Need ${(MIN_SECONDS - elapsed).toFixed(1)}s more`) : 'Ready'}
            </div>
          </div>
        </div>

        {!recording ? (
          <button
            data-testid="harmonic-blueprint-record-button"
            type="button"
            onClick={onStart}
            className="px-7 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors inline-flex items-center gap-2"
          >
            <Mic size={16} /> Start recording
          </button>
        ) : (
          <button
            data-testid="harmonic-blueprint-stop-button"
            type="button"
            onClick={onStop}
            disabled={!readyToStop}
            className="px-7 py-3 rounded-full bg-[#C4A67A] hover:bg-[#D6B98A] text-[#08120F] font-medium tracking-wide transition-colors disabled:opacity-40 inline-flex items-center gap-2"
          >
            <Check size={16} /> Stop &amp; analyse
          </button>
        )}

        <div className="text-[10px] tracking-widest text-[#5A6B65] mt-4 uppercase">
          Auto-stops at {MAX_SECONDS}s · {secondsLeft.toFixed(1)}s left
        </div>
      </div>

      <div className="glass p-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="label-tiny text-[#C4A67A]">Prefer to upload?</div>
          <div className="text-[#8A9A92] text-sm mt-1">
            Any short audio recording (WAV, MP3, M4A, WebM). Same local FFT — nothing leaves your device.
          </div>
        </div>
        <label
          data-testid="harmonic-blueprint-upload-label"
          className="cursor-pointer px-5 py-2.5 rounded-full border border-[rgba(114,194,172,0.35)] text-[#72C2AC] hover:bg-[#72C2AC]/10 transition-colors text-sm inline-flex items-center gap-2"
        >
          <Upload size={14} /> Choose file
          <input
            data-testid="harmonic-blueprint-upload-input"
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={onFile}
          />
        </label>
      </div>

      {error && (
        <div
          data-testid="harmonic-blueprint-error"
          className="glass p-5 border border-[rgba(217,108,108,0.4)] text-[#D96C6C] text-sm flex items-start gap-3"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> <span>{error}</span>
        </div>
      )}

      <div className="pt-2">
        <button
          type="button"
          onClick={onBack}
          className="text-[#8A9A92] hover:text-[#E8E3D9] text-sm transition-colors"
        >
          ← Back
        </button>
      </div>
    </div>
  );
}

function ResultsPanel({ profile, onRecordAgain, onReset }) {
  return (
    <div className="space-y-6" data-testid="harmonic-blueprint-results">
      <SpectrumMap profile={profile} />
      <BandGrid bands={profile.bands} />
      <div className="grid sm:grid-cols-2 gap-4">
        <FrequencyList
          title="Dominant frequencies"
          hint="Where your voice concentrates energy"
          items={profile.dominant}
          accent="#72C2AC"
        />
        <FrequencyList
          title="Notable dips"
          hint="Frequencies you're moving away from"
          items={profile.dips}
          accent="#C4A67A"
        />
      </div>
      {profile.underrepresented && profile.underrepresented.length > 0 && (
        <div className="glass p-6" data-testid="harmonic-blueprint-underrepresented">
          <div className="label-tiny text-[#C4A67A]">Underrepresented ranges</div>
          <div className="text-[#8A9A92] text-sm mt-1 mb-4">
            Bands where your signal is markedly below the rest of your spectrum.
          </div>
          <div className="flex flex-wrap gap-2">
            {profile.underrepresented.map((b) => (
              <span
                key={b.key}
                className="px-3 py-1.5 rounded-full bg-[#C4A67A]/10 text-[#C4A67A] text-xs tracking-wide"
              >
                {b.label} · {b.lo}–{b.hi} Hz
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 pt-2">
        <button
          data-testid="harmonic-blueprint-record-again-button"
          type="button"
          onClick={onRecordAgain}
          className="px-5 py-2.5 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] text-sm font-medium transition-colors inline-flex items-center gap-2"
        >
          <RotateCcw size={14} /> Record again
        </button>
        <button
          data-testid="harmonic-blueprint-reset-button"
          type="button"
          onClick={onReset}
          className="text-[#8A9A92] hover:text-[#D96C6C] text-sm transition-colors"
        >
          Delete profile
        </button>
      </div>
    </div>
  );
}

function SpectrumMap({ profile }) {
  // Simple SVG spectrum plot. Y axis: 0 (peak) → -60 dB. X axis: bin index.
  const w = 720, h = 220, pad = 24;
  const pts = profile.spectrum || [];
  // Guard: very sparse spectra (e.g. a stale seed doc) can't be plotted
  // meaningfully — render a friendly empty state instead of a broken chart.
  if (pts.length < 2) {
    return (
      <div className="glass p-6 text-[#8A9A92] text-sm" data-testid="harmonic-blueprint-spectrum">
        Not enough spectral data yet — record a fresh sample to build the map.
      </div>
    );
  }
  const minDb = -60, maxDb = 0;
  const xy = pts.map((p, i) => {
    const x = pad + (i / (pts.length - 1)) * (w - pad * 2);
    const y = pad + (1 - (Math.max(minDb, Math.min(maxDb, p.db)) - minDb) / (maxDb - minDb)) * (h - pad * 2);
    return { x, y, hz: p.hz };
  });
  const line = xy.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const areaBottom = h - pad;
  const area = `M${xy[0].x},${areaBottom} ${xy.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')} L${xy[xy.length - 1].x},${areaBottom} Z`;
  const hzToX = (hz) => {
    // Nearest point search — cheap and accurate enough for annotations.
    let best = 0, bestD = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const d = Math.abs(pts[i].hz - hz);
      if (d < bestD) { bestD = d; best = i; }
    }
    return xy[best].x;
  };
  // Fixed logarithmic-ish x-axis ticks so the axis reads sensibly across the
  // 60 Hz – 4 kHz vocal band regardless of spectrum-array length.
  const xTicks = [100, 250, 500, 1000, 2000, 4000].filter(
    (hz) => hz >= pts[0].hz && hz <= pts[pts.length - 1].hz,
  );
  return (
    <div className="glass p-6" data-testid="harmonic-blueprint-spectrum">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="label-tiny text-[#C4A67A]">Spectrum map</div>
          <div className="text-[#8A9A92] text-sm mt-1">60 Hz — 4 kHz · averaged over {profile.duration}s</div>
        </div>
        <div className="text-[#5A6B65] text-xs tracking-widest uppercase">FFT · {profile.fft_size}</div>
      </div>
      <svg viewBox={`0 0 ${w} ${h + 18}`} className="w-full h-auto">
        <defs>
          <linearGradient id="hb-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#72C2AC" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#72C2AC" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* Grid */}
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <line
            key={t}
            x1={pad} x2={w - pad}
            y1={pad + t * (h - pad * 2)} y2={pad + t * (h - pad * 2)}
            stroke="rgba(114,194,172,0.08)"
          />
        ))}
        <path d={area} fill="url(#hb-fill)" />
        <path d={line} fill="none" stroke="#72C2AC" strokeWidth="1.5" />
        {/* Dominant markers */}
        {profile.dominant && profile.dominant.map((p, i) => (
          <g key={`dom-${i}`}>
            <line
              x1={hzToX(p.hz)} x2={hzToX(p.hz)} y1={pad} y2={h - pad}
              stroke="rgba(114,194,172,0.35)" strokeDasharray="2 4"
            />
            <text
              x={hzToX(p.hz)} y={pad - 6}
              textAnchor="middle" fontSize="10"
              fill="#72C2AC" fontFamily="ui-monospace, monospace"
            >
              {Math.round(p.hz)} Hz
            </text>
          </g>
        ))}
        {/* Dip markers */}
        {profile.dips && profile.dips.map((p, i) => (
          <g key={`dip-${i}`}>
            <line
              x1={hzToX(p.hz)} x2={hzToX(p.hz)} y1={pad} y2={h - pad}
              stroke="rgba(196,166,122,0.4)" strokeDasharray="1 3"
            />
            <text
              x={hzToX(p.hz)} y={h - pad + 14}
              textAnchor="middle" fontSize="10"
              fill="#C4A67A" fontFamily="ui-monospace, monospace"
            >
              {Math.round(p.hz)} Hz
            </text>
          </g>
        ))}
        {/* Fixed x-axis frequency ticks (so the axis is always readable). */}
        {xTicks.map((hz) => (
          <text
            key={`tick-${hz}`}
            x={hzToX(hz)} y={h + 12}
            textAnchor="middle" fontSize="9"
            fill="#5A6B65" fontFamily="ui-monospace, monospace"
          >
            {hz >= 1000 ? `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 1)}k` : `${hz}`} Hz
          </text>
        ))}
      </svg>
      <div className="flex items-center gap-6 text-xs text-[#8A9A92] mt-3">
        <span className="inline-flex items-center gap-2"><span className="w-3 h-0.5 bg-[#72C2AC]" /> Signal</span>
        <span className="inline-flex items-center gap-2"><span className="w-3 h-0.5 bg-[#72C2AC] opacity-50" style={{ borderTop: '1px dashed' }} /> Dominant</span>
        <span className="inline-flex items-center gap-2"><span className="w-3 h-0.5 bg-[#C4A67A] opacity-60" style={{ borderTop: '1px dashed' }} /> Dip</span>
      </div>
    </div>
  );
}

function BandGrid({ bands }) {
  if (!bands || bands.length === 0) return null;
  const min = Math.min(...bands.map((b) => b.db));
  const max = Math.max(...bands.map((b) => b.db));
  const range = Math.max(1, max - min);
  return (
    <div className="glass p-6" data-testid="harmonic-blueprint-bands">
      <div className="label-tiny text-[#C4A67A] mb-4">Band energy</div>
      <div className="space-y-3">
        {bands.map((b) => {
          const pct = Math.max(4, Math.round(((b.db - min) / range) * 100));
          return (
            <div key={b.key} className="flex items-center gap-3">
              <div className="w-28 text-[#C6CDCA] text-sm">{b.label}</div>
              <div className="flex-1 h-2 bg-[rgba(114,194,172,0.08)] rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-[#5C9E8C] to-[#72C2AC]"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="w-24 text-right text-[#8A9A92] text-xs font-mono">
                {b.lo}–{b.hi} Hz
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FrequencyList({ title, hint, items, accent }) {
  return (
    <div className="glass p-6">
      <div className="label-tiny" style={{ color: accent }}>{title}</div>
      <div className="text-[#8A9A92] text-xs mt-1 mb-4">{hint}</div>
      {items && items.length > 0 ? (
        <ul className="space-y-2">
          {items.map((p, i) => (
            <li key={`${p.hz}-${i}`} className="flex items-center justify-between text-sm">
              <span className="text-[#E8E3D9] font-mono">{Math.round(p.hz)} Hz</span>
              <span className="text-[#8A9A92] font-mono text-xs">{p.db.toFixed(1)} dB</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-[#5A6B65] text-sm italic">None detected.</div>
      )}
    </div>
  );
}
