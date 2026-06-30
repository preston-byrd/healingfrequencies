import React, { useEffect, useState } from 'react';
import { X, HeartPulse, Activity, Wind, Waves, Power, AlertTriangle } from 'lucide-react';
import haptic from '@/lib/hapticEngine';

/**
 * HapticsModal — settings + quick-test surface for the Pulsing Haptics
 * feature. The actual scheduling lives in `lib/hapticEngine`; this is just
 * the React shell that lets a user toggle on/off, pick a pattern, and run
 * a one-shot test pulse to confirm device support.
 *
 * Supported devices (Android Chrome/Edge/Firefox) get the full experience.
 * Unsupported devices (iOS Safari + iOS PWA standalone) see a friendly
 * notice — the audio session is never blocked.
 */

const PATTERNS = [
  { key: 'auto',       label: 'Auto',           desc: 'Sync to the active session — heartbeat by default, switches to entrainment when a binaural / isochronic rate is set.', icon: Activity },
  { key: 'heartbeat',  label: 'Heartbeat',      desc: 'Calm 60 bpm lub-dub. Slows to 50 bpm in Sleep Mode.',                                                                  icon: HeartPulse },
  { key: 'breath478',  label: '4-7-8 Breath',   desc: 'Long inhale pulse · silent hold · taper of taps on the exhale. Cycles every 19 s.',                                       icon: Wind },
  { key: 'frequency',  label: 'Frequency Pulse', desc: 'Pulses on the active binaural / isochronic beat rate. Brain-wave entrainment by touch.',                                 icon: Waves },
];

export default function HapticsModal({ open, onClose }) {
  const [snap, setSnap] = useState(() => haptic.snapshot());

  useEffect(() => {
    const off = haptic.on((s) => setSnap(s));
    return () => off();
  }, []);

  if (!open) return null;
  const { supported, enabled, pattern, running } = snap;

  return (
    <div
      data-testid="haptics-modal"
      className="fixed inset-0 z-[65] flex items-end sm:items-center justify-center bg-black/65 backdrop-blur-sm p-0 sm:p-4"
      role="dialog"
      aria-modal="true"
      onClick={(e) => { if (e.target === e.currentTarget) onClose && onClose(); }}
    >
      <div className="w-full sm:max-w-md bg-[#0E1F18] border border-[#5C9E8C]/25 rounded-t-3xl sm:rounded-2xl shadow-2xl overflow-hidden flex flex-col" style={{ maxHeight: '85vh' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#5C9E8C]/15">
          <div className="flex items-center gap-2">
            <HeartPulse size={14} className="text-[#C4A67A]" />
            <div className="label-tiny text-[#C4A67A]">Pulsing Haptics</div>
          </div>
          <button
            data-testid="haptics-modal-close"
            onClick={onClose}
            className="text-[#8A9A92] hover:text-[#E8E3D9] p-1"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 overflow-y-auto custom-scrollbar space-y-5">
          {!supported && (
            <div data-testid="haptics-unsupported" className="flex items-start gap-3 p-3 rounded-xl border border-[#C4A67A]/30 bg-[#C4A67A]/5">
              <AlertTriangle size={16} className="text-[#C4A67A] shrink-0 mt-0.5" />
              <div className="text-sm text-[#E8E3D9]/90 leading-relaxed">
                <div className="text-[#C4A67A] font-medium">Not available on this device</div>
                <div className="text-[12px] text-[#8A9A92] mt-1">
                  iOS Safari and iOS standalone PWAs don&apos;t expose the vibration motor to web apps. Try Solarisound on an Android device for the full haptic experience — audio still works normally here.
                </div>
              </div>
            </div>
          )}

          {/* Master toggle */}
          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-[#5C9E8C]/20 bg-black/30">
            <div className="flex-1 min-w-0">
              <div className="text-sm text-[#E8E3D9]">Pulsing Haptics</div>
              <div className="text-[11px] text-[#8A9A92] mt-1 leading-relaxed">
                Gentle vibration synced with your session so you can close your eyes and feel the pacing.
              </div>
            </div>
            <button
              data-testid="haptics-toggle"
              role="switch"
              aria-checked={enabled}
              disabled={!supported}
              onClick={() => haptic.setEnabled(!enabled)}
              className={`relative w-11 h-6 rounded-full transition-colors flex-shrink-0 ${
                !supported
                  ? 'bg-[#5C9E8C]/10 cursor-not-allowed'
                  : enabled
                  ? 'bg-[#72C2AC]/60'
                  : 'bg-[#5C9E8C]/20'
              }`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-[#0E1F18] border border-[#5C9E8C]/40 transition-transform ${
                  enabled ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>

          {/* Pattern picker */}
          <div data-testid="haptics-pattern-picker" className="space-y-2">
            <div className="label-tiny text-[#8A9A92]">Pattern</div>
            {PATTERNS.map(({ key, label, desc, icon: Icon }) => {
              const selected = pattern === key;
              return (
                <button
                  key={key}
                  data-testid={`haptics-pattern-${key}`}
                  disabled={!supported}
                  onClick={() => haptic.setPattern(key)}
                  className={`w-full text-left p-3 rounded-xl border transition-colors flex items-start gap-3 ${
                    !supported
                      ? 'border-[#5C9E8C]/10 bg-black/20 cursor-not-allowed opacity-60'
                      : selected
                      ? 'border-[#72C2AC]/60 bg-[#5C9E8C]/10'
                      : 'border-[#5C9E8C]/20 bg-black/30 hover:border-[#72C2AC]/40'
                  }`}
                >
                  <Icon
                    size={16}
                    className={`shrink-0 mt-0.5 ${selected ? 'text-[#72C2AC]' : 'text-[#8A9A92]'}`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className={`text-sm ${selected ? 'text-[#72C2AC]' : 'text-[#E8E3D9]'}`}>{label}</div>
                    <div className="text-[11px] text-[#8A9A92] mt-1 leading-relaxed">{desc}</div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Test / Stop row */}
          <div className="flex items-center gap-2 pt-1">
            <button
              data-testid="haptics-test"
              disabled={!supported}
              onClick={() => haptic.test()}
              className={`flex-1 inline-flex items-center justify-center gap-2 px-3 py-2.5 rounded-full text-sm font-medium tracking-wider transition-colors ${
                !supported
                  ? 'bg-[#5C9E8C]/10 text-[#5A6B65] cursor-not-allowed'
                  : 'bg-[#5C9E8C]/15 text-[#72C2AC] hover:bg-[#5C9E8C]/25'
              }`}
            >
              <Activity size={14} /> Test pulse
            </button>
            {running && (
              <button
                data-testid="haptics-stop"
                onClick={() => haptic.stop()}
                className="inline-flex items-center justify-center gap-2 px-3 py-2.5 rounded-full text-sm text-[#C4A67A] hover:text-[#E8B872] transition-colors bg-[#C4A67A]/10 hover:bg-[#C4A67A]/15"
              >
                <Power size={14} /> Stop
              </button>
            )}
          </div>

          {/* Footnotes */}
          <ul className="text-[11px] text-[#8A9A92] space-y-1.5 pt-2 border-t border-[#5C9E8C]/15">
            <li>· Auto-starts when you play a session and stops when you pause / sleep timer ends.</li>
            <li>· Respects your phone&apos;s silent mode, Do Not Disturb, and battery-saver settings.</li>
            <li>· Tapers down with Smart Fade in the last 5 minutes of any session.</li>
            <li>· Optional — audio works exactly the same with this turned off.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
