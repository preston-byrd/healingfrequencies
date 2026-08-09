import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Sparkles, X, Send, Loader2, Check } from 'lucide-react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import audioEngine from '@/lib/audioEngine';

/**
 * SupportBubble — floating "contact us" affordance shown on every logged-in
 * screen (Dashboard, Account, Admin). Bottom-right circular sparkle button.
 *
 * Behaviour:
 *   • Dims to ~35% opacity while audio is playing so it never fights for
 *     attention during a session; back to full opacity on hover or when
 *     playback stops.
 *   • Opens a two-step modal: (1) pick a reason chip, (2) write the message.
 *   • Name / email pre-fill from /auth/me; email stays read-only for signed-in
 *     users (we already know who they are — reduces friction).
 *   • Submits to POST /api/support/contact which delivers via Resend AND
 *     mirrors the message into `support_messages` in Mongo as a durable
 *     audit trail.
 *   • Success screen shows the exact copy from the product spec:
 *     "Thank you for reaching out. We will get back to you shortly."
 */

const REASONS = [
  { key: 'report_issue',      label: 'Report an Issue' },
  { key: 'share_feedback',    label: 'Share Feedback' },
  { key: 'express_gratitude', label: 'Express Gratitude' },
  { key: 'feature_request',   label: 'Feature Request' },
  { key: 'billing_question',  label: 'Billing Question' },
  { key: 'other',             label: 'Other' },
];

