import { useEffect, useState, useCallback } from 'react';

/**
 * usePWAInstall — small hook that exposes the three things a UI needs to
 * surface a "Install app" CTA cleanly:
 *
 *   • `canPrompt`     — true on Android/desktop Chromium browsers AFTER the
 *                       `beforeinstallprompt` event has fired. Tap "install"
 *                       and the browser shows the native install sheet.
 *   • `isIOS`         — true on iOS Safari (where the install API does NOT
 *                       exist — you have to show instructions for the
 *                       Share-sheet → "Add to Home Screen" route).
 *   • `isInstalled`   — true when the app is already running in standalone
 *                       (i.e. launched from the home screen). UI uses this
 *                       to hide the CTA entirely.
 *
 *   • `promptInstall()` — call when the user clicks the install button.
 *                          Returns the user's choice ('accepted' | 'dismissed').
 */
export default function usePWAInstall() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [isInstalled, setIsInstalled] = useState(false);

  // Detect "running as installed PWA" — both the modern matchMedia hook and
  // the iOS-only navigator.standalone fallback.
  useEffect(() => {
    const check = () => {
      const standalone =
        (typeof window !== 'undefined' &&
          window.matchMedia &&
          window.matchMedia('(display-mode: standalone)').matches) ||
        (typeof navigator !== 'undefined' && navigator.standalone === true);
      setIsInstalled(!!standalone);
    };
    check();
    const mq = window.matchMedia ? window.matchMedia('(display-mode: standalone)') : null;
    if (mq && mq.addEventListener) {
      mq.addEventListener('change', check);
      return () => mq.removeEventListener('change', check);
    }
    return undefined;
  }, []);

  // Capture the deferred install prompt the moment the browser offers it.
  useEffect(() => {
    const onBeforeInstall = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
    };
    const onInstalled = () => {
      setDeferredPrompt(null);
      setIsInstalled(true);
    };
    window.addEventListener('beforeinstallprompt', onBeforeInstall);
    window.addEventListener('appinstalled', onInstalled);
    return () => {
      window.removeEventListener('beforeinstallprompt', onBeforeInstall);
      window.removeEventListener('appinstalled', onInstalled);
    };
  }, []);

  // iOS detection — Safari does not fire beforeinstallprompt, so the UI must
  // route iOS users to the manual instructions modal. We rely on user-agent
  // sniffing because there is no feature-detect equivalent for the share
  // sheet. iPad-on-desktop-mode is also covered (iPadOS reports as MacIntel
  // with touch points > 0).
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : '';
  const platform = typeof navigator !== 'undefined' ? navigator.platform || '' : '';
  const isIOS =
    /iPad|iPhone|iPod/.test(ua) ||
    (platform === 'MacIntel' && typeof navigator !== 'undefined' && (navigator.maxTouchPoints || 0) > 1);

  const promptInstall = useCallback(async () => {
    if (!deferredPrompt) return 'unavailable';
    try {
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      setDeferredPrompt(null);
      return choice && choice.outcome ? choice.outcome : 'dismissed';
    } catch (e) {
      console.warn('[usePWAInstall] prompt failed', e);
      return 'error';
    }
  }, [deferredPrompt]);

  return {
    canPrompt: !!deferredPrompt && !isInstalled,
    isIOS: isIOS && !isInstalled,
    isInstalled,
    promptInstall,
  };
}
