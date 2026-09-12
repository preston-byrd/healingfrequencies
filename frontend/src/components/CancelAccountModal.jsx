import React, { useState } from 'react';
import { X, AlertTriangle, Loader2 } from 'lucide-react';

/**
 * HF-043 Cancel Account confirmation modal.
 *
 * Deliberately friction-ful — user must type the word "close" AND click
 * the confirm button so this can't fire on an accidental tap. Optional
 * one-line reason survey helps us learn why people leave without making
 * them fill in a form.
 */
export default function CancelAccountModal({ onCancel, onConfirm, busy, hasActiveSub }) {
  const [reason, setReason] = useState('');
  const [confirmText, setConfirmText] = useState('');
  const canConfirm = confirmText.trim().toLowerCase() === 'close' && !busy;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(8, 18, 15, 0.72)', backdropFilter: 'blur(6px)' }}
      data-testid="cancel-account-modal"
    >
      <div className="glass w-full max-w-md p-6 border border-[#D96C6C]/30 relative">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          aria-label="Dismiss cancellation dialog"
          data-testid="cancel-account-modal-close"
          className="absolute top-3 right-3 text-[#8A9A92] hover:text-[#E8E3D9] transition-colors p-1 disabled:opacity-40"
        >
          <X size={16} />
        </button>

        <div className="flex items-start gap-3 mb-4">
          <div className="w-9 h-9 rounded-full bg-[#D96C6C]/10 border border-[#D96C6C]/30 flex items-center justify-center flex-shrink-0">
            <AlertTriangle size={15} className="text-[#D96C6C]" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="label-tiny text-[#D96C6C] mb-1">Close account</div>
            <div className="text-[16px] font-display text-[#E8E3D9] leading-snug">
              Are you sure you want to close your Solarisound account?
            </div>
          </div>
        </div>

        <div className="text-[13px] text-[#C9DED6] leading-relaxed space-y-2 mb-5">
          <p>Closing your account will:</p>
          <ul className="list-disc pl-5 space-y-1 text-[#8A9A92]">
            {hasActiveSub && (
              <li>Cancel your Pro subscription at the end of your current billing period — no further charges.</li>
            )}
            <li>Sign you out immediately on every device.</li>
            <li>Preserve your Harmonic Blueprint, journeys, and settings so signing in again fully reactivates your account.</li>
            <li>Continue occasional emails and texts about coming back — reply STOP to any text or use the unsubscribe link in any email to opt out.</li>
          </ul>
        </div>

        <label className="block text-[11px] text-[#8A9A92] mb-1 uppercase tracking-wider">
          Anything you'd like to share? <span className="text-[#5A6B65] normal-case tracking-normal">(optional)</span>
        </label>
        <textarea
          data-testid="cancel-account-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. I found what I needed / I'll be back next season / the app wasn't for me"
          maxLength={500}
          disabled={busy}
          className="admin-input w-full min-h-[70px] resize-y mb-4"
        />

        <label className="block text-[11px] text-[#8A9A92] mb-1 uppercase tracking-wider">
          To confirm, type <span className="font-mono text-[#D96C6C]">close</span> below
        </label>
        <input
          data-testid="cancel-account-confirm-input"
          type="text"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          placeholder="Type 'close' to enable the button"
          maxLength={20}
          autoComplete="off"
          disabled={busy}
          className="admin-input w-full mb-5 font-mono"
        />

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            data-testid="cancel-account-cancel-button"
            className="text-[13px] px-4 py-2 rounded-full text-[#8A9A92] hover:text-[#E8E3D9] transition-colors disabled:opacity-40"
          >
            Never mind
          </button>
          <button
            type="button"
            onClick={() => onConfirm(reason.trim() || null)}
            disabled={!canConfirm}
            data-testid="cancel-account-confirm-button"
            className="text-[13px] px-4 py-2 rounded-full bg-[#D96C6C] text-[#08120F] font-medium hover:bg-[#E88A8A] transition-colors disabled:opacity-30 inline-flex items-center gap-1.5"
          >
            {busy && <Loader2 size={12} className="animate-spin" />}
            {busy ? 'Closing…' : 'Close my account'}
          </button>
        </div>
      </div>
    </div>
  );
}
