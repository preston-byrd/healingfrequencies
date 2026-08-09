import React, { useEffect, useRef, useState } from 'react';
import { Share2, Download, X, Loader2, Check } from 'lucide-react';

/**
 * SessionShareCard — soft, optional "share your session" overlay shown after
 * the Wellness Assistant post-session check-in. Generates a 1080×1350 (4:5)
 * PNG entirely client-side via a hidden <canvas>, so no server round-trip is
 * needed and there's no privacy concern about session metadata leaving the
 * device.
 *
 * The card is styled to match Solarisound's dark / gold / teal aesthetic and
 * embeds a cymatics-inspired concentric geometric pattern derived from the
 * primary frequency so every share is subtly unique.
 *
 * Sharing strategy:
 *   • navigator.canShare({ files:[…] }) → native Web Share API (Instagram,
 *     TikTok, WhatsApp, Messages, etc.) on mobile Safari / Chrome / Edge.
 *   • Fallback → download the PNG so desktop / Firefox users still get the
 *     asset and can upload manually.
 *
 * Props:
 *   open           bool
 *   onClose        () => void            single-tap dismissal
 *   isPro          bool                  Pro users get the journey line;
 *                                        Free users get a "Try Pro" watermark
 *                                        linking to the upgrade page
 *   onUpgrade      () => void            free-tier "Try Pro" tap handler
 *   session {
 *     frequencyHz    number              e.g. 528
 *     frequencyName  string              e.g. "Miracle"
 *     frequencyDesc  string|null         e.g. "DNA Repair"
 *     durationMin    number              actual completed minutes
 *     visualLabel    string              e.g. "Chladni · Cymatics"
 *     journeyLabel   string|null         e.g. "Deep Restore Journey" (Pro only)
 *   }
 */

const QUOTES = [
  'Tuned to my natural frequency',
  'Returning to resonance',
  'Sound is the medicine of the future',
  'In harmony with 528Hz today',
  'My nervous system thanks me',
  'Back to my natural tuning',
];

// Palette pulled from index.css so the canvas render stays in lock-step with
// the rest of the app if we ever retune the theme.
const COLORS = {
  bgTop:      '#0B1814',
  bgBottom:   '#050B09',
  surface:    '#101F1A',
  primary:    '#5C9E8C',
  primaryHi:  '#72C2AC',
  accent:     '#C4A67A',
  accentHi:   '#E8B872',
  text:       '#E8E3D9',
  muted:      '#8A9A92',
};

function pickQuote(hz) {
  // Prefer the 528Hz-specific line when the user's primary frequency was
  // actually 528, otherwise rotate deterministically per-session-open so the
  // same card doesn't jitter between quotes on preview repaints.
  if (Math.round(hz) === 528) {
    return QUOTES[3];
  }
  const others = QUOTES.filter((_, i) => i !== 3);
  return others[Math.floor(Math.random() * others.length)];
}

/**
 * Draw the shareable card into a canvas at the given DPR-aware pixel size.
 * Returns a promise that resolves once all draw ops complete (canvas is sync
 * but wrapping keeps the API future-proof for image loading if we ever add
 * a Solarisound logo bitmap).
 */
