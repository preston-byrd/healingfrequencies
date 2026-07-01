import React, { useEffect, useState, useCallback } from 'react';
import { Play, Pause, ExternalLink, AlertTriangle } from 'lucide-react';
import audioEngine from '@/lib/audioEngine';
import haptic from '@/lib/hapticEngine';
import { resolvePreset } from '@/lib/voicePresets';

/**
 * PlayDeepLink — fullscreen "voice shortcut" landing for /play.
 *
 * URL params:
 *   ?preset=sleep|calm|focus|meditation|anxiety|grounding   (or any alias)
 *   ?frequency=174           (Hz; overrides preset)
 *   ?soundscape=rain         (ALLOWED_AMBIENT key; overrides preset)
 *   ?volume=0.4              (0..1; soundscape level)
 *   ?binaural=7.83           (Hz; >0 starts binaural beat)
 *   ?isochronic=10           (Hz; >0 starts isochronic tone)
 *   ?pattern=heartbeat       (auto|heartbeat|breath478|frequency)
 *   ?duration_min=60         (5|10|15|20|30|45|60|90|120|240|480)
 *   ?waveform=sine           (sine|square|triangle|sawtooth)
 *
 * Designed to be triggered by:
 *   • iOS Shortcuts → "Open URL" action with one of the URLs above
 *   • Google Assistant Routines → custom action with URL
 *
 * Autoplay handling:
 *   • Most modern Chromium-based Android browsers honour the navigation
 *     gesture from Assistant and start audio without further interaction.
 *   • iOS Safari is stricter — we always show a single giant "tap to start"
 *     button that resumes the AudioContext on first user gesture.
 */
