// Small helper that owns the browser Push subscription lifecycle.
// Keeps Notification API + Service Worker + our backend in sync.
import api from '@/lib/api';

const urlBase64ToUint8Array = (b64) => {
  const padding = '='.repeat((4 - (b64.length % 4)) % 4);
  const base64 = (b64 + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = window.atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
};

export const pushSupported = () =>
  typeof window !== 'undefined' &&
  'serviceWorker' in navigator &&
  'PushManager' in window &&
  'Notification' in window;

export const currentPermission = () => {
  if (typeof Notification === 'undefined') return 'default';
  return Notification.permission || 'default';
};

export async function requestPushPermission() {
  if (!pushSupported()) return { granted: false, reason: 'unsupported' };
  const perm = await Notification.requestPermission();
  return { granted: perm === 'granted', reason: perm };
}

export async function subscribeToPush() {
  if (!pushSupported()) return { ok: false, reason: 'unsupported' };
  const perm = currentPermission();
  if (perm !== 'granted') {
    const p = await requestPushPermission();
    if (!p.granted) return { ok: false, reason: p.reason || 'denied' };
  }
  const reg = await navigator.serviceWorker.ready;
  const { data } = await api.get('/notifications/vapid-public-key');
  const publicKey = data?.public_key;
  if (!publicKey) return { ok: false, reason: 'no_vapid_key' };
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    try {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    } catch (e) {
      return { ok: false, reason: e?.name || 'subscribe_failed' };
    }
  }
  try {
    await api.post('/me/notifications/push/subscribe', {
      subscription: sub.toJSON(),
      user_agent: navigator.userAgent?.slice(0, 240) || null,
    });
  } catch (e) {
    return { ok: false, reason: 'server_register_failed' };
  }
  return { ok: true, subscription: sub };
}

export async function unsubscribeFromPush() {
  if (!pushSupported()) return { ok: false, reason: 'unsupported' };
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      const endpoint = sub.endpoint;
      await sub.unsubscribe();
      try { await api.delete(`/me/notifications/push/subscribe?endpoint=${encodeURIComponent(endpoint)}`); } catch (_) {}
    } else {
      try { await api.delete('/me/notifications/push/subscribe'); } catch (_) {}
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: e?.name || 'unsubscribe_failed' };
  }
}
