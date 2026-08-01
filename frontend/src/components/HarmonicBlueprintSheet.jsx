import React, { useEffect, useRef, useState } from 'react';
import { X, Mic, Upload, RotateCcw, Waves, Check, AlertTriangle, Sparkles, Anchor } from 'lucide-react';
import api, { formatApiError } from '@/lib/api';
import {
  validateBuffer, analyseBuffer,
  decodeBlobToBuffer, decodeFileToBuffer,
  compareToEigenmode,
} from '@/lib/harmonicBlueprintEngine';
import HarmonicJourneyPlayer from '@/components/HarmonicJourneyPlayer';
import ResonanceScoreReveal, { computeResonanceScore } from '@/components/ResonanceScoreReveal';
import { BeforeAfterCelebration } from '@/components/BeforeAfterMap';

/**
 * Full-screen Harmonic Blueprint experience — voice capture, FFT analysis,
 * eigenmode drift comparison, and visual resonance map.
 *
 * Steps: intro → capture → analysing → (review-findings | eigenmode-saved) → results.
 *
 * The raw audio blob is NEVER uploaded. FFT runs locally; only the derived
 * profile JSON (± confirmed_gaps) is POSTed.
 *
 * Phase 2 (Eigenmode Tuning): the user's first-ever capture is stored as
 * their `is_eigenmode` baseline. Subsequent captures compare against it in
 * a "Review findings" step where the user selects which gaps feel relevant
 * before the profile is saved. Any profile can later be promoted to become
 * the new baseline via 'Set as new baseline'.
 */
const MIN_SECONDS = 10;
const MAX_SECONDS = 30;

