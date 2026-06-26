import { useEffect, useState } from 'react';

/**
 * Detect Apple Pay / Google Pay availability so we can hide the corresponding
 * buttons on devices/browsers that don't support them.
 *
 * - Apple Pay: synchronous via `window.ApplePaySession.canMakePayments()`.
 *   Returns true on Safari (macOS/iOS/iPadOS) with the device capability,
 *   regardless of whether the user has cards in Wallet (Stripe Checkout will
 *   gate the actual UI).
 *
 * - Google Pay: async via `PaymentRequest.canMakePayment()` with the official
 *   google.com/pay supportedMethods identifier. Falls back to a UA-based hint
 *   (Chromium browsers) if PaymentRequest is unavailable.
 *
 * The returned flags are best-effort; the final source of truth is Stripe
 * Checkout itself, which will only render the AP/GP buttons inside its hosted
 * page when the browser truly supports them. If the heuristic over-shows a
 * button, the user clicks → Stripe Checkout falls back to the regular card
 * form. Graceful degradation, no error.
 */
export function usePaymentMethodSupport() {
  const [support, setSupport] = useState({ applePay: false, googlePay: false, ready: false });

  useEffect(() => {
    let cancelled = false;

    // Apple Pay — synchronous
    let applePay = false;
    try {
      if (
        typeof window !== 'undefined' &&
        typeof window.ApplePaySession !== 'undefined' &&
        typeof window.ApplePaySession.canMakePayments === 'function'
      ) {
        applePay = !!window.ApplePaySession.canMakePayments();
      }
    } catch (e) {
      console.warn('[usePaymentMethodSupport] Apple Pay detect failed', e);
    }

    // Google Pay — async via PaymentRequest
    (async () => {
      let googlePay = false;
      try {
        if (typeof window !== 'undefined' && window.PaymentRequest) {
          const ua = navigator.userAgent || '';
          const isChromeLike = /Chrome|Chromium|CriOS/i.test(ua) && !/Edg|Edge/i.test(ua);
          if (isChromeLike) {
            const req = new window.PaymentRequest(
              [{
                supportedMethods: 'https://google.com/pay',
                data: {
                  environment: 'TEST',
                  apiVersion: 2,
                  apiVersionMinor: 0,
                  allowedPaymentMethods: [{
                    type: 'CARD',
                    parameters: {
                      allowedAuthMethods: ['PAN_ONLY', 'CRYPTOGRAM_3DS'],
                      allowedCardNetworks: ['VISA', 'MASTERCARD', 'AMEX', 'DISCOVER'],
                    },
                  }],
                },
              }],
              { total: { label: 'Solarisound Pro', amount: { currency: 'USD', value: '1.00' } } },
            );
            googlePay = await req.canMakePayment();
          }
        }
      } catch (e) {
        console.warn('[usePaymentMethodSupport] Google Pay detect failed', e);
      }
      if (!cancelled) setSupport({ applePay, googlePay, ready: true });
    })();

    return () => { cancelled = true; };
  }, []);

  return support;
}

export default usePaymentMethodSupport;
