import React, { useEffect, useState } from 'react';
import { Sparkles, X, Ear, ChevronRight } from 'lucide-react';

/**
 * OnboardingTransitionCard — slide-up bottom card that triggers about
 * 30 seconds after the user accepts a suggestion from the Wellness Assistant.
 *
 * It composes the two messages from the onboarding strategy:
 *   • Guidance line ("Let's slow things down…") with conditional copy
 *     based on whether headphones were detected.
 *   • Calibration pivot ("To unlock the full therapeutic power…") with
 *     primary [Start 30-Sec Calibration] + secondary [Skip for Now].
 *
 * Auto-fades in with a translate-y. Dismisses on Start (opens the
 * calibration modal) or Skip (records the dismissal so the card doesn't
 * keep popping every minute, but the user can still tap the Ear icon).
 */
export default function OnboardingTransitionCard({
  open,
  headphonesDetected,
  alreadyCalibrated,
  onStart,
  onSkip,
}) {
  // Local mounted/animated state — we keep the DOM around for ~300 ms after
  // `open` flips false so the fade-out transition runs cleanly.
  const [mounted, setMounted] = useState(false);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (open) {
      setMounted(true);
      // Next frame → flip visible so CSS transition kicks in.
      requestAnimationFrame(() => setVisible(true));
    } else if (mounted) {
      setVisible(false);
      const t = setTimeout(() => setMounted(false), 320);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [open, mounted]);

  if (!mounted) return null;

  // Copy variations.
  const guidance = headphonesDetected
    ? "Let's slow things down and let this baseline tone calm your nervous system."
    : "Let's slow things down. Pop in your headphones, and let this baseline tone calm your nervous system.";

  const pivotTitle = alreadyCalibrated
    ? 'Want to fine-tune your hearing profile?'
    : 'Unlock the full therapeutic power';

  const pivotBody = alreadyCalibrated
    ? "You're already calibrated, but you can fine-tune anytime — useful if you've switched headphones or your hearing feels different."
    : "To unlock the full therapeutic power of these sound waves, let's calibrate the audio perfectly to your unique hearing.";

  const startLabel = alreadyCalibrated ? 'Recalibrate (30 s)' : 'Start 30-Sec Calibration';

  return (
    <div
      data-testid="onboarding-transition-card"
      className="fixed left-0 right-0 bottom-0 z-[60] flex justify-center px-3 sm:px-4 pb-16 sm:pb-6 pointer-events-none"
      role="dialog"
      aria-modal="false"
    >
      <div
        className={`pointer-events-auto w-full max-w-md bg-[#0E1F18]/95 backdrop-blur-md border border-[#5C9E8C]/30 rounded-2xl shadow-2xl overflow-hidden transition-all duration-300 ease-out`}
        style={{
          transform: visible ? 'translateY(0)' : 'translateY(110%)',
          opacity: visible ? 1 : 0,
        }}
      >
        {/* Companion-voice guidance (italic, subdued) */}
        <div className="px-5 pt-4 pb-3 border-b border-[#5C9E8C]/15 flex items-start gap-3">
          <Sparkles size={14} className="text-[#C4A67A] shrink-0 mt-0.5" />
          <p
            data-testid="otc-guidance"
            className="text-sm text-[#E8E3D9]/85 italic leading-relaxed flex-1"
          >
            {guidance}
          </p>
          <button
            data-testid="otc-close"
            onClick={onSkip}
            className="text-[#8A9A92] hover:text-[#E8E3D9] p-0.5 -mt-0.5"
            aria-label="Dismiss"
          >
            <X size={14} />
          </button>
        </div>

        {/* Calibration pivot card */}
        <div className="px-5 py-4 space-y-3">
          <div className="flex items-center gap-2">
            <Ear size={14} className="text-[#72C2AC]" />
            <div className="label-tiny text-[#C4A67A]">{pivotTitle}</div>
          </div>
          <p data-testid="otc-pivot-body" className="text-sm text-[#E8E3D9] leading-relaxed">
            {pivotBody}
          </p>
          <div className="flex items-center gap-2 pt-1">
            <button
              data-testid="otc-start"
              onClick={onStart}
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-3 rounded-full bg-[#C4A67A] text-[#08120F] text-sm font-medium tracking-wider hover:bg-[#d6b88c] transition-colors"
            >
              {startLabel} <ChevronRight size={14} />
            </button>
            <button
              data-testid="otc-skip"
              onClick={onSkip}
              className="px-4 py-3 rounded-full text-sm text-[#8A9A92] hover:text-[#E8E3D9] transition-colors"
            >
              {alreadyCalibrated ? 'Not now' : 'Skip for now'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