export default function HarmonicBlueprintSheet({ open, onClose, isPro = true, onOpenAccount, initialData = null }) {
  // Seed state from pre-fetched Dashboard cache so the sheet renders its
  // final panel on the very first frame instead of gating on a loading
  // spinner. A background refresh (see effect below) then quietly reconciles.
  const seedProfile = initialData && initialData.profile ? initialData.profile.profile : null;
  const seedEigen = initialData && initialData.profile ? initialData.profile.eigenmode : null;
  const seedJourney = (initialData && initialData.journey) || null;

  const [existing, setExisting] = useState(seedProfile);   // latest saved profile or null
  const [eigenmode, setEigenmode] = useState(seedEigen);   // baseline profile or null
  const [loading, setLoading] = useState(!initialData);
  // intro | tipsGate | tipsRitual | capture | analysing | scoreReveal | review | eigenmodeSaved | results | error
  const [step, setStep] = useState(seedProfile ? 'results' : 'intro');
  // Phase 11 — Resonance / Drift Score. Computed client-side after FFT
  // completes and BEFORE the review step, so users see how closely they're
  // aligned with their eigenmode baseline before they see the gaps.
  const [resonanceScore, setResonanceScore] = useState(null);
  // Phase 12b — Before/After celebration overlay shown every 5th capture.
  // `data` is the payload from /harmonic-blueprint/before-after with
  // `show_celebration: true`. `null` when the overlay isn't active.
  const [celebrationData, setCelebrationData] = useState(null);
  // Phase 9 — tips-skipped preference loaded from /me/settings on open. When
  // true, IntroPanel's Begin flows straight to `capture`, bypassing the
  // Setup Tips ritual. Default false so first-time users see the tips.
  const [hbTipsSkipped, setHbTipsSkipped] = useState(false);
  const [savingTipsPref, setSavingTipsPref] = useState(false);
  const [error, setError] = useState('');
  const [profile, setProfile] = useState(seedProfile);
  // Phase 2: findings pending user confirmation. `selected` is a Set of finding keys.
  const [pendingDerived, setPendingDerived] = useState(null);
  const [pendingFindings, setPendingFindings] = useState([]);
  const [selectedKeys, setSelectedKeys] = useState(() => new Set());
  const [savingReview, setSavingReview] = useState(false);
  // Phase 3: Eigenmode Journey — server-generated personalised playlist.
  const [journey, setJourney] = useState(seedJourney);
  const [journeyLoading, setJourneyLoading] = useState(false);
  const [journeyError, setJourneyError] = useState('');

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

  // Background refresh whenever the sheet opens — parallelised so users
  // never wait on two sequential round-trips. We only show the full-panel
  // loading spinner on the FIRST-ever open before any cache exists; every
  // subsequent open renders the last-known state immediately and reconciles
  // in the background.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const hadSeed = !!(existing || eigenmode || journey);
    if (!hadSeed) setLoading(true);
    setError('');
    (async () => {
      try {
        const [pRes, jRes] = await Promise.allSettled([
          api.get('/harmonic-blueprint/profile'),
          api.get('/harmonic-blueprint/journey'),
        ]);
        if (!alive) return;
        let profileData = { profile: null, eigenmode: null };
        if (pRes.status === 'fulfilled') {
          profileData = pRes.value.data || profileData;
        } else if (pRes.reason && pRes.reason.response && pRes.reason.response.status !== 402) {
          // Only escalate to the error step for genuine 4xx client failures
          // (e.g. 401 unauth). Server-side hiccups (5xx, Cloudflare 502/520/
          // 522, transient 404 from a mid-deploy backend) or network drops
          // fall through so the user can still enter the intro / capture
          // flow with a soft inline banner. Prevents a Cloudflare error
          // page from filling the sheet.
          const s = pRes.reason.response.status;
          const isTransient = !s || s >= 500 || s === 404;
          if (!isTransient) throw pRes.reason;
          // Show a subtle inline notice but keep the sheet usable.
          setError(formatApiError(pRes.reason));
        }
        setExisting(profileData.profile || null);
        setEigenmode(profileData.eigenmode || null);
        setProfile(profileData.profile || null);
        if (jRes.status === 'fulfilled') {
          setJourney((jRes.value.data && jRes.value.data.journey) || null);
        }
        // Only auto-flip the step if we're still on the initial intro/results
        // choice — never yank the user out of review / eigenmodeSaved / etc.
        setStep((cur) => {
          if (cur !== 'intro' && cur !== 'results') return cur;
          return profileData.profile ? 'results' : 'intro';
        });
      } catch (e) {
        if (!alive) return;
        // Only escalate to the error step if we had no seed to fall back on.
        if (!hadSeed) {
          setError(formatApiError(e));
          setStep('error');
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Phase 3: request a fresh personalised journey for the current user.
  // Works for both Pro (full playlist) and free (2-track preview + upgrade CTA).
  const generateJourney = async () => {
    setJourneyError('');
    setJourneyLoading(true);
    try {
      const { data } = await api.post('/harmonic-blueprint/journey/generate');
      setJourney(data);
    } catch (e) {
      setJourneyError(formatApiError(e));
    } finally {
      setJourneyLoading(false);
    }
  };

  useEffect(() => () => stopStream(), []);
  useEffect(() => { if (!open) stopStream(); }, [open]);

  // Phase 9 — load `hb_tips_skipped` when the sheet opens so IntroPanel's
  // Begin knows whether to route through the tips ritual or straight to
  // capture. Silent on failure — defaults to showing tips.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    (async () => {
      try {
        const { data } = await api.get('/me/settings');
        if (alive && data && typeof data === 'object') {
          setHbTipsSkipped(!!data.hb_tips_skipped);
        }
      } catch (_) { /* graceful */ }
    })();
    return () => { alive = false; };
  }, [open]);

  const beginFromIntro = () => {
    setError('');
    // If the user has permanently opted out of tips, skip straight to
    // capture — matches "returning users can bypass this screen".
    setStep(hbTipsSkipped ? 'capture' : 'tipsGate');
  };

  const saveTipsSkippedPreference = async (skipped) => {
    // Optimistic UI — no need to gate the ritual on the network round-trip.
    setHbTipsSkipped(skipped);
    setSavingTipsPref(true);
    try { await api.post('/me/settings', { hb_tips_skipped: skipped }); }
    catch (_) { /* graceful — local state stays, next visit will sync */ }
    finally { setSavingTipsPref(false); }
  };


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
      // Phase 2 branch: if the user already has an eigenmode baseline, route
      // into the Review-Findings step. Otherwise this becomes their baseline
      // and we save immediately.
      if (eigenmode) {
        const findings = compareToEigenmode(derived, eigenmode);
        setPendingDerived(derived);
        setPendingFindings(findings);
        // Default: pre-select the top finding so users see the affordance;
        // they can toggle any/all off before saving.
        setSelectedKeys(new Set(findings.slice(0, 1).map((f) => f.key)));
        // Phase 11 — compute Resonance Score locally and reveal it BEFORE
        // findings. Matches the server-side formula so the number persisted
        // on save equals what the user just saw.
        try {
          const score = computeResonanceScore(derived?.spectrum, eigenmode?.spectrum);
          setResonanceScore(score);
        } catch (_) { setResonanceScore(100); }
        setStep('scoreReveal');
        return;
      }
      // First-ever capture — this IS the eigenmode.
      const { data } = await api.post('/harmonic-blueprint/profile', derived);
      const saved = data.profile || derived;
      setProfile(saved);
      setExisting(saved);
      setEigenmode(saved);
      // No comparison possible for a first capture — it IS the baseline.
      setResonanceScore(100);
      setStep('eigenmodeSaved');
    } catch (e) {
      setError(formatApiError(e) || 'Could not analyse audio — please try again.');
      setStep('capture');
    }
  }

  // Confirm the current review-findings selection and persist the profile
  // to the server with `confirmed_gaps` populated.
  async function confirmFindings() {
    if (!pendingDerived) return;
    setSavingReview(true);
    try {
      const confirmed_gaps = pendingFindings
        .filter((f) => selectedKeys.has(f.key))
        .map((f) => ({
          key: f.key, label: f.label, description: f.description,
          direction: f.direction, delta_db: f.delta_db, lo: f.lo, hi: f.hi,
        }));
      const payload = { ...pendingDerived, confirmed_gaps };
      const { data } = await api.post('/harmonic-blueprint/profile', payload);
      const saved = data.profile || payload;
      setProfile(saved);
      setExisting(saved);
      setPendingDerived(null);
      setPendingFindings([]);
      setSelectedKeys(new Set());
      setStep('results');
      // Phase 12b — after every 5th capture, gently celebrate progress by
      // surfacing the before/after map. Fetch is non-blocking; failure just
      // means no overlay this session.
      try {
        const ba = await api.get('/harmonic-blueprint/before-after');
        if (ba.data && ba.data.show_celebration) {
          setCelebrationData(ba.data);
        }
      } catch (_) { /* silent */ }
    } catch (e) {
      setError(formatApiError(e) || 'Could not save findings — please try again.');
    } finally {
      setSavingReview(false);
    }
  }

  function toggleFinding(key) {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  // Promote the current (latest) profile to become the user's new eigenmode
  // baseline. Used by the "Set as new baseline" action on the results panel.
  async function promoteCurrentAsEigenmode() {
    if (!profile || !profile.id) return;
    try {
      const { data } = await api.post(`/harmonic-blueprint/eigenmode/promote/${profile.id}`);
      const newEigen = data.eigenmode || profile;
      setEigenmode(newEigen);
      // Also flip the flag on the local profile so the banner reflects the
      // new baseline state immediately (server has already updated the doc).
      setProfile((prev) => (prev && prev.id === newEigen.id ? { ...prev, is_eigenmode: true } : prev));
    } catch (e) {
      setError(formatApiError(e));
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
      setEigenmode(null);
      setPendingDerived(null);
      setPendingFindings([]);
      setSelectedKeys(new Set());
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
                {step === 'results' ? 'Your resonance profile'
                  : step === 'review' ? 'Review your findings'
                  : step === 'scoreReveal' ? 'Your Resonance Score'
                  : step === 'eigenmodeSaved' ? 'Your natural baseline is set'
                  : 'Discover your signature'}
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
              isPro={isPro}
              onBegin={beginFromIntro}
              onPreviewJourney={async () => {
                setError('');
                await generateJourney();
                setStep('freePreview');
              }}
              onUpgrade={onOpenAccount}
            />
          )}

          {!loading && step === 'freePreview' && (
            <FreePreviewPanel
              journey={journey}
              journeyLoading={journeyLoading}
              journeyError={journeyError}
              isPro={isPro}
              onUpgrade={onOpenAccount}
              onRegenerate={generateJourney}
              onBack={() => setStep('intro')}
            />
          )}

          {!loading && step === 'tipsGate' && (
            <TipsGatePanel
              onYes={() => setStep('tipsRitual')}
              onNo={() => { setError(''); setStep('capture'); }}
              onBack={() => setStep('intro')}
            />
          )}

          {!loading && step === 'tipsRitual' && (
            <TipsRitualPanel
              hbTipsSkipped={hbTipsSkipped}
              savingTipsPref={savingTipsPref}
              onToggleSkip={saveTipsSkippedPreference}
              onReady={() => { setError(''); setStep('capture'); }}
              onBack={() => setStep('tipsGate')}
            />
          )}

          {!loading && step === 'capture' && (
            <CapturePanel
              recording={recording}
              elapsed={elapsed}
              level={level}
              error={error}
              hasEigenmode={!!eigenmode}
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

          {!loading && step === 'scoreReveal' && (
            <div className="glass p-6 sm:p-10" data-testid="harmonic-blueprint-score-reveal">
              <ResonanceScoreReveal
                score={resonanceScore}
                hasBaseline={!!eigenmode}
                onContinue={() => setStep('review')}
              />
            </div>
          )}

          {!loading && step === 'review' && pendingDerived && (
            <ReviewFindingsPanel
              findings={pendingFindings}
              selectedKeys={selectedKeys}
              onToggle={toggleFinding}
              onBack={() => setStep('capture')}
              onConfirm={confirmFindings}
              saving={savingReview}
              error={error}
            />
          )}

          {!loading && step === 'eigenmodeSaved' && profile && (
            <EigenmodeCapturedPanel
              profile={profile}
              onContinue={() => setStep('results')}
            />
          )}

          {!loading && step === 'results' && profile && (
            <ResultsPanel
              profile={profile}
              eigenmode={eigenmode}
              journey={journey}
              journeyLoading={journeyLoading}
              journeyError={journeyError}
              isPro={isPro}
              onGenerateJourney={generateJourney}
              onOpenAccount={onOpenAccount}
              onRecordAgain={resetToCapture}
              onReset={clearProfile}
              onPromoteBaseline={promoteCurrentAsEigenmode}
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
      {celebrationData && (
        <BeforeAfterCelebration
          data={celebrationData}
          onClose={() => setCelebrationData(null)}
        />
      )}
    </div>
  );
}

// ---------- Sub-panels -------------------------------------------------------

function IntroPanel({ existing, isPro, onBegin, onPreviewJourney, onUpgrade }) {
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

      {isPro ? (
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
      ) : (
        <div
          className="glass p-6 border border-[rgba(196,166,122,0.35)]"
          data-testid="harmonic-blueprint-free-intro-block"
        >
          <div className="label-tiny mb-3 text-[#C4A67A] inline-flex items-center gap-2">
            <Sparkles size={12} /> Pro-only voice capture
          </div>
          <div className="text-[#8A9A92] text-sm leading-relaxed">
            Voice capture &amp; personalised analysis are Pro features. Meanwhile,
            you can preview a two-track sample of the personalised
            <span className="text-[#E8E3D9]"> Eigenmode Journey </span>
            to hear how it feels.
          </div>
          <div className="flex flex-wrap items-center gap-3 mt-5">
            <button
              data-testid="harmonic-blueprint-preview-journey-button"
              type="button"
              onClick={onPreviewJourney}
              className="px-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors"
            >
              Preview a sample journey
            </button>
            <button
              data-testid="harmonic-blueprint-intro-upgrade-button"
              type="button"
              onClick={onUpgrade}
              className="px-5 py-2.5 rounded-full border border-[#C4A67A] text-[#C4A67A] hover:bg-[#C4A67A]/10 text-sm tracking-wide transition-colors"
            >
              Upgrade to Pro
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// -- Phase 9: HB Setup Tips gate + ritual --------------------------------------
function TipsGatePanel({ onYes, onNo, onBack }) {
  return (
    <div className="max-w-2xl mx-auto space-y-6" data-testid="hb-tips-gate">
      <div className="glass p-8 text-center">
        <div className="label-tiny mb-4 text-[#C4A67A]">One quick moment</div>
        <h2 className="text-2xl text-[#E8E3D9] leading-relaxed mb-3" style={{ fontFamily: 'Cormorant Garamond, serif' }}>
          Before we begin, would you like a few tips to help you get the most accurate reading?
        </h2>
        <p className="text-[#8A9A92] text-sm leading-relaxed mb-8 max-w-md mx-auto">
          A gentle ritual for arriving present. Takes about a minute to read.
        </p>
        <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
          <button
            type="button"
            data-testid="hb-tips-gate-yes"
            onClick={onYes}
            className="px-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors w-full sm:w-auto"
          >
            Yes, share the tips
          </button>
          <button
            type="button"
            data-testid="hb-tips-gate-no"
            onClick={onNo}
            className="px-6 py-3 rounded-full border border-[#5C9E8C]/40 hover:border-[#72C2AC]/70 text-[#C9DED6] hover:bg-black/25 transition-colors w-full sm:w-auto"
          >
            No, I&rsquo;m ready
          </button>
        </div>
      </div>
      <div className="text-center">
        <button
          type="button"
          onClick={onBack}
          className="text-[11px] uppercase tracking-[0.18em] text-[#5A6B65] hover:text-[#C4A67A] transition-colors"
        >
          ← Back
        </button>
      </div>
    </div>
  );
}

const HB_TIPS = [
  {
    title: 'Choose Your Moment',
    body: 'The ideal time to record your Eigenmode baseline is first thing in the morning, around 15 to 30 minutes after waking. Your voice carries its most natural, uninfluenced frequency signature before the day has had a chance to shape it.',
  },
  {
    title: 'Hydrate Gently',
    body: 'Drink half a glass of room temperature water before you begin. This helps your vocal tract resonate more naturally and gives your system a gentle reset.',
  },
  {
    title: 'Clear the Vocal Tract',
    body: 'Take a moment to gently clear your throat to wake up the diaphragm and vocal folds. Nothing forced — just a soft, natural clearing to prepare your voice.',
  },
  {
    title: 'Settle Into Stillness',
    body: 'Sit quietly for 1 to 2 minutes before recording. This is a perfect moment to use the Breathwork feature to stabilize your heart rate and arrive fully present before your baseline is captured.',
  },
];

function TipsRitualPanel({ hbTipsSkipped, savingTipsPref, onToggleSkip, onReady, onBack }) {
  return (
    <div className="max-w-3xl mx-auto space-y-6" data-testid="hb-tips-ritual">
      <div className="text-center mb-2">
        <div className="label-tiny mb-3 text-[#C4A67A]">A gentle pre-session ritual</div>
        <h2 className="text-3xl text-[#E8E3D9] leading-relaxed" style={{ fontFamily: 'Cormorant Garamond, serif' }}>
          Arriving Present
        </h2>
        <p className="text-[#8A9A92] text-sm leading-relaxed mt-2 max-w-lg mx-auto">
          Take your time with these. They&rsquo;re not steps to check off, just a soft invitation to
          arrive fully before your voice is captured.
        </p>
      </div>

      <div className="space-y-4">
        {HB_TIPS.map((tip, i) => (
          <div
            key={tip.title}
            className="glass p-6 border-l-2 border-[#C4A67A]/30 hover:border-[#C4A67A]/60 transition-colors"
            data-testid={`hb-tip-${i + 1}`}
          >
            <div className="flex items-baseline gap-3">
              <span className="label-tiny text-[#C4A67A]">Tip {i + 1}</span>
              <h3 className="text-lg text-[#E8E3D9]" style={{ fontFamily: 'Cormorant Garamond, serif' }}>
                {tip.title}
              </h3>
            </div>
            <p className="text-[#B5C4BC] text-[13.5px] leading-relaxed mt-2">
              {tip.body}
            </p>
          </div>
        ))}
      </div>

      <div className="glass p-5 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[13px] text-[#E8E3D9] font-medium">Skip tips next time</div>
          <div className="text-[11px] text-[#8A9A92] leading-relaxed mt-0.5">
            Go straight to recording on future analyses. You can turn this back on in the Assistant settings.
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={!!hbTipsSkipped}
          onClick={() => onToggleSkip(!hbTipsSkipped)}
          disabled={savingTipsPref}
          data-testid="hb-tips-skip-toggle"
          className={`shrink-0 relative inline-flex h-5 w-9 rounded-full border transition-colors ${
            hbTipsSkipped ? 'bg-[#5C9E8C]/50 border-[#72C2AC]' : 'bg-black/40 border-[#5C9E8C]/25'
          } ${savingTipsPref ? 'opacity-60' : ''}`}
        >
          <span
            aria-hidden="true"
            className={`inline-block h-3.5 w-3.5 my-[2px] rounded-full bg-[#E8E3D9] shadow transform transition-transform ${
              hbTipsSkipped ? 'translate-x-[18px]' : 'translate-x-[2px]'
            }`}
          />
        </button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
        <button
          type="button"
          onClick={onBack}
          className="text-[11px] uppercase tracking-[0.18em] text-[#5A6B65] hover:text-[#C4A67A] transition-colors"
        >
          ← Back
        </button>
        <button
          type="button"
          data-testid="hb-tips-ready"
          onClick={onReady}
          className="px-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors"
        >
          I&rsquo;m Ready to Begin
        </button>
      </div>
    </div>
  );
}


function FreePreviewPanel({ journey, journeyLoading, journeyError, isPro, onUpgrade, onRegenerate, onBack }) {
  return (
    <div className="space-y-6" data-testid="harmonic-blueprint-free-preview">
      <button
        type="button"
        onClick={onBack}
        className="text-[#8A9A92] hover:text-[#E8E3D9] text-sm transition-colors"
      >
        ← Back
      </button>
      {journeyLoading && !journey && (
        <div className="glass p-8 text-center">
          <Waves className="mx-auto text-[#72C2AC] animate-pulse" size={28} />
          <div className="text-[#8A9A92] text-sm mt-4">Composing your sample journey…</div>
        </div>
      )}
      {journeyError && (
        <div className="glass p-5 text-[#D96C6C] text-sm">{journeyError}</div>
      )}
      {journey && (
        <HarmonicJourneyPlayer
          journey={journey}
          isPro={isPro}
          onUpgrade={onUpgrade}
          onRegenerate={onRegenerate}
        />
      )}
    </div>
  );
}

function CapturePanel({ recording, elapsed, level, error, hasEigenmode, onStart, onStop, onFile, onBack }) {
  const secondsLeft = Math.max(0, MAX_SECONDS - elapsed);
  const readyToStop = elapsed >= MIN_SECONDS;
  return (
    <div className="space-y-6" data-testid="harmonic-blueprint-capture">
      {hasEigenmode && (
        <div
          className="glass p-5 border border-[rgba(196,166,122,0.3)]"
          data-testid="harmonic-blueprint-eigenmode-note"
        >
          <div className="label-tiny text-[#C4A67A] inline-flex items-center gap-2">
            <Anchor size={12} /> Comparing against your baseline
          </div>
          <div className="text-[#8A9A92] text-sm mt-2 leading-relaxed">
            This capture will be compared against your saved eigenmode profile
            to surface areas inviting rebalancing. You'll review the findings
            before anything is saved.
          </div>
        </div>
      )}
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

function ResultsPanel({ profile, eigenmode, journey, journeyLoading, journeyError, isPro, onGenerateJourney, onOpenAccount, onRecordAgain, onReset, onPromoteBaseline }) {
  const isEigenmode = !!(profile && profile.is_eigenmode);
  const confirmedGaps = (profile && profile.confirmed_gaps) || [];
  return (
    <div className="space-y-6" data-testid="harmonic-blueprint-results">
      {/* Baseline banner — the anchor / signal that Phase 2 is active. */}
      <div
        className={`glass p-5 flex items-center justify-between gap-4 ${isEigenmode ? 'border border-[rgba(196,166,122,0.35)]' : ''}`}
        data-testid="harmonic-blueprint-baseline-banner"
      >
        <div className="flex items-center gap-3">
          <Anchor size={16} className={isEigenmode ? 'text-[#C4A67A]' : 'text-[#72C2AC]'} />
          <div>
            <div className="label-tiny text-[#C4A67A]">
              {isEigenmode ? 'This is your natural baseline' : 'Compared against your natural baseline'}
            </div>
            <div className="text-[#8A9A92] text-xs mt-1">
              {isEigenmode
                ? 'Future captures will be compared against this eigenmode profile.'
                : eigenmode
                  ? 'Findings below reflect drift from your first-ever capture.'
                  : 'No baseline set — capture one to unlock drift analysis.'}
            </div>
          </div>
        </div>
        {!isEigenmode && eigenmode && (
          <button
            data-testid="harmonic-blueprint-promote-baseline-button"
            type="button"
            onClick={onPromoteBaseline}
            className="text-xs tracking-widest text-[#C4A67A] hover:text-[#D6B98A] transition-colors uppercase"
          >
            Set as new baseline
          </button>
        )}
      </div>

      {confirmedGaps.length > 0 && (
        <div
          className="glass p-6"
          data-testid="harmonic-blueprint-confirmed-gaps"
        >
          <div className="label-tiny text-[#C4A67A] mb-3 inline-flex items-center gap-2">
            <Sparkles size={12} /> Areas you're inviting rebalancing
          </div>
          <ul className="space-y-3">
            {confirmedGaps.map((g, i) => (
              <li key={`${g.key}-${i}`} className="flex items-start gap-3">
                <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-[#72C2AC] shrink-0" />
                <div>
                  <div className="text-[#E8E3D9] text-sm">{g.label} · {g.lo}–{g.hi} Hz</div>
                  <div className="text-[#8A9A92] text-sm mt-1 leading-relaxed">{g.description}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Phase 3: Your Eigenmode Journey — personalised playlist generator +
          player. If a journey already exists we render the player straight
          away with a Regenerate affordance; otherwise a compact Generate CTA. */}
      {journey ? (
        <HarmonicJourneyPlayer
          journey={journey}
          isPro={isPro}
          onUpgrade={onOpenAccount}
          onRegenerate={onGenerateJourney}
        />
      ) : (
        <div className="glass p-6" data-testid="harmonic-journey-cta">
          <div className="label-tiny text-[#C4A67A] mb-2 inline-flex items-center gap-2">
            <Sparkles size={12} /> Your Eigenmode Journey
          </div>
          <div className="text-[#C6CDCA] text-sm leading-relaxed">
            Generate a personalised playlist drawing from Solfeggio presets,
            Sound Baths and Flow Mode journeys — each track chosen to guide
            you back toward your natural baseline.
          </div>
          {journeyError && (
            <div
              data-testid="harmonic-journey-error"
              className="text-[#D96C6C] text-xs mt-3"
            >
              {journeyError}
            </div>
          )}
          <button
            data-testid="harmonic-journey-generate-button"
            type="button"
            onClick={onGenerateJourney}
            disabled={journeyLoading}
            className="mt-4 px-5 py-2.5 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] text-sm font-medium tracking-wide transition-colors disabled:opacity-50 inline-flex items-center gap-2"
          >
            <Sparkles size={14} />
            {journeyLoading ? 'Composing…' : 'Generate journey'}
          </button>
        </div>
      )}

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
          Delete all data
        </button>
      </div>
    </div>
  );
}

function ReviewFindingsPanel({ findings, selectedKeys, onToggle, onBack, onConfirm, saving, error }) {
  const nothingDrifted = findings.length === 0;
  return (
    <div className="space-y-6" data-testid="harmonic-blueprint-review">
      <div className="glass p-6 leading-relaxed">
        <div className="text-[#C6CDCA] text-base">
          We compared your current signature to your{' '}
          <span className="text-[#C4A67A]">natural baseline</span>. Review the
          findings below and confirm which ones feel relevant to you right
          now. Only what you affirm will be saved with this session.
        </div>
        <div className="text-[#8A9A92] text-sm mt-3">
          These are supportive observations about drift — never conclusions.
        </div>
      </div>

      {nothingDrifted ? (
        <div
          className="glass p-8 text-center"
          data-testid="harmonic-blueprint-review-empty"
        >
          <Anchor className="mx-auto text-[#72C2AC] mb-3" size={28} />
          <div className="font-display text-xl text-[#E8E3D9]">
            You're closely aligned with your natural baseline.
          </div>
          <div className="text-[#8A9A92] text-sm mt-3 leading-relaxed max-w-md mx-auto">
            No band has drifted by more than a few decibels from your
            eigenmode profile. Save this session to log the check-in.
          </div>
        </div>
      ) : (
        <ul
          className="space-y-3"
          data-testid="harmonic-blueprint-findings-list"
        >
          {findings.map((f) => {
            const selected = selectedKeys.has(f.key);
            return (
              <li key={f.key}>
                <button
                  data-testid={`harmonic-blueprint-finding-${f.key}`}
                  type="button"
                  onClick={() => onToggle(f.key)}
                  aria-pressed={selected}
                  className={`w-full text-left glass p-5 transition-colors border ${
                    selected
                      ? 'border-[rgba(114,194,172,0.6)] bg-[rgba(114,194,172,0.05)]'
                      : 'border-[rgba(114,194,172,0.15)]'
                  }`}
                >
                  <div className="flex items-start gap-4">
                    <div
                      className={`mt-1 w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                        selected
                          ? 'border-[#72C2AC] bg-[#72C2AC]/20'
                          : 'border-[#3A4A45]'
                      }`}
                    >
                      {selected && <Check size={11} className="text-[#72C2AC]" />}
                    </div>
                    <div className="flex-1">
                      <div className="flex items-baseline justify-between gap-3 flex-wrap">
                        <div className="text-[#E8E3D9] text-sm">
                          {f.label}
                          <span className="text-[#8A9A92] font-mono ml-2">
                            {f.lo}–{f.hi} Hz
                          </span>
                        </div>
                        <div className="text-[#5A6B65] text-xs font-mono">
                          {f.delta_db > 0 ? '+' : ''}{f.delta_db.toFixed(1)} dB
                        </div>
                      </div>
                      <div className="text-[#8A9A92] text-sm mt-2 leading-relaxed">
                        {f.description}
                      </div>
                    </div>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {error && (
        <div className="glass p-4 border border-[rgba(217,108,108,0.4)] text-[#D96C6C] text-sm">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
        <button
          data-testid="harmonic-blueprint-review-back-button"
          type="button"
          onClick={onBack}
          className="text-[#8A9A92] hover:text-[#E8E3D9] text-sm transition-colors"
        >
          ← Discard &amp; retake
        </button>
        <button
          data-testid="harmonic-blueprint-review-save-button"
          type="button"
          onClick={onConfirm}
          disabled={saving}
          className="px-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors disabled:opacity-50 inline-flex items-center gap-2"
        >
          {saving ? 'Saving…' : nothingDrifted ? 'Save session' : `Save findings (${selectedKeys.size})`}
        </button>
      </div>
    </div>
  );
}

function EigenmodeCapturedPanel({ profile, onContinue }) {
  return (
    <div className="space-y-6" data-testid="harmonic-blueprint-eigenmode-saved">
      <div className="glass p-8 leading-relaxed border border-[rgba(196,166,122,0.35)]">
        <div className="inline-flex items-center gap-2 label-tiny text-[#C4A67A]">
          <Anchor size={12} /> Eigenmode profile saved
        </div>
        <div className="font-display text-2xl text-[#E8E3D9] mt-4">
          This is your natural baseline.
        </div>
        <div className="text-[#C6CDCA] text-base mt-3">
          We've saved this capture as your <span className="text-[#C4A67A]">eigenmode profile</span> —
          the unique harmonic signature we'll compare against on every future
          session. From now on, each new capture will surface any drift from
          this natural tuning.
        </div>
        <div className="text-[#8A9A92] text-sm mt-3 leading-relaxed">
          You can designate a future analysis as your new baseline at any
          time from the results view.
        </div>
      </div>
      <button
        data-testid="harmonic-blueprint-eigenmode-continue-button"
        type="button"
        onClick={onContinue}
        className="px-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors"
      >
        View my resonance profile
      </button>
    </div>
  );
}

function SpectrumMap({ profile }) {
  // Log-scale SVG spectrum plot. Y axis: 0 (peak) → -60 dB. X axis: log10(Hz)
  // spread from 60 Hz to 4 kHz — perceptually accurate for audio and prevents
  // the low-band labels (100/250/500 Hz + most vocal dominants) from crowding
  // into a narrow strip on the left.
  const w = 720, h = 260, padL = 28, padR = 28, padT = 44, padB = 52;
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
  const loHz = Math.max(pts[0].hz, 20);
  const hiHz = Math.max(pts[pts.length - 1].hz, loHz * 2);
  const logLo = Math.log10(loHz);
  const logHi = Math.log10(hiHz);
  const usableW = w - padL - padR;
  const usableH = h - padT - padB;
  const hzToX = (hz) => {
    const clamped = Math.max(loHz, Math.min(hiHz, hz));
    return padL + ((Math.log10(clamped) - logLo) / (logHi - logLo)) * usableW;
  };
  const dbToY = (db) =>
    padT + (1 - (Math.max(minDb, Math.min(maxDb, db)) - minDb) / (maxDb - minDb)) * usableH;
  const xy = pts.map((p) => ({ x: hzToX(p.hz), y: dbToY(p.db), hz: p.hz }));
  const line = xy.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const areaBottom = padT + usableH;
  const area = `M${xy[0].x.toFixed(1)},${areaBottom} ${xy.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')} L${xy[xy.length - 1].x.toFixed(1)},${areaBottom} Z`;

  // Collision-aware label placement. Given a list of {x, text}, assign each
  // one a row index so labels that would overlap horizontally are pushed to
  // a second (or third) row instead. Uses ~52px estimated label width.
  const staggerRows = (items, minGap = 52) => {
    const sorted = [...items].sort((a, b) => a.x - b.x);
    const rowLastX = [];
    const out = new Map();
    for (const it of sorted) {
      let row = 0;
      while (row < rowLastX.length && it.x - rowLastX[row] < minGap) row++;
      rowLastX[row] = it.x;
      out.set(it.key, row);
    }
    return out;
  };
  const domItems = (profile.dominant || []).map((p, i) => ({
    key: `d${i}`, x: hzToX(p.hz), hz: p.hz,
  }));
  const dipItems = (profile.dips || []).map((p, i) => ({
    key: `p${i}`, x: hzToX(p.hz), hz: p.hz,
  }));
  const domRows = staggerRows(domItems);
  const dipRows = staggerRows(dipItems);
  // Fixed log-friendly x-axis ticks. All fall inside 60-4000 Hz.
  const xTicks = [100, 250, 500, 1000, 2000, 4000].filter(
    (hz) => hz >= loHz && hz <= hiHz,
  );
  const tickRows = staggerRows(
    xTicks.map((hz, i) => ({ key: `t${i}`, x: hzToX(hz), hz })),
    44,
  );
  const extraDomRows = Math.max(0, (domRows.size ? Math.max(...domRows.values()) : 0));
  const extraDipRows = Math.max(0, (dipRows.size ? Math.max(...dipRows.values()) : 0));
  const extraTickRows = Math.max(0, (tickRows.size ? Math.max(...tickRows.values()) : 0));
  const svgH = h + (extraDipRows + extraTickRows) * 14 + extraDomRows * 4;
  return (
    <div className="glass p-6" data-testid="harmonic-blueprint-spectrum">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="label-tiny text-[#C4A67A]">Spectrum map</div>
          <div className="text-[#8A9A92] text-sm mt-1">60 Hz — 4 kHz · averaged over {profile.duration}s</div>
        </div>
        <div className="text-[#5A6B65] text-xs tracking-widest uppercase">FFT · {profile.fft_size}</div>
      </div>
      <svg viewBox={`0 0 ${w} ${svgH}`} className="w-full h-auto">
        <defs>
          <linearGradient id="hb-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#72C2AC" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#72C2AC" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* Horizontal dB grid */}
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <line
            key={t}
            x1={padL} x2={w - padR}
            y1={padT + t * usableH} y2={padT + t * usableH}
            stroke="rgba(114,194,172,0.08)"
          />
        ))}
        <path d={area} fill="url(#hb-fill)" />
        <path d={line} fill="none" stroke="#72C2AC" strokeWidth="1.5" />
        {/* Dominant markers — vertical guide + label staggered above the chart.
            NOTE: text content passed via the `children` prop as a pre-composed
            string so the dev-tool AST transform doesn't wrap the numeric
            expression in a <span> (which SVG can't render). */}
        {domItems.map((it, i) => {
          const row = domRows.get(it.key) || 0;
          const labelY = padT - 10 - row * 14;
          return (
            <g key={`dom-${i}`}>
              <line
                x1={it.x} x2={it.x} y1={padT} y2={padT + usableH}
                stroke="rgba(114,194,172,0.35)" strokeDasharray="2 4"
              />
              <text
                x={it.x} y={labelY}
                textAnchor="middle" fontSize="11"
                fill="#72C2AC" fontFamily="ui-monospace, monospace"
                fontWeight="500"
                children={`${Math.round(it.hz)} Hz`}
              />
            </g>
          );
        })}
        {/* Dip markers — vertical guide + label staggered below the chart. */}
        {dipItems.map((it, i) => {
          const row = dipRows.get(it.key) || 0;
          const labelY = padT + usableH + 16 + row * 14;
          return (
            <g key={`dip-${i}`}>
              <line
                x1={it.x} x2={it.x} y1={padT} y2={padT + usableH}
                stroke="rgba(196,166,122,0.4)" strokeDasharray="1 3"
              />
              <text
                x={it.x} y={labelY}
                textAnchor="middle" fontSize="11"
                fill="#C4A67A" fontFamily="ui-monospace, monospace"
                fontWeight="500"
                children={`${Math.round(it.hz)} Hz`}
              />
            </g>
          );
        })}
        {/* Fixed log-scaled x-axis ticks. Stagger onto a second row if two
            adjacent ticks would otherwise collide horizontally. */}
        {xTicks.map((hz, i) => {
          const key = `t${i}`;
          const row = tickRows.get(key) || 0;
          const dipRowCount = extraDipRows + 1;
          const labelY = padT + usableH + 16 + dipRowCount * 14 + 8 + row * 14;
          const tickText = hz >= 1000
            ? `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 1)}k Hz`
            : `${hz} Hz`;
          return (
            <text
              key={`tick-${hz}`}
              x={hzToX(hz)} y={labelY}
              textAnchor="middle" fontSize="10"
              fill="#8A9A92" fontFamily="ui-monospace, monospace"
              children={tickText}
            />
          );
        })}
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