export default function SupportBubble() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState(null); // key or null
  const [message, setMessage] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState('idle'); // idle | sending | sent | error
  const [errMsg, setErrMsg] = useState('');
  // Track audio-engine playback so we can dim the bubble during sessions.
  const [audioPlaying, setAudioPlaying] = useState(() => !!audioEngine.playing);
  const textareaRef = useRef(null);

  // Subscribe to the audio engine's state stream. The engine debounces
  // internal emits, so this is cheap even during a soundbath.
  useEffect(() => {
    const off = audioEngine.on((s) => setAudioPlaying(!!s.playing));
    return off;
  }, []);

  // Pre-fill / re-sync identity fields whenever the modal opens or the
  // logged-in user changes. We never overwrite a value the user has typed
  // between opens — resetIdentity only runs when the modal transitions
  // from closed → open.
  useEffect(() => {
    if (!open) return;
    setName((user?.name || '').slice(0, 120));
    setEmail(user?.email || '');
    setReason(null);
    setMessage('');
    setStatus('idle');
    setErrMsg('');
  }, [open, user?.name, user?.email]);

  // Autofocus the textarea when we advance to step 2 so mobile users get
  // the keyboard immediately.
  useEffect(() => {
    if (open && reason) {
      // Delay one tick so the element is mounted before focus.
      const t = setTimeout(() => textareaRef.current?.focus(), 30);
      return () => clearTimeout(t);
    }
  }, [open, reason]);

  const activeReasonLabel = useMemo(
    () => REASONS.find((r) => r.key === reason)?.label,
    [reason]
  );

  const canSend = message.trim().length >= 10 && !!reason && status !== 'sending';

  const handleSend = async (e) => {
    e && e.preventDefault && e.preventDefault();
    if (!canSend) return;
    setStatus('sending');
    setErrMsg('');
    try {
      const { data } = await api.post('/support/contact', {
        reason,
        message: message.trim().slice(0, 4000),
        name: name.trim().slice(0, 120) || undefined,
        email: (email || '').trim() || undefined,
      });
      if (data && data.ok) {
        setStatus('sent');
      } else {
        throw new Error('Unexpected response');
      }
    } catch (err) {
      const detail = err?.response?.data?.detail;
      // FastAPI validation errors come back as an array; the common case is
      // "message too short". Show a friendly single line.
      let friendly = 'Something went wrong sending your message.';
      if (typeof detail === 'string') friendly = detail;
      else if (Array.isArray(detail) && detail[0]?.msg) friendly = detail[0].msg;
      setErrMsg(friendly);
      setStatus('error');
    }
  };

  const closeAndReset = () => {
    setOpen(false);
    // Slight delay so the close animation looks natural before state clears.
    setTimeout(() => {
      setReason(null);
      setMessage('');
      setStatus('idle');
      setErrMsg('');
    }, 200);
  };

  return (
    <>
      {/* Floating bubble */}
      <button
        type="button"
        data-testid="support-bubble-button"
        aria-label="Contact support"
        title="Contact support"
        onClick={() => setOpen(true)}
        className={`fixed bottom-5 right-5 z-40 w-12 h-12 rounded-full border border-[#C4A67A]/40 bg-[#0B1814] shadow-[0_8px_24px_rgba(0,0,0,0.5)] hover:bg-[#101F1A] hover:border-[#C4A67A] flex items-center justify-center transition-all group ${
          audioPlaying ? 'opacity-35 hover:opacity-100' : 'opacity-90 hover:opacity-100'
        }`}
        style={{
          bottom: 'calc(1.25rem + env(safe-area-inset-bottom, 0px))',
          right: 'calc(1.25rem + env(safe-area-inset-right, 0px))',
        }}
      >
        {/* Subtle gold-teal gradient halo — pure CSS, no re-render cost. */}
        <span
          aria-hidden="true"
          className="absolute inset-0 rounded-full pointer-events-none"
          style={{
            background: 'radial-gradient(circle at 30% 30%, rgba(196,166,122,0.28), rgba(114,194,172,0.12) 55%, transparent 75%)',
          }}
        />
        <Sparkles size={18} className="text-[#C4A67A] group-hover:text-[#E8B872] relative z-10" strokeWidth={1.75} />
      </button>

      {/* Modal */}
      {open && (
        <div
          className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center p-3 sm:p-4 bg-black/70 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="Contact support"
          data-testid="support-modal"
          onClick={closeAndReset}
        >
          <div
            className="relative w-full sm:max-w-md rounded-2xl border border-[#5C9E8C]/25 bg-[#0B1814] shadow-[0_20px_60px_rgba(0,0,0,0.5)] p-5 sm:p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              data-testid="support-modal-close"
              onClick={closeAndReset}
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
              <div className="text-[15px] text-[#E8E3D9] font-medium">
                {status === 'sent'
                  ? 'Thank you'
                  : reason
                    ? activeReasonLabel
                    : 'How can we help?'}
              </div>
              {!reason && status !== 'sent' && (
                <div className="text-[12px] text-[#8A9A92] mt-1">
                  Pick a reason so we can route your message to the right person.
                </div>
              )}
            </div>

            {/* Step 1: pick a reason */}
            {!reason && status !== 'sent' && (
              <div className="grid grid-cols-2 gap-2" data-testid="support-reasons">
                {REASONS.map((r) => (
                  <button
                    key={r.key}
                    data-testid={`support-reason-${r.key}`}
                    onClick={() => setReason(r.key)}
                    className="text-left py-3 px-3 rounded-lg bg-black/25 hover:bg-[#5C9E8C]/20 border border-[#5C9E8C]/20 hover:border-[#72C2AC]/50 text-[#C9DED6] hover:text-[#E8E3D9] text-[13px] tracking-wide transition-colors"
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            )}

            {/* Step 2: message form */}
            {reason && status !== 'sent' && (
              <form onSubmit={handleSend} className="flex flex-col gap-3" data-testid="support-form">
                <div>
                  <label className="block text-[10px] uppercase tracking-widest text-[#8A9A92] mb-1.5">Message</label>
                  <textarea
                    ref={textareaRef}
                    data-testid="support-message"
                    value={message}
                    onChange={(e) => setMessage(e.target.value.slice(0, 4000))}
                    placeholder="Tell us what's on your mind — we read every message."
                    rows={5}
                    required
                    minLength={10}
                    maxLength={4000}
                    className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2.5 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none resize-none transition-colors"
                  />
                  <div className="flex items-center justify-between text-[10px] text-[#5A6B65] mt-1">
                    <span>{message.trim().length < 10 ? `${10 - message.trim().length} more chars` : 'Ready to send'}</span>
                    <span>{message.length}/4000</span>
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[10px] uppercase tracking-widest text-[#8A9A92] mb-1.5">Name</label>
                    <input
                      data-testid="support-name"
                      value={name}
                      onChange={(e) => setName(e.target.value.slice(0, 120))}
                      placeholder="Your name"
                      className="w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none transition-colors"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] uppercase tracking-widest text-[#8A9A92] mb-1.5">Email</label>
                    <input
                      data-testid="support-email"
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value.slice(0, 200))}
                      placeholder="you@example.com"
                      readOnly={!!user?.email}
                      className={`w-full bg-black/25 border border-[#5C9E8C]/25 focus:border-[#72C2AC] rounded-lg px-3 py-2 text-[13px] text-[#E8E3D9] placeholder-[#5A6B65] outline-none transition-colors ${
                        user?.email ? 'cursor-not-allowed opacity-80' : ''
                      }`}
                    />
                  </div>
                </div>

                {errMsg && (
                  <div className="text-[11px] text-[#C4A67A]/90 italic" data-testid="support-error">
                    {errMsg}
                  </div>
                )}

                <div className="flex items-center gap-2 pt-1">
                  <button
                    type="button"
                    data-testid="support-back"
                    onClick={() => setReason(null)}
                    disabled={status === 'sending'}
                    className="px-3 py-2.5 rounded-lg text-[#8A9A92] hover:text-[#C9DED6] text-xs tracking-wide transition-colors disabled:opacity-50"
                  >
                    ← Back
                  </button>
                  <button
                    type="submit"
                    data-testid="support-send"
                    disabled={!canSend}
                    className="flex-1 py-2.5 rounded-lg bg-[#5C9E8C]/25 hover:bg-[#5C9E8C]/40 disabled:opacity-40 disabled:cursor-not-allowed border border-[#72C2AC]/50 hover:border-[#72C2AC] text-[#72C2AC] text-sm font-medium tracking-wide transition-colors inline-flex items-center justify-center gap-2"
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

            {/* Success state */}
            {status === 'sent' && (
              <div className="text-center flex flex-col items-center gap-3" data-testid="support-success">
                <div className="w-12 h-12 rounded-full bg-[#5C9E8C]/20 border border-[#72C2AC]/40 flex items-center justify-center">
                  <Check size={20} className="text-[#72C2AC]" />
                </div>
                <div className="text-[14px] text-[#E8E3D9] leading-relaxed max-w-xs">
                  Thank you for reaching out. We will get back to you shortly.
                </div>
                <button
                  data-testid="support-success-close"
                  onClick={closeAndReset}
                  className="mt-2 px-4 py-2 rounded-lg bg-black/25 hover:bg-black/40 border border-[#5C9E8C]/25 hover:border-[#5C9E8C]/45 text-[#C9DED6] text-xs tracking-wide transition-colors"
                >
                  Close
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
