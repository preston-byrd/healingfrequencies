import React, { useState } from 'react';
import { X, Copy, Check, ExternalLink, Smartphone } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';

/**
 * Payment Link modal — shows a Stripe Checkout URL with a copy button and a
 * scannable QR code so users can finish payment on another device (e.g., start
 * on desktop, complete on phone via QR). The same URL is a valid Stripe Checkout
 * Session, so all payment-status polling / fulfillment logic still applies.
 */
export function PaymentLinkModal({ url, planLabel, onClose }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch (e) {
      console.warn('[PaymentLinkModal] clipboard copy failed', e);
      // Fallback: select the input so user can manually copy
      const input = document.querySelector('[data-testid=payment-link-input]');
      if (input) input.select();
    }
  };

  return (
    <div
      data-testid="payment-link-modal"
      className="fixed inset-0 z-[55] flex items-center justify-center px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="paymentlink-title"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="absolute inset-0 bg-[#06100E]/85 backdrop-blur-md" />

      <div className="relative z-10 w-full max-w-md glass p-6 sm:p-8">
        <button
          onClick={onClose}
          data-testid="payment-link-close"
          className="absolute top-4 right-4 text-[#8A9A92] hover:text-[#E8E3D9] transition-colors"
          aria-label="Close"
        >
          <X size={18} />
        </button>

        <div className="label-tiny text-[#72C2AC] mb-2 inline-flex items-center gap-1.5">
          <Smartphone size={12} /> Pay from any device
        </div>
        <h2
          id="paymentlink-title"
          className="font-display text-2xl sm:text-3xl font-light text-[#E8E3D9] mb-2 leading-tight"
        >
          Your {planLabel} payment&nbsp;link
        </h2>
        <p className="text-xs text-[#8A9A92] mb-6 leading-relaxed">
          Scan with your phone camera, open the link in any browser, or copy and send it to yourself.
          We&apos;ll detect your payment automatically when you return.
        </p>

        {/* QR */}
        <div
          data-testid="payment-link-qr"
          className="bg-[#E8E3D9] p-4 rounded-lg flex items-center justify-center mb-5"
        >
          <QRCodeSVG
            value={url}
            size={188}
            level="M"
            bgColor="#E8E3D9"
            fgColor="#0A1A16"
          />
        </div>

        {/* URL + copy */}
        <div className="flex items-stretch gap-2 mb-3">
          <input
            data-testid="payment-link-input"
            readOnly
            value={url}
            onClick={(e) => e.target.select()}
            className="flex-1 min-w-0 px-3 py-2 rounded bg-[#08120F] border border-[#5C9E8C]/25 text-[11px] text-[#E8E3D9] font-mono outline-none focus:border-[#72C2AC]/60"
          />
          <button
            data-testid="payment-link-copy"
            onClick={handleCopy}
            className="px-3 rounded bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] transition-colors flex items-center gap-1.5 text-xs font-medium"
          >
            {copied ? <><Check size={14} /> Copied</> : <><Copy size={14} /> Copy</>}
          </button>
        </div>

        <a
          data-testid="payment-link-open"
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="block w-full text-center py-2.5 rounded-full border border-[#5C9E8C]/40 text-[#72C2AC] hover:bg-[#5C9E8C]/15 transition-colors text-xs inline-flex items-center justify-center gap-1.5"
        >
          <ExternalLink size={12} /> Open in new tab
        </a>
      </div>
    </div>
  );
}

export default PaymentLinkModal;
