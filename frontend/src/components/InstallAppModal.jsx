import React, { useState } from 'react';
import { X, Download, Share, Plus, Smartphone, CheckCircle2 } from 'lucide-react';

/**
 * InstallAppModal — small modal that explains how to install Solarisound to
 * the home screen on each platform. Receives `canPrompt` + `promptInstall`
 * from the usePWAInstall hook so it can fire the native browser sheet on
 * Android/desktop Chromium, and renders manual share-sheet instructions for
 * iOS Safari (which has no install API).
 */
export default function InstallAppModal({ open, onClose, canPrompt, isIOS, promptInstall }) {
  const [installedJustNow, setInstalledJustNow] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const handleNativeInstall = async () => {
    setBusy(true);
    const outcome = await promptInstall();
    setBusy(false);
    if (outcome === 'accepted') {
      setInstalledJustNow(true);
      // Auto-close after a moment so the user sees the success state.
      setTimeout(() => { onClose && onClose(); }, 1600);
    }
  };

  return (
    <div
      data-testid="install-modal"
      className="fixed inset-0 z-[65] flex items-end sm:items-center justify-center bg-black/65 backdrop-blur-sm p-0 sm:p-4"
      role="dialog"
      aria-modal="true"
      onClick={(e) => { if (e.target === e.currentTarget) onClose && onClose(); }}
    >
      <div className="w-full sm:max-w-md bg-[#0E1F18] border border-[#5C9E8C]/25 rounded-t-3xl sm:rounded-2xl shadow-2xl overflow-hidden flex flex-col" style={{ maxHeight: '85vh' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#5C9E8C]/15">
          <div className="flex items-center gap-2">
            <Smartphone size={14} className="text-[#C4A67A]" />
            <div className="label-tiny text-[#C4A67A]">Install Solarisound</div>
          </div>
          <button
            data-testid="install-modal-close"
            onClick={onClose}
            className="text-[#8A9A92] hover:text-[#E8E3D9] p-1"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 overflow-y-auto custom-scrollbar space-y-5">
          {installedJustNow && (
            <div data-testid="install-success" className="flex items-start gap-3 p-3 rounded-xl border border-[#72C2AC]/30 bg-[#5C9E8C]/10">
              <CheckCircle2 size={18} className="text-[#72C2AC] shrink-0 mt-0.5" />
              <div className="text-sm text-[#E8E3D9]">
                <div>Installed. Look for the Solarisound icon on your home screen.</div>
                <div className="text-[11px] text-[#8A9A92] mt-1">Lock-screen audio + offline access are already wired in.</div>
              </div>
            </div>
          )}

          {/* Native-prompt path — Android Chrome / desktop Chromium */}
          {canPrompt && !installedJustNow && (
            <div data-testid="install-native">
              <p className="text-sm text-[#E8E3D9]/90 leading-relaxed">
                One tap to install Solarisound. The app icon will appear on your home screen and run full-screen with offline support.
              </p>
              <button
                data-testid="install-native-button"
                onClick={handleNativeInstall}
                disabled={busy}
                className={`mt-4 w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-full text-sm font-medium tracking-wider transition-colors ${
                  busy
                    ? 'bg-[#5C9E8C]/10 text-[#5A6B65] cursor-not-allowed'
                    : 'bg-[#C4A67A] text-[#08120F] hover:bg-[#d6b88c]'
                }`}
              >
                <Download size={16} />
                {busy ? 'Installing…' : 'Install app'}
              </button>
            </div>
          )}

          {/* iOS Safari fallback — no install API exists; show step-by-step */}
          {isIOS && !installedJustNow && (
            <div data-testid="install-ios">
              <p className="text-sm text-[#E8E3D9]/90 leading-relaxed">
                iOS doesn&apos;t expose a one-tap install, but Safari can add Solarisound to your home screen in two taps.
              </p>
              <ol className="mt-4 space-y-3">
                <li className="flex items-start gap-3 p-3 rounded-xl border border-[#5C9E8C]/20 bg-black/30">
                  <span className="shrink-0 w-6 h-6 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-xs font-mono flex items-center justify-center">1</span>
                  <div className="text-sm text-[#E8E3D9]/90 flex-1">
                    Tap the <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#5C9E8C]/15 text-[#72C2AC] font-mono text-[11px]"><Share size={11} /> Share</span> button at the bottom of Safari.
                  </div>
                </li>
                <li className="flex items-start gap-3 p-3 rounded-xl border border-[#5C9E8C]/20 bg-black/30">
                  <span className="shrink-0 w-6 h-6 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-xs font-mono flex items-center justify-center">2</span>
                  <div className="text-sm text-[#E8E3D9]/90 flex-1">
                    Scroll and tap <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#5C9E8C]/15 text-[#72C2AC] font-mono text-[11px]"><Plus size={11} /> Add to Home Screen</span>.
                  </div>
                </li>
                <li className="flex items-start gap-3 p-3 rounded-xl border border-[#5C9E8C]/20 bg-black/30">
                  <span className="shrink-0 w-6 h-6 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-xs font-mono flex items-center justify-center">3</span>
                  <div className="text-sm text-[#E8E3D9]/90 flex-1">
                    Tap <span className="text-[#C4A67A] font-medium">Add</span> in the top-right. Solarisound will appear on your home screen.
                  </div>
                </li>
              </ol>
              <p className="mt-3 text-[11px] text-[#8A9A92]">
                Tip: open in Safari (not Chrome on iOS) for the install option to appear.
              </p>
            </div>
          )}

          {/* Desktop / browsers without either path */}
          {!canPrompt && !isIOS && !installedJustNow && (
            <div data-testid="install-fallback" className="text-sm text-[#E8E3D9]/90 leading-relaxed space-y-3">
              <p>
                Look for the install icon in your browser&apos;s address bar (usually on the right side of the URL field) to add Solarisound as a desktop app.
              </p>
              <p className="text-[12px] text-[#8A9A92]">
                Chrome and Edge: address bar → install icon, or Settings menu → &ldquo;Install Solarisound&rdquo;.
                <br />
                Brave: address bar → &ldquo;Install&rdquo; button on the right.
                <br />
                Firefox: doesn&apos;t currently support desktop PWA installs — bookmark this page for quick access instead.
              </p>
            </div>
          )}

          {/* Benefits — always shown to nudge the install */}
          {!installedJustNow && (
            <ul className="text-[12px] text-[#8A9A92] space-y-1.5 pt-2 border-t border-[#5C9E8C]/15">
              <li>· Launches full-screen, like a native app — no browser chrome.</li>
              <li>· Lock-screen play/pause controls + background audio.</li>
              <li>· Works offline once first opened.</li>
              <li>· Daily-reminder notifications survive a phone reboot.</li>
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
