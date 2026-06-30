import React, { useEffect, useRef, useState } from 'react';
import { X, Ear, Headphones, ChevronRight, Check, AudioLines, RotateCcw } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import api from '@/lib/api';

/**
 * CalibrationModal — onboarding-style hearing calibration.
 *
 * Flow:
 *   1) "Welcome" screen explaining the why + headphone tip.
 *   2) Volume reference: plays a 1 kHz tone at a comfortable level so the
 *      user can set their device volume before the faint tones begin.
 *   3) Tone loop: 9 bands (60 Hz - 12 kHz), each at -30 dB for ~2.5s. After
 *      each tone the user taps "I hear it" / "Not really" / "Play again".
 *   4) Results: POSTs {bands:[{freq,heard}, ...]} to /me/hearing-profile.
 *      Server computes per-band gain_db and returns the saved profile.
 *      Engine immediately applies the EQ chain.
 *
 * Skip / Cancel at every step. Acceptance requires playback to remain
 * available even if the user never calibrates.
 */
const TEST_BANDS = [60, 125, 250, 500, 1000, 2000, 4000, 8000, 12000];
const TONE_MS = 2500;

const STEPS = ['welcome', 'volume', 'test', 'results'];

export default function CalibrationModal({ open, onClose, onComplete }) {
  const [step, setStep] = useState('welcome');
  const [bandIdx, setBandIdx] = useState(0);
  const [responses, setResponses] = useState({}); // {freq: heard:bool}
  const [playing, setPlaying] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const [savedProfile, setSavedProfile] = useState(null);
  const stopRequestedRef = useRef(false);

  useEffect(() => {
    if (!open) {
      setStep('welcome');
      setBandIdx(0);
      setResponses({});
      setSavedProfile(null);
      setErr('');
    }
  }, [open]);

  const close = () => {
    stopRequestedRef.current = true;
    onClose && onClose();
  };

  const handleSkip = async () => {
    try {
      await api.post('/me/hearing-profile', { skipped: true });
    } catch (e) {
      console.warn('[Calibration] skip persist failed', e);
    }
    if (onComplete) onComplete({ skipped: true });
    close();
  };

  // ---- Step 1 → 2: volume reference ---------------------------------------
  const playVolumeReference = async () => {
    setErr('');
    setPlaying(true);
    try {
      // Slightly louder than the test tones so the user can pick a comfortable
      // level. -16 dB is loud enough to hear clearly without being startling.
      await audioEngine.playCalibrationTone(1000, 1800, -16);
    } catch (e) {
      console.warn('[Calibration] reference tone failed', e);
      setErr('Could not play the reference tone on this device.');
    }
    setPlaying(false);
  };

  // ---- Step 2 → 3: band loop ----------------------------------------------
  const playCurrentBand = async () => {
    setErr('');
    setPlaying(true);
    try {
      await audioEngine.playCalibrationTone(TEST_BANDS[bandIdx], TONE_MS, -30);
    } catch (e) {
      console.warn('[Calibration] band tone failed', e);
      setErr('Could not play this band.');
    }
    setPlaying(false);
  };

  const recordResponse = async (heard) => {
    const freq = TEST_BANDS[bandIdx];
    const next = { ...responses, [freq]: heard };
    setResponses(next);
    if (bandIdx + 1 >= TEST_BANDS.length) {
      // All bands answered — submit to backend.
      await submitProfile(next);
    } else {
      setBandIdx(bandIdx + 1);
      // Tiny breath between tones so the user doesn't feel rushed.
      setTimeout(() => { playCurrentBand(); }, 350);
    }
  };

  // ---- Step 3 → 4: submit + apply ----------------------------------------
  const submitProfile = async (responsesByFreq) => {
    setSaving(true);
    setErr('');
    try {
      const bands = TEST_BANDS.map((f) => ({ freq: f, heard: !!responsesByFreq[f] }));
      const { data } = await api.post('/me/hearing-profile', { bands });
      setSavedProfile(data);
      audioEngine.setHearingProfile(data);
      setStep('results');
      if (onComplete) onComplete({ skipped: false, profile: data });
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Could not save calibration.';
      setErr(typeof msg === 'string' ? msg : 'Save failed.');
    }
    setSaving(false);
  };

  const restartTest = () => {
    setResponses({});
    setBandIdx(0);
    setSavedProfile(null);
    setStep('test');
    setTimeout(() => { playCurrentBand(); }, 300);
  };

  if (!open) return null;

  return (
    <div
      data-testid="calibration-modal"
      className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center bg-black/70 backdrop-blur-sm p-0 sm:p-4"
      role="dialog"
      aria-modal="true"
    >
      <div className="w-full sm:max-w-md bg-[#0E1F18] border border-[#5C9E8C]/25 rounded-t-3xl sm:rounded-2xl shadow-2xl overflow-hidden flex flex-col" style={{ maxHeight: '90vh' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#5C9E8C]/15">
          <div className="flex items-center gap-2">
            <Ear size={14} className="text-[#C4A67A]" />
            <div className="label-tiny text-[#C4A67A]">Equalizer Calibration</div>
          </div>
          <button
            data-testid="calibration-close"
            onClick={close}
            className="text-[#8A9A92] hover:text-[#E8E3D9] p-1"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Step progress bar */}
        <div className="px-5 py-2 flex items-center gap-1.5 border-b border-[#5C9E8C]/10">
          {STEPS.map((s) => (
            <div
              key={s}
              className={`h-0.5 flex-1 rounded-full transition-colors ${
                STEPS.indexOf(step) >= STEPS.indexOf(s) ? 'bg-[#72C2AC]/70' : 'bg-[#5C9E8C]/15'
              }`}
            />
          ))}
        </div>

        {/* Body */}
        <div className="px-5 py-5 overflow-y-auto custom-scrollbar space-y-4">
          {step === 'welcome' && (
            <div data-testid="cal-step-welcome" className="space-y-4">
              <div className="flex items-center gap-3 p-3 rounded-xl border border-[#5C9E8C]/20 bg-black/30">
                <Headphones size={20} className="text-[#C4A67A] shrink-0" />
                <div className="text-sm text-[#E8E3D9]/90 leading-relaxed">
                  Use headphones for the most accurate result. Find a quiet room. Takes about 30 seconds.
                </div>
              </div>
              <p className="text-sm text-[#E8E3D9]/85 leading-relaxed">
                We&apos;ll play a few faint tones at different frequencies. Tap whether you can hear each one. The app builds a personal profile and gently boosts the bands your ears (or your hardware) under-deliver — so Solfeggio tones and binaural beats come through evenly.
              </p>
              <p className="text-xs text-[#8A9A92] leading-relaxed">
                Audio playback works perfectly without this. You can skip and run it later from <span className="text-[#C4A67A]">Account → Hearing profile</span>.
              </p>
              <div className="flex items-center gap-2 pt-2">
                <button
                  data-testid="cal-begin"
                  onClick={() => setStep('volume')}
                  className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-3 rounded-full bg-[#C4A67A] text-[#08120F] text-sm font-medium tracking-wider hover:bg-[#d6b88c] transition-colors"
                >
                  Begin calibration <ChevronRight size={14} />
                </button>
                <button
                  data-testid="cal-skip"
                  onClick={handleSkip}
                  className="px-4 py-3 rounded-full text-sm text-[#8A9A92] hover:text-[#E8E3D9] transition-colors"
                >
                  Skip
                </button>
              </div>
            </div>
          )}

          {step === 'volume' && (
            <div data-testid="cal-step-volume" className="space-y-4">
              <div className="font-display text-xl text-[#E8E3D9] tracking-wide">Set your volume</div>
              <p className="text-sm text-[#E8E3D9]/85 leading-relaxed">
                Tap <span className="text-[#C4A67A]">Play reference tone</span>, then adjust your device&apos;s volume slider until the tone is at a <strong>comfortable, conversational level</strong>. The test tones will be quieter — that&apos;s by design.
              </p>
              <button
                data-testid="cal-play-reference"
                onClick={playVolumeReference}
                disabled={playing}
                className={`w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-full text-sm transition-colors ${
                  playing
                    ? 'bg-[#5C9E8C]/15 text-[#72C2AC]'
                    : 'bg-[#5C9E8C]/15 text-[#72C2AC] hover:bg-[#5C9E8C]/25'
                }`}
              >
                <AudioLines size={14} /> {playing ? 'Playing 1 kHz…' : 'Play reference tone'}
              </button>
              <div className="flex items-center gap-2 pt-2">
                <button
                  data-testid="cal-volume-ready"
                  onClick={() => { setBandIdx(0); setResponses({}); setStep('test'); setTimeout(playCurrentBand, 400); }}
                  className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-3 rounded-full bg-[#C4A67A] text-[#08120F] text-sm font-medium tracking-wider hover:bg-[#d6b88c] transition-colors"
                >
                  Volume looks good — start <ChevronRight size={14} />
                </button>
                <button
                  data-testid="cal-skip"
                  onClick={handleSkip}
                  className="px-4 py-3 rounded-full text-sm text-[#8A9A92] hover:text-[#E8E3D9] transition-colors"
                >
                  Skip
                </button>
              </div>
            </div>
          )}

          {step === 'test' && (
            <div data-testid="cal-step-test" className="space-y-4">
              <div className="text-center">
                <div className="label-tiny text-[#C4A67A] mb-2">
                  Tone {bandIdx + 1} of {TEST_BANDS.length}
                </div>
                <div data-testid="cal-current-band" className="font-display text-3xl text-[#E8E3D9]">
                  {TEST_BANDS[bandIdx] >= 1000
                    ? `${(TEST_BANDS[bandIdx] / 1000).toFixed(TEST_BANDS[bandIdx] % 1000 ? 1 : 0)} kHz`
                    : `${TEST_BANDS[bandIdx]} Hz`}
                </div>
                <div className="text-xs text-[#8A9A92] mt-1">Can you hear this tone?</div>
              </div>

              {/* Visual playing indicator */}
              <div className="flex items-center justify-center gap-1 h-8">
                {[0, 1, 2, 3, 4].map((i) => (
                  <div
                    key={i}
                    className={`w-1 rounded-full transition-all ${
                      playing
                        ? 'bg-[#72C2AC]/70'
                        : 'bg-[#5C9E8C]/20'
                    }`}
                    style={{
                      height: playing ? `${10 + Math.abs(Math.sin((bandIdx + i) * 1.7)) * 18}px` : '4px',
                      animation: playing ? `pulse 1.2s ease-in-out ${i * 0.12}s infinite` : 'none',
                    }}
                  />
                ))}
              </div>

              <div className="grid grid-cols-2 gap-2">
                <button
                  data-testid="cal-heard"
                  disabled={playing}
                  onClick={() => recordResponse(true)}
                  className="px-4 py-3 rounded-xl border border-[#72C2AC]/40 bg-[#72C2AC]/10 text-[#72C2AC] text-sm hover:bg-[#72C2AC]/20 transition-colors disabled:opacity-50"
                >
                  I hear it
                </button>
                <button
                  data-testid="cal-not-heard"
                  disabled={playing}
                  onClick={() => recordResponse(false)}
                  className="px-4 py-3 rounded-xl border border-[#C4A67A]/30 bg-[#C4A67A]/5 text-[#C4A67A] text-sm hover:bg-[#C4A67A]/15 transition-colors disabled:opacity-50"
                >
                  Not really
                </button>
              </div>
              <button
                data-testid="cal-replay"
                disabled={playing}
                onClick={playCurrentBand}
                className="w-full inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-full text-xs text-[#8A9A92] hover:text-[#72C2AC] transition-colors disabled:opacity-50"
              >
                <RotateCcw size={11} /> Play this tone again
              </button>
              <div className="text-center">
                <button
                  data-testid="cal-skip"
                  onClick={handleSkip}
                  className="text-[11px] text-[#8A9A92] hover:text-[#E8E3D9] underline-offset-2 hover:underline transition-colors"
                >
                  Skip the rest
                </button>
              </div>
            </div>
          )}

          {step === 'results' && savedProfile && (
            <div data-testid="cal-step-results" className="space-y-4">
              <div className="flex items-center gap-3 p-3 rounded-xl border border-[#72C2AC]/30 bg-[#5C9E8C]/10">
                <Check size={18} className="text-[#72C2AC] shrink-0" />
                <div className="text-sm text-[#E8E3D9]/90">
                  Profile saved. Your audio is now equalised for your ears.
                </div>
              </div>
              <div className="text-xs text-[#8A9A92] mb-1">Your hearing curve</div>
              <CurveChart bands={savedProfile.bands} />
              <ul className="text-[11px] text-[#8A9A92] space-y-1 pt-2 border-t border-[#5C9E8C]/15">
                {savedProfile.bands
                  .filter((b) => Math.abs(b.gain_db) > 0.1)
                  .slice(0, 4)
                  .map((b) => (
                    <li key={b.freq}>
                      · {b.freq >= 1000 ? `${(b.freq / 1000).toFixed(b.freq % 1000 ? 1 : 0)} kHz` : `${b.freq} Hz`}
                      {' '}— boosted by {b.gain_db.toFixed(1)} dB
                    </li>
                  ))}
                {savedProfile.bands.every((b) => Math.abs(b.gain_db) < 0.1) && (
                  <li>· No compensation needed — your hearing is even across all tested bands.</li>
                )}
              </ul>
              <div className="flex items-center gap-2 pt-2">
                <button
                  data-testid="cal-done"
                  onClick={close}
                  className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-3 rounded-full bg-[#C4A67A] text-[#08120F] text-sm font-medium tracking-wider hover:bg-[#d6b88c] transition-colors"
                >
                  Done
                </button>
                <button
                  data-testid="cal-redo"
                  onClick={restartTest}
                  className="inline-flex items-center gap-1.5 px-4 py-3 rounded-full text-sm text-[#8A9A92] hover:text-[#E8E3D9] transition-colors"
                >
                  <RotateCcw size={12} /> Redo
                </button>
              </div>
            </div>
          )}

          {(err || saving) && (
            <div className="text-xs">
              {saving && <div data-testid="cal-saving" className="text-[#8A9A92] italic">Saving profile…</div>}
              {err && <div data-testid="cal-error" className="text-[#E07A5F]">{err}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Tiny inline SVG bar chart of the user's audiogram. Bars above the
 *  midline = boosted, at midline = flat. Width auto-scales to container. */
function CurveChart({ bands }) {
  const W = 320;
  const H = 90;
  const PAD = 8;
  const max = 9; // dB scale
  const step = (W - PAD * 2) / bands.length;
  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[90px]" data-testid="cal-curve-chart">
        <line x1={PAD} x2={W - PAD} y1={H / 2} y2={H / 2} stroke="#5C9E8C" strokeOpacity="0.25" strokeDasharray="2 3" />
        {bands.map((b, i) => {
          const x = PAD + i * step + step / 2;
          const h = Math.max(2, (Math.abs(b.gain_db) / max) * (H / 2 - PAD));
          const y = b.gain_db >= 0 ? H / 2 - h : H / 2;
          const fill = b.heard ? '#5C9E8C' : '#C4A67A';
          return (
            <g key={b.freq}>
              <rect x={x - step * 0.35} y={y} width={step * 0.7} height={h} rx="2" fill={fill} fillOpacity={b.heard ? 0.45 : 0.85} />
              <text x={x} y={H - 2} textAnchor="middle" fontSize="8" fill="#5C9E8C" opacity="0.7">
                {b.freq >= 1000 ? `${b.freq / 1000}k` : b.freq}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
