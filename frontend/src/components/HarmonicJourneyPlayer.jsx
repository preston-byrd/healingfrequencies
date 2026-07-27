import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Play, Pause, SkipForward, SkipBack, Square, Sparkles, Lock, Clock, Waves } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import { getSoundBath } from '@/lib/soundBathEngine';

/**
 * Phase 3 — Harmonic Blueprint · Your Eigenmode Journey player.
 *
 * Renders the generated playlist (from POST /api/harmonic-blueprint/journey/generate)
 * with per-track rationale copy, sequential playback via the existing
 * audioEngine + soundBath engine, an integrated Smart Fade Timer and Sleep
 * Mode toggle, and a free-tier preview state with an upgrade prompt.
 *
 * Props:
 *   journey     — server-generated journey object (see backend).
 *   isPro       — bool. Only affects the upgrade CTA / preview badge.
 *   onUpgrade   — () => void. Routes to the paywall for free users.
 *   onRegenerate — () => void. Requests a new journey.
 *
 * The component owns its playback state locally; audioEngine + soundBath
 * are global singletons so playback survives step changes within the sheet,
 * but stops when the parent sheet unmounts (matches Dashboard behaviour).
 */
export default function HarmonicJourneyPlayer({ journey, isPro, onUpgrade, onRegenerate }) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [smartFade, setSmartFade] = useState(true);
  const [sleepMode, setSleepMode] = useState(false);
  const tickRef = useRef(null);
  const fadeArmedRef = useRef(false);

  const tracks = (journey && journey.tracks) || [];
  const current = tracks[index] || null;
  const durationS = current ? current.duration_seconds : 0;
  const isLastTrack = index === tracks.length - 1;

  const stopEverything = useCallback(() => {
    try { getSoundBath(audioEngine).stop(); } catch (_) { /* graceful */ }
    try { audioEngine.stop(); } catch (_) { /* graceful */ }
  }, []);

  // Kick off a specific track. Chosen engine depends on the track type.
  const startTrack = useCallback(async (track) => {
    if (!track) return;
    stopEverything();
    // Small delay so the previous engine has time to release its nodes;
    // otherwise the sound-bath oscillator tail can bleed into the next track.
    await new Promise((r) => setTimeout(r, 60));
    try {
      if (track.type === 'soundbath' && track.ref) {
        await getSoundBath(audioEngine).start(track.ref);
        return;
      }
      // Flow-mode tracks are rendered as their fundamental frequency for
      // Phase 3 MVP; the full 3-stage crossfade lives inside the Flow panel.
      audioEngine.setFrequency(track.freq);
      await audioEngine.start();
    } catch (e) {
      // audioEngine already logs; surface nothing to the user beyond the
      // paused UI state (play button will simply not toggle to 'playing').
      setPlaying(false);
    }
  }, [stopEverything]);

  // Handle play / pause on the CURRENT track without reshuffling the queue.
  const togglePlay = useCallback(async () => {
    if (!current) return;
    if (playing) {
      stopEverything();
      setPlaying(false);
      return;
    }
    await startTrack(current);
    setPlaying(true);
  }, [current, playing, startTrack, stopEverything]);

  const goTo = useCallback(async (nextIdx) => {
    const clamped = Math.max(0, Math.min(tracks.length - 1, nextIdx));
    setIndex(clamped);
    setElapsed(0);
    fadeArmedRef.current = false;
    // If we were playing, seamlessly switch to the new track. Otherwise just
    // preview it in the UI without starting playback.
    if (playing) {
      await startTrack(tracks[clamped]);
    }
  }, [playing, startTrack, tracks]);

  const stopAll = useCallback(() => {
    stopEverything();
    setPlaying(false);
    setElapsed(0);
    fadeArmedRef.current = false;
  }, [stopEverything]);

  // Cleanup on unmount / journey change — halt audio and reset UI state.
  useEffect(() => () => stopEverything(), [stopEverything]);
  useEffect(() => {
    setIndex(0); setElapsed(0); setPlaying(false);
    fadeArmedRef.current = false;
    stopEverything();
  }, [journey && journey.id, stopEverything]);

  // Per-second tick that drives the progress bar + auto-advance + smart-fade.
  useEffect(() => {
    if (!playing) {
      if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
      return;
    }
    tickRef.current = setInterval(() => {
      setElapsed((prev) => {
        const next = prev + 1;
        // Smart Fade: on the last track's final 60s, trigger a graceful
        // fade-out (Sleep Mode uses the same primitive with a longer window).
        if (isLastTrack && !fadeArmedRef.current) {
          const fadeWindow = sleepMode ? 120 : (smartFade ? 60 : 0);
          if (fadeWindow > 0 && next >= durationS - fadeWindow) {
            try { audioEngine.fadeOutAll(fadeWindow); } catch (_) {}
            fadeArmedRef.current = true;
          }
        }
        if (next >= durationS) {
          // Auto-advance or stop at end of playlist.
          if (isLastTrack) {
            setTimeout(() => { stopAll(); }, 200);
          } else {
            setTimeout(() => { goTo(index + 1); }, 200);
          }
          return durationS;
        }
        return next;
      });
    }, 1000);
    return () => {
      if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
    };
  }, [playing, durationS, isLastTrack, sleepMode, smartFade, goTo, index, stopAll]);

  if (!journey || !tracks.length) return null;

  const totalMin = Math.round(journey.total_duration_seconds / 60);
  const isPreview = journey.tier === 'free';
  const progressPct = durationS > 0 ? Math.min(100, (elapsed / durationS) * 100) : 0;

  return (
    <div className="glass p-6" data-testid="harmonic-journey-player">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <div className="label-tiny text-[#C4A67A] inline-flex items-center gap-2">
            <Sparkles size={12} /> {journey.name}
          </div>
          <div className="text-[#E8E3D9] mt-2 text-sm">
            {isPreview
              ? `Preview · ${tracks.length} of ${journey.full_track_count} tracks · ${totalMin} min`
              : `${tracks.length} tracks · ${totalMin} min total`}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isPreview && (
            <span
              data-testid="harmonic-journey-preview-badge"
              className="text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-0.5 rounded-full inline-flex items-center gap-1"
            >
              <Lock size={9} /> PREVIEW
            </span>
          )}
          <button
            data-testid="harmonic-journey-regenerate-button"
            type="button"
            onClick={onRegenerate}
            className="text-[#8A9A92] hover:text-[#72C2AC] text-xs tracking-widest uppercase transition-colors"
          >
            Regenerate
          </button>
        </div>
      </div>

      {/* Track list */}
      <ul className="space-y-2 mb-6" data-testid="harmonic-journey-tracks">
        {tracks.map((t, i) => {
          const active = i === index;
          return (
            <li key={t.id}>
              <button
                data-testid={`harmonic-journey-track-${t.id}`}
                type="button"
                onClick={() => goTo(i)}
                className={`w-full text-left rounded-xl p-4 border transition-colors ${
                  active
                    ? 'border-[rgba(114,194,172,0.55)] bg-[rgba(114,194,172,0.06)]'
                    : 'border-[rgba(114,194,172,0.12)] hover:border-[rgba(114,194,172,0.3)]'
                }`}
              >
                <div className="flex items-start gap-3">
                  <div className="text-[#5A6B65] font-mono text-xs pt-1 w-6 shrink-0">
                    {String(i + 1).padStart(2, '0')}
                  </div>
                  <div className="flex-1">
                    <div className="flex items-baseline justify-between gap-3 flex-wrap">
                      <div className="text-[#E8E3D9] text-sm">
                        {t.name}
                      </div>
                      <div className="text-[#5A6B65] text-xs font-mono inline-flex items-center gap-2">
                        <Clock size={10} /> {Math.round(t.duration_seconds / 60)}m
                      </div>
                    </div>
                    <div className="text-[#72C2AC] text-xs mt-1 tracking-wide">
                      {t.tagline}
                    </div>
                    <div className="text-[#8A9A92] text-xs mt-2 leading-relaxed">
                      {t.rationale}
                    </div>
                  </div>
                  {active && playing && (
                    <Waves size={14} className="text-[#72C2AC] animate-pulse mt-1" />
                  )}
                </div>
              </button>
            </li>
          );
        })}
      </ul>

      {/* Free-tier upgrade prompt */}
      {isPreview && (
        <div
          className="mb-6 p-4 rounded-xl border border-[rgba(196,166,122,0.35)] bg-[rgba(196,166,122,0.05)]"
          data-testid="harmonic-journey-upgrade-prompt"
        >
          <div className="label-tiny text-[#C4A67A] inline-flex items-center gap-2">
            <Lock size={10} /> Preview
          </div>
          <div className="text-[#E8E3D9] text-sm mt-2 leading-relaxed">
            {journey.upgrade_prompt || 'Unlock your full Eigenmode Journey with Pro.'}
          </div>
          {!isPro && (
            <button
              data-testid="harmonic-journey-upgrade-button"
              type="button"
              onClick={onUpgrade}
              className="mt-3 px-5 py-2 rounded-full bg-[#C4A67A] hover:bg-[#D6B98A] text-[#08120F] text-xs font-medium tracking-wider transition-colors"
            >
              Upgrade to Pro
            </button>
          )}
        </div>
      )}

      {/* Progress + controls */}
      <div className="space-y-4">
        <div>
          <div className="flex items-center justify-between text-[#8A9A92] text-xs mb-1.5 font-mono">
            <span>{formatMMSS(elapsed)}</span>
            <span>{formatMMSS(durationS)}</span>
          </div>
          <div
            className="h-1 bg-[rgba(114,194,172,0.12)] rounded-full overflow-hidden"
            data-testid="harmonic-journey-progress"
          >
            <div
              className="h-full bg-gradient-to-r from-[#5C9E8C] to-[#72C2AC] transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>

        <div className="flex items-center justify-center gap-4">
          <button
            data-testid="harmonic-journey-prev-button"
            type="button"
            onClick={() => goTo(index - 1)}
            disabled={index === 0}
            className="p-2 text-[#C6CDCA] hover:text-[#E8E3D9] disabled:opacity-30 transition-colors"
            aria-label="Previous track"
          >
            <SkipBack size={20} />
          </button>
          <button
            data-testid="harmonic-journey-play-button"
            type="button"
            onClick={togglePlay}
            className="w-14 h-14 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] transition-colors flex items-center justify-center"
            aria-label={playing ? 'Pause' : 'Play'}
          >
            {playing ? <Pause size={22} /> : <Play size={22} className="ml-1" />}
          </button>
          <button
            data-testid="harmonic-journey-next-button"
            type="button"
            onClick={() => goTo(index + 1)}
            disabled={index >= tracks.length - 1}
            className="p-2 text-[#C6CDCA] hover:text-[#E8E3D9] disabled:opacity-30 transition-colors"
            aria-label="Next track"
          >
            <SkipForward size={20} />
          </button>
          <button
            data-testid="harmonic-journey-stop-button"
            type="button"
            onClick={stopAll}
            className="p-2 text-[#8A9A92] hover:text-[#D96C6C] transition-colors ml-2"
            aria-label="Stop playback"
          >
            <Square size={16} />
          </button>
        </div>

        {/* Smart Fade + Sleep Mode toggles — apply on the final track. */}
        <div className="flex flex-wrap items-center justify-center gap-4 pt-2 text-xs">
          <label
            data-testid="harmonic-journey-smart-fade-toggle"
            className="inline-flex items-center gap-2 text-[#8A9A92] hover:text-[#E8E3D9] cursor-pointer transition-colors"
          >
            <input
              type="checkbox"
              checked={smartFade}
              onChange={(e) => setSmartFade(e.target.checked)}
              className="accent-[#72C2AC]"
            />
            Smart Fade (60s taper at end)
          </label>
          <label
            data-testid="harmonic-journey-sleep-mode-toggle"
            className="inline-flex items-center gap-2 text-[#8A9A92] hover:text-[#E8E3D9] cursor-pointer transition-colors"
          >
            <input
              type="checkbox"
              checked={sleepMode}
              onChange={(e) => setSleepMode(e.target.checked)}
              className="accent-[#C4A67A]"
            />
            Sleep Mode (2 min gentle fade)
          </label>
        </div>
      </div>
    </div>
  );
}

function formatMMSS(secs) {
  if (!Number.isFinite(secs) || secs < 0) return '00:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