export default function PlayDeepLink({ onOpenApp }) {
  const params = new URLSearchParams(window.location.search);
  const presetSlug = params.get('preset');
  const preset = resolvePreset(presetSlug);

  // Build the effective config: explicit URL params override preset values.
  const numParam = (k) => {
    const v = params.get(k);
    if (v === null || v === '') return null;
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  };
  const cfg = {
    frequency: numParam('frequency') ?? preset?.config?.frequency ?? null,
    waveform: params.get('waveform') || preset?.config?.waveform || 'sine',
    binaural: numParam('binaural') ?? preset?.config?.binaural ?? 0,
    isochronic: numParam('isochronic') ?? preset?.config?.isochronic ?? 0,
    soundscape: params.get('soundscape') || preset?.config?.soundscape || null,
    soundscape_volume: numParam('volume') ?? preset?.config?.soundscape_volume ?? 0.45,
    pattern: params.get('pattern') || preset?.config?.pattern || null,
    duration_min: numParam('duration_min') ?? preset?.config?.duration_min ?? null,
  };

  const [playing, setPlaying] = useState(false);
  const [needsGesture, setNeedsGesture] = useState(false);
  const [error, setError] = useState('');

  // Apply session config to the engines exactly once.
  const applyConfig = useCallback(() => {
    try {
      // Reset everything so nothing leaks from a previous session.
      audioEngine.setBinaural(0);
      audioEngine.setIsochronic(0);
      ['rain', 'ocean', 'forest', 'wind', 'crickets', 'bowls', 'brown', 'white']
        .forEach((k) => audioEngine.setAmbient(k, 0));

      if (cfg.frequency && cfg.frequency > 0) {
        audioEngine.setFrequency(cfg.frequency);
        audioEngine.setWaveform(cfg.waveform || 'sine');
      }
      if (cfg.binaural && cfg.binaural > 0) audioEngine.setBinaural(cfg.binaural);
      if (cfg.isochronic && cfg.isochronic > 0) audioEngine.setIsochronic(cfg.isochronic);
      if (cfg.soundscape) audioEngine.setAmbient(cfg.soundscape, cfg.soundscape_volume);
      if (cfg.pattern) {
        haptic.setEnabled(true);
        haptic.setPattern(cfg.pattern);
      }
    } catch (e) {
      console.warn('[PlayDeepLink] applyConfig failed', e);
    }
  }, []);

  const startPlayback = useCallback(async () => {
    setError('');
    try {
      if (!audioEngine.playing) {
        await audioEngine.start();
      }
      // Verify the AudioContext actually started — iOS sometimes silently
      // refuses without a gesture.
      const ctxState = audioEngine.ctx && audioEngine.ctx.state;
      if (ctxState && ctxState !== 'running') {
        setNeedsGesture(true);
        return;
      }
      setNeedsGesture(false);
      setPlaying(true);
    } catch (e) {
      console.warn('[PlayDeepLink] start failed', e);
      setNeedsGesture(true);
    }
  }, []);

  const stopPlayback = useCallback(() => {
    try { audioEngine.stop(); } catch (e) { /* graceful */ }
    setPlaying(false);
  }, []);

  // Auto-attempt on mount. If the browser blocks (iOS), the "tap to start"
  // button takes over.
  useEffect(() => {
    if (!preset && !cfg.frequency && !cfg.binaural && !cfg.isochronic && !cfg.soundscape) {
      setError('No preset specified — add ?preset=sleep or ?frequency=… to the URL.');
      return;
    }
    haptic.attachToAudio();
    applyConfig();
    startPlayback();
    return () => {
      // Don't auto-stop on unmount — the user may navigate to the dashboard
      // and want playback to continue. The dashboard subscribes to the same
      // audioEngine state and will reflect this session.
    };
  }, [applyConfig, startPlayback, preset, cfg.frequency, cfg.binaural, cfg.isochronic, cfg.soundscape]);

  // Hand off to Sleep Mode AFTER the gesture / autoplay starts, so the timer
  // is correctly armed against a running session.
  useEffect(() => {
    if (!playing) return;
    if (!cfg.duration_min) return;
    if ([30, 60, 120, 240, 480].includes(cfg.duration_min)) {
      // Fire the same event the Wellness Assistant uses to trigger Sleep Mode.
      window.dispatchEvent(new CustomEvent('sf:agent:sleep', { detail: { duration_min: cfg.duration_min } }));
    }
  }, [playing, cfg.duration_min]);

  const titleLabel = preset
    ? preset.label
    : (cfg.frequency ? `${cfg.frequency} Hz` : 'Custom session');

  return (
    <div data-testid="play-deeplink" className="fixed inset-0 bg-[#0A1612] flex flex-col items-center justify-center text-center px-6">
      <div className="aurora-bg" />
      <div className="relative max-w-md w-full flex flex-col items-center gap-8">
        {/* Breathing orb — gives the user a calm visual focus while haptics pulse */}
        <div className="relative w-48 h-48">
          <div
            className="absolute inset-0 rounded-full"
            style={{
              background: 'radial-gradient(circle, rgba(196,166,122,0.35) 0%, rgba(92,158,140,0.18) 45%, rgba(0,0,0,0) 75%)',
              animation: 'breathe 6s ease-in-out infinite',
            }}
          />
          <div
            className="absolute inset-6 rounded-full"
            style={{
              background: 'radial-gradient(circle, rgba(114,194,172,0.45) 0%, rgba(92,158,140,0.20) 45%, rgba(0,0,0,0) 80%)',
              animation: 'breathe 6s ease-in-out infinite reverse',
            }}
          />
          <div className="absolute inset-0 flex items-center justify-center">
            {playing ? (
              <Pause size={28} className="text-[#C4A67A]" />
            ) : (
              <Play size={28} className="text-[#C4A67A]" />
            )}
          </div>
        </div>

        {/* Status text */}
        <div>
          <div className="label-tiny text-[#C4A67A] mb-2" data-testid="deeplink-status">
            {playing ? 'Now playing' : (needsGesture ? 'Ready' : 'Tuning in…')}
          </div>
          <div className="font-display text-3xl text-[#E8E3D9] tracking-wide" data-testid="deeplink-title">
            {titleLabel}
          </div>
          {preset && (
            <div className="text-sm text-[#8A9A92] mt-2 leading-relaxed" data-testid="deeplink-description">
              {preset.description}
            </div>
          )}
        </div>

        {/* Gesture / control */}
        {needsGesture && !playing && (
          <button
            data-testid="deeplink-start"
            onClick={startPlayback}
            className="px-8 py-4 rounded-full bg-[#C4A67A] text-[#08120F] text-sm font-medium tracking-wider hover:bg-[#d6b88c] transition-colors"
          >
            Tap to begin
          </button>
        )}
        {playing && (
          <button
            data-testid="deeplink-pause"
            onClick={stopPlayback}
            className="px-6 py-3 rounded-full border border-[#5C9E8C]/30 text-[#8A9A92] text-sm hover:text-[#E8E3D9] hover:border-[#72C2AC]/40 transition-colors"
          >
            Pause
          </button>
        )}

        {error && (
          <div className="flex items-start gap-2 text-[#E07A5F] text-sm" data-testid="deeplink-error">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Footer — open the full app. Keeps audio playing because the
            engine is a singleton subscribed to by Dashboard. */}
        <button
          data-testid="deeplink-open-app"
          onClick={() => onOpenApp && onOpenApp()}
          className="mt-4 inline-flex items-center gap-2 text-[12px] text-[#8A9A92] hover:text-[#72C2AC] transition-colors"
        >
          <ExternalLink size={12} /> Open full Solarisound app
        </button>
      </div>
    </div>
  );
}
