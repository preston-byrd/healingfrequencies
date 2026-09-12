import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, X, Send, Loader2, Check } from 'lucide-react';
import api from '@/lib/api';

/**
 * PublicSupportModal — the "Support" modal available to LOGGED-OUT visitors
 * from the landing-page footer. Mirrors the in-app SupportBubble styling
 * (dark theme, Cormorant/system stack, gold-teal palette) but requires all
 * four fields — Name, Email, Reason, Message — because we have no auth
 * context to fall back on.
 *
 * Submits to `POST /api/public/support/contact`, which routes the message
 * to the admin support email via Resend and stores the ticket in
 * `support_messages` for the admin inbox.
 */

// Reasons requested by the spec: Report an Issue, Feedback, Feature Request,
// Billing, Other. Backend keys reuse the existing catalogue.
const REASONS = [
  { key: 'report_issue',     label: 'Report an Issue' },
  { key: 'share_feedback',   label: 'Feedback' },
  { key: 'feature_request',  label: 'Feature Request' },
  { key: 'billing_question', label: 'Billing' },
  { key: 'other',            label: 'Other' },
];

export default function PublicSupportModal({ open, onClose }) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState('idle'); // idle | sending | sent | error
  const [errMsg, setErrMsg] = useState('');
  const nameRef = useRef(null);

  // Reset form each time the modal opens.
  useEffect(() => {
    if (!open) return;
    setName('');
    setEmail('');
    setReason('');
    setMessage('');
    setStatus('idle');
    setErrMsg('');
    const t = setTimeout(() => nameRef.current?.focus(), 30);
    return () => clearTimeout(t);
  }, [open]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const h = (e) => { if (e.key === 'Escape') onClose && onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);

  if (!open) return null;

  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const MIN_MSG = 2;
  const canSend =
    name.trim().length >= 1 &&
    emailValid &&
    !!reason &&
    message.trim().length >= MIN_MSG &&
    status !== 'sending';

  // Called when the user taps the (visually disabled) Send button — we
  // surface a clear inline hint about which field is blocking the submit
  // instead of failing silently.
  const explainWhyBlocked = () => {
    if (!name.trim()) return 'Please add your name.';
    if (!emailValid) return 'Please enter a valid email address.';
    if (!reason) return 'Please select a reason.';
    if (message.trim().length < MIN_MSG) return 'Please add a short message.';
    return '';
  };

  const handleSend = async (e) => {
    e.preventDefault();
    if (!canSend) {
      const hint = explainWhyBlocked();
      if (hint) setErrMsg(hint);
      return;
    }
    setStatus('sending');
    setErrMsg('');
    try {
      const { data } = await api.post('/public/support/contact', {
        name: name.trim().slice(0, 120),
        email: email.trim().slice(0, 200),
        reason,
        message: message.trim().slice(0, 4000),
      });
      if (data && data.ok) {
        setStatus('sent');
      } else {
        throw new Error('Unexpected response');
      }
    } catch (err) {
      const detail = err?.response?.data?.detail;
      let friendly = 'Something went wrong sending your message.';
      if (typeof detail === 'string') friendly = detail;
      else if (Array.isArray(detail) && detail[0]?.msg) friendly = detail[0].msg;
      setErrMsg(friendly);
      setStatus('error');
    }
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end sm:items-center justify-center p-3 sm:p-4 bg-black/70 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Contact support"
      data-testid="public-support-modal"
      onClick={onClose}
    >
      <div
        className="relative w-full sm:max-w-md rounded-2xl border border-[#5C9E8C]/25 bg-[#0B1814] shadow-[0_20px_60px_rgba(0,0,0,0.5)] p-5 sm:p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          data-testid="public-support-close"
          onClick={onClose}
          aria-label="Close"
          className="absolute top-3 right-3 text-[#8A9A92] hover:text-[#E8E3D9] transition-colors p-1"
        >
          <X size={16} />
        </button>

        <div className="text-center mb-5">
          <div className="flex justify-center mb-2">
            <div className="w-9 h-9 rounded-full bg-[#C4A67A]/15 flex items-center justify-center">
              <Sparkles size={16} className="text-[#C4A67A]" strokeWidth={1.75} />
            </div>
          </div>
          <div className="text-[11px] uppercase tracking-[0.2em] text-[#C4A67A] font-mono mb-2">
            Support
          </div>
          <div className="font-display text-[22px] text-[#E8E3D9] leading-tight">
            {status === 'sent' ? 'Thank you' : 'How can we help?'}
          </div>
          {status !== 'sent' && (
            <div className="text-[12px] text-[#8A9A92] mt-1.5">
              Tell us who you are and how we can reach you.
            </div>
          )}
        </div>

        {status !== 'sent' && (
          <form
            onSubmit={handleSend}
            className="flex flex-col gap-3"
            data-testid="public-support-form"
          >
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-[10px] uppercase tracking-widest text-[#8A9A92] mb-1.5">Name</label>
                <input
                  ref={nameRef}
                  data-testid="public-support-name"
                  value={name}
                  onChange={(e) => setName(e.target.value.slice(0, 120))}
                  placeholder="Your name"
                  required
                  className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none transition-colors"
                />
              </div>
              <div>
                <label className="block text-[10px] uppercase tracking-widest text-[#8A9A92] mb-1.5">Email</label>
                <input
                  data-testid="public-support-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value.slice(0, 200))}
                  placeholder="you@example.com"
                  required
                  className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none transition-colors"
                />
              </div>
            </div>

            <div>
              <label className="block text-[10px] uppercase tracking-widest text-[#8A9A92] mb-1.5">Reason</label>
              <select
                data-testid="public-support-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2 text-[13px] text-[#E8E3D9] outline-none transition-colors appearance-none cursor-pointer"
              >
                <option value="" disabled>Select a reason…</option>
                {REASONS.map((r) => (
                  <option key={r.key} value={r.key} className="bg-[#0B1814]">
                    {r.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-[10px] uppercase tracking-widest text-[#8A9A92] mb-1.5">Message</label>
              <textarea
                data-testid="public-support-message"
                value={message}
                onChange={(e) => setMessage(e.target.value.slice(0, 4000))}
                placeholder="Tell us what's on your mind — we read every message."
                rows={5}
                required
                minLength={MIN_MSG}
                maxLength={4000}
                className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2.5 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none resize-none transition-colors"
              />
              <div className="flex items-center justify-between text-[10px] text-[#5A6B65] mt-1">
                <span>
                  {message.trim().length < MIN_MSG
                    ? 'Add a short message'
                    : 'Ready to send'}
                </span>
                <span>{message.length}/4000</span>
              </div>
            </div>

            {errMsg && (
              <div
                className="text-[11px] text-[#C4A67A]/90 italic"
                data-testid="public-support-error"
              >
                {errMsg}
              </div>
            )}

            <div className="pt-1">
              <button
                type="submit"
                data-testid="public-support-send"
                aria-disabled={!canSend}
                className={`w-full py-2.5 rounded-lg border border-[#72C2AC]/50 hover:border-[#72C2AC] text-[#72C2AC] text-sm font-medium tracking-wide transition-colors inline-flex items-center justify-center gap-2 ${
                  canSend
                    ? 'bg-[#5C9E8C]/25 hover:bg-[#5C9E8C]/40 cursor-pointer'
                    : 'bg-[#5C9E8C]/10 opacity-70 cursor-pointer'
                }`}
              >
                {status === 'sending' ? (
                  <><Loader2 size={14} className="animate-spin" />Sending…</>
                ) : (
                  <><Send size={14} />Send</>
                )}
              </button>
            </div>
          </form>
        )}

        {status === 'sent' && (
          <div
            className="text-center flex flex-col items-center gap-3"
            data-testid="public-support-success"
          >
            <div className="w-12 h-12 rounded-full bg-[#5C9E8C]/20 border border-[#72C2AC]/40 flex items-center justify-center">
              <Check size={20} className="text-[#72C2AC]" />
            </div>
            <div className="text-[14px] text-[#E8E3D9] leading-relaxed max-w-xs">
              Thank you for reaching out. A member of our team will get back to you soon.
            </div>
            <button
              data-testid="public-support-success-close"
              onClick={onClose}
              className="mt-2 px-4 py-2 rounded-lg bg-black/25 hover:bg-black/40 border border-[#5C9E8C]/25 hover:border-[#5C9E8C]/45 text-[#C9DED6] text-xs tracking-wide transition-colors"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