function drawCard(canvas, session, isPro, quote) {
  const W = 1080;
  const H = 1350;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');

  // 1. Vertical gradient background
  const bg = ctx.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, COLORS.bgTop);
  bg.addColorStop(1, COLORS.bgBottom);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  // 2. Cymatics-inspired concentric pattern — number of rings & spacing
  //    tied to the primary frequency so every card is visually unique.
  const cx = W / 2;
  const cy = H / 2 + 40;
  const hz = Math.max(1, session.frequencyHz || 1);
  const ringCount = 22;
  const spacing = 34;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 1; i <= ringCount; i++) {
    const r = i * spacing;
    // Modulate alpha with a cosine tied to hz so lower frequencies show
    // sparser wave crests, higher frequencies denser. Keeps it "resonant".
    const wave = Math.cos((i * hz) / 300) * 0.5 + 0.5;
    const alpha = 0.03 + wave * 0.06;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = i % 3 === 0
      ? `rgba(196, 166, 122, ${alpha})`
      : `rgba(114, 194, 172, ${alpha})`;
    ctx.lineWidth = i % 5 === 0 ? 1.6 : 1;
    ctx.stroke();
  }
  ctx.restore();

  // 3. Radial glow behind the frequency number
  const glow = ctx.createRadialGradient(cx, cy - 40, 20, cx, cy - 40, 380);
  glow.addColorStop(0, 'rgba(114, 194, 172, 0.28)');
  glow.addColorStop(0.55, 'rgba(114, 194, 172, 0.06)');
  glow.addColorStop(1, 'rgba(114, 194, 172, 0)');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, W, H);

  // 4. Header: logo mark + brand name
  //    Logo = a triple-ring glyph rendered vector-only so we don't need a
  //    remote SVG load (share card generation must be instant / offline).
  const logoX = 100;
  const logoY = 110;
  ctx.save();
  ctx.translate(logoX, logoY);
  ctx.strokeStyle = COLORS.accent;
  ctx.lineWidth = 2.2;
  ctx.beginPath(); ctx.arc(0, 0, 22, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = 'rgba(196, 166, 122, 0.55)';
  ctx.beginPath(); ctx.arc(0, 0, 14, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = 'rgba(114, 194, 172, 0.85)';
  ctx.beginPath(); ctx.arc(0, 0, 6, 0, Math.PI * 2); ctx.stroke();
  ctx.restore();

  ctx.fillStyle = COLORS.text;
  ctx.font = "500 30px 'Cormorant Garamond', 'Georgia', serif";
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'left';
  ctx.fillText('Solarisound', logoX + 42, logoY + 1);

  // "Try Pro" watermark for free users — top-right corner.
  if (!isPro) {
    ctx.save();
    ctx.fillStyle = 'rgba(196, 166, 122, 0.85)';
    ctx.font = "500 20px 'Outfit', system-ui, sans-serif";
    ctx.textAlign = 'right';
    ctx.fillText('Try Pro →', W - 100, logoY + 1);
    ctx.restore();
  }

  // 5. Primary frequency — HUGE.
  ctx.textAlign = 'center';
  ctx.fillStyle = COLORS.text;
  ctx.font = "300 260px 'Cormorant Garamond', 'Georgia', serif";
  const hzText = Number.isFinite(session.frequencyHz)
    ? (session.frequencyHz % 1 === 0
        ? String(Math.round(session.frequencyHz))
        : session.frequencyHz.toFixed(1))
    : '—';
  const hzMetric = ctx.measureText(hzText);
  const hzBaseY = cy - 40;
  ctx.fillText(hzText, cx - 44, hzBaseY);

  // Hz unit tucked to the top-right of the number, gold accent.
  ctx.fillStyle = COLORS.accent;
  ctx.font = "500 56px 'Cormorant Garamond', 'Georgia', serif";
  ctx.textAlign = 'left';
  ctx.fillText('Hz', cx + hzMetric.width / 2 - 30, hzBaseY - 60);

  // 6. Frequency name + descriptor line
  ctx.textAlign = 'center';
  ctx.fillStyle = COLORS.primaryHi;
  ctx.font = "500 44px 'Outfit', system-ui, sans-serif";
  const nameLine = session.frequencyDesc
    ? `${session.frequencyName} · ${session.frequencyDesc}`
    : (session.frequencyName || '');
  if (nameLine) ctx.fillText(nameLine, cx, hzBaseY + 130);

  // 7. Meta row: duration · visualizer
  ctx.fillStyle = COLORS.muted;
  ctx.font = "400 26px 'Outfit', system-ui, sans-serif";
  const durLine = `${session.durationMin} min session`;
  const visLine = session.visualLabel ? `${durLine}  •  ${session.visualLabel}` : durLine;
  ctx.fillText(visLine, cx, hzBaseY + 190);

  // 8. Pro-only: Flow Mode journey line (gold, italic-feel via Cormorant)
  if (isPro && session.journeyLabel) {
    ctx.fillStyle = COLORS.accent;
    ctx.font = "500 30px 'Cormorant Garamond', 'Georgia', serif";
    ctx.fillText(session.journeyLabel, cx, hzBaseY + 250);
  }

  // 9. Bottom band — inspirational quote + solarisound.com
  //    Thin gold divider first.
  ctx.save();
  ctx.strokeStyle = 'rgba(196, 166, 122, 0.35)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx - 80, H - 240);
  ctx.lineTo(cx + 80, H - 240);
  ctx.stroke();
  ctx.restore();

  ctx.fillStyle = COLORS.text;
  ctx.font = "400 italic 34px 'Cormorant Garamond', 'Georgia', serif";
  ctx.textAlign = 'center';
  ctx.fillText(`"${quote}"`, cx, H - 180);

  ctx.fillStyle = COLORS.muted;
  ctx.font = "500 22px 'Outfit', system-ui, sans-serif";
  ctx.fillText('solarisound.com', cx, H - 90);
}

async function canvasToBlob(canvas) {
  return new Promise((resolve) => {
    canvas.toBlob((b) => resolve(b), 'image/png', 0.95);
  });
}

export default function SessionShareCard({
  open,
  onClose,
  isPro,
  onUpgrade,
  session,
}) {
  const canvasRef = useRef(null);
  const [dataUrl, setDataUrl] = useState('');
  const [status, setStatus] = useState('idle');     // idle | sharing | shared | error
  const [errMsg, setErrMsg] = useState('');
  // Re-roll the quote every time the card is (re)opened.
  const quote = React.useMemo(
    () => (open ? pickQuote(session?.frequencyHz || 0) : ''),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open, session?.frequencyHz]
  );

  // Render the card into the hidden canvas as soon as the sheet opens.
  useEffect(() => {
    if (!open || !session) return;
    setStatus('idle');
    setErrMsg('');
    const cv = canvasRef.current;
    if (!cv) return;
    try {
      drawCard(cv, session, isPro, quote);
      setDataUrl(cv.toDataURL('image/png'));
    } catch (e) {
      console.warn('[SessionShareCard] draw failed', e);
      setErrMsg('Could not render your card. Try again in a moment.');
    }
  }, [open, session, isPro, quote]);

  if (!open) return null;

  const canNativeShare = (() => {
    try {
      if (typeof navigator === 'undefined') return false;
      if (typeof navigator.canShare !== 'function') return false;
      // We need canShare({files:[…]}) support specifically; probe with a
      // 1-byte placeholder file rather than the real blob so we can decide
      // BEFORE generating the (bigger) card if the button should even show.
      const probe = new File([new Uint8Array([0])], 'probe.png', { type: 'image/png' });
      return navigator.canShare({ files: [probe] });
    } catch (_) {
      return false;
    }
  })();

  const handleShare = async () => {
    const cv = canvasRef.current;
    if (!cv) return;
    setStatus('sharing');
    setErrMsg('');
    try {
      const blob = await canvasToBlob(cv);
      if (!blob) throw new Error('No blob');
      const file = new File([blob], `solarisound-${Math.round(session.frequencyHz || 0)}hz.png`, { type: 'image/png' });
      const payload = {
        files: [file],
        title: 'My Solarisound session',
        text: `${Math.round(session.frequencyHz || 0)} Hz · ${session.durationMin} min · solarisound.com`,
      };
      if (navigator.canShare && !navigator.canShare(payload)) {
        // File payload rejected mid-flight — fall back to download.
        triggerDownload(blob);
        setStatus('shared');
        return;
      }
      await navigator.share(payload);
      setStatus('shared');
    } catch (e) {
      // AbortError = user cancelled the sheet, not an actual failure.
      if (e && e.name === 'AbortError') {
        setStatus('idle');
        return;
      }
      console.warn('[SessionShareCard] share failed', e);
      setErrMsg('Sharing was blocked — try downloading instead.');
      setStatus('error');
    }
  };

  const triggerDownload = (blob) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `solarisound-${Math.round(session.frequencyHz || 0)}hz.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  };

  const handleDownload = async () => {
    const cv = canvasRef.current;
    if (!cv) return;
    setStatus('sharing');
    setErrMsg('');
    try {
      const blob = await canvasToBlob(cv);
      if (!blob) throw new Error('No blob');
      triggerDownload(blob);
      setStatus('shared');
    } catch (e) {
      console.warn('[SessionShareCard] download failed', e);
      setErrMsg('Could not download the card.');
      setStatus('error');
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      role="dialog"
      aria-label="Share your session"
      data-testid="session-share-card"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-md rounded-2xl border border-[#5C9E8C]/25 bg-[#0B1814] shadow-[0_20px_60px_rgba(0,0,0,0.5)] p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          data-testid="session-share-close"
          onClick={onClose}
          aria-label="Close"
          className="absolute top-3 right-3 text-[#8A9A92] hover:text-[#E8E3D9] transition-colors p-1"
        >
          <X size={16} />
        </button>

        <div className="text-center mb-4">
          <div className="text-[13px] uppercase tracking-[0.2em] text-[#C4A67A] font-mono mb-2">
            Session card
          </div>
          <div className="text-[15px] text-[#C9DED6]">
            Share your resonance with the world.
          </div>
        </div>

        {/* Preview — a scaled-down copy of the canvas render. We render to
            the actual canvas for correct 1080×1350 export, then show a CSS
            preview via the data URL so the modal stays lightweight. */}
        <div className="rounded-xl overflow-hidden border border-[#5C9E8C]/20 bg-black/40 aspect-[4/5] mb-4 flex items-center justify-center">
          {dataUrl ? (
            <img
              src={dataUrl}
              alt="Session share card preview"
              className="w-full h-full object-contain"
              data-testid="session-share-preview"
            />
          ) : (
            <Loader2 size={22} className="text-[#72C2AC] animate-spin" />
          )}
        </div>

        {/* Hidden full-res canvas used for the actual PNG export. */}
        <canvas ref={canvasRef} style={{ display: 'none' }} />

        {errMsg && (
          <div className="text-[11px] text-[#C4A67A]/90 italic mb-3 text-center" data-testid="session-share-error">
            {errMsg}
          </div>
        )}

        <div className="flex flex-col gap-2">
          {canNativeShare ? (
            <button
              data-testid="session-share-share-btn"
              onClick={handleShare}
              disabled={status === 'sharing'}
              className="w-full py-3 rounded-lg bg-[#5C9E8C]/25 hover:bg-[#5C9E8C]/40 disabled:opacity-50 border border-[#72C2AC]/50 hover:border-[#72C2AC] text-[#72C2AC] text-sm font-medium tracking-wide transition-colors inline-flex items-center justify-center gap-2"
            >
              {status === 'sharing' ? (
                <><Loader2 size={14} className="animate-spin" />Preparing…</>
              ) : status === 'shared' ? (
                <><Check size={14} />Shared</>
              ) : (
                <><Share2 size={14} />Share your session</>
              )}
            </button>
          ) : (
            <button
              data-testid="session-share-download-btn"
              onClick={handleDownload}
              disabled={status === 'sharing'}
              className="w-full py-3 rounded-lg bg-[#5C9E8C]/25 hover:bg-[#5C9E8C]/40 disabled:opacity-50 border border-[#72C2AC]/50 hover:border-[#72C2AC] text-[#72C2AC] text-sm font-medium tracking-wide transition-colors inline-flex items-center justify-center gap-2"
            >
              {status === 'sharing' ? (
                <><Loader2 size={14} className="animate-spin" />Preparing…</>
              ) : status === 'shared' ? (
                <><Check size={14} />Downloaded</>
              ) : (
                <><Download size={14} />Download card</>
              )}
            </button>
          )}

          {!isPro && (
            <button
              data-testid="session-share-try-pro"
              onClick={() => { onUpgrade && onUpgrade(); onClose(); }}
              className="w-full py-2 rounded-lg text-[#C4A67A] hover:text-[#E8B872] text-xs tracking-wide transition-colors"
            >
              Try Pro to add your Flow journey to the card →
            </button>
          )}

          <button
            data-testid="session-share-dismiss"
            onClick={onClose}
            className="w-full py-2 rounded-lg text-[#8A9A92] hover:text-[#C9DED6] text-xs tracking-wide transition-colors"
          >
            Maybe later
          </button>
        </div>
      </div>
    </div>
  );
}
