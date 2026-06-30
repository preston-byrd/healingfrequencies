/**
 * detectHeadphones — best-effort headphone detection for the onboarding
 * transition flow's copy variation.
 *
 * Web platforms do NOT expose a reliable "headphones plugged in" signal.
 * `navigator.mediaDevices.enumerateDevices()` lists audio outputs but:
 *   • Device labels are empty without prior microphone permission.
 *   • Bluetooth headphones may not show up as a separate output device.
 *   • Some browsers report only the default output.
 *
 * So this is a heuristic. We return true when ANY of the following are true:
 *   1. More than one audiooutput device exists (suggests both speaker AND
 *      headphones connected — usually means the user has wired/bluetooth
 *      audio plugged in alongside the built-in speaker).
 *   2. Any device label matches headphone-ish keywords.
 *
 * Returns false on unsupported browsers or any thrown error — caller
 * should default to the "Pop in your headphones" copy variant in that case.
 */
const HEADPHONE_LABEL_REGEX = /headphone|airpod|earpod|earbud|head[\s-]*set|bluetooth|bt[\s-]*audio|beats|sony[\s-]*wh|jbl[\s-]*tune|powerbeats/i;

export default async function detectHeadphones() {
  try {
    if (
      typeof navigator === 'undefined' ||
      !navigator.mediaDevices ||
      typeof navigator.mediaDevices.enumerateDevices !== 'function'
    ) {
      return false;
    }
    const devices = await navigator.mediaDevices.enumerateDevices();
    const outputs = devices.filter((d) => d.kind === 'audiooutput');
    if (outputs.length === 0) return false;
    // Heuristic #1 — more than one output usually means headphones connected
    // alongside the built-in speaker.
    if (outputs.length > 1) return true;
    // Heuristic #2 — label matching (only works post-mic-permission, but it's
    // a free check when available).
    return outputs.some((d) => HEADPHONE_LABEL_REGEX.test(d.label || ''));
  } catch (e) {
    console.warn('[detectHeadphones] enumerateDevices failed', e);
    return false;
  }
}
