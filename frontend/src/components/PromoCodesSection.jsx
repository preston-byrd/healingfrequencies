import React, { useEffect, useState } from 'react';
import { Ticket, Plus, X, Trash2, Copy, Check, Users } from 'lucide-react';
import api from '@/lib/api';

/**
 * PromoCodesSection — admin-only management UI for the three promo-code
 * families (Complimentary Access · Discount · Referral). Styling matches the
 * rest of the admin dashboard (sage/gold accents, glass-soft cards, mono
 * numerals). The section renders inside the AccountDashboard for admin users.
 */
function randomCode(len = 10) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let s = '';
  for (let i = 0; i < len; i++) s += chars.charAt(Math.floor(Math.random() * chars.length));
  return s;
}

const TYPE_LABELS = {
  comp:     'Complimentary Access',
  discount: 'Discount',
  referral: 'Referral',
};

export default function PromoCodesSection() {
  const [codes, setCodes] = useState([]);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState(null);
  const [err, setErr] = useState('');

  const load = async () => {
    try {
      const { data } = await api.get('/admin/promo');
      setCodes(data || []);
    } catch (e) {
      console.warn('[PromoCodes] load failed', e);
    }
  };
  useEffect(() => { load(); }, []);

  const toggleActive = async (code, next) => {
    try {
      await api.patch(`/admin/promo/${encodeURIComponent(code)}`, { active: next });
      load();
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Failed to update code');
    }
  };
  const deleteCode = async (code) => {
    if (!window.confirm(`Delete promo code "${code}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/promo/${encodeURIComponent(code)}`);
      load();
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Failed to delete code');
    }
  };

  return (
    <div className="glass p-4" data-testid="promo-codes-section">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Ticket size={14} className="text-[#C4A67A]" />
          <div className="label-tiny text-[#C4A67A]">Promo Codes</div>
        </div>
        <button
          data-testid="promo-create-open"
          onClick={() => setCreating(true)}
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-mono text-[#C4A67A] hover:text-[#E8B872] transition-colors"
        >
          <Plus size={11} /> Create Promo Code
        </button>
      </div>
      {err && <div className="text-[11px] text-[#D96C6C] mb-2" data-testid="promo-error">{err}</div>}

      {codes.length === 0 ? (
        <div className="text-xs text-[#8A9A92]">No promo codes yet.</div>
      ) : (
        <div className="space-y-2">
          {codes.map((c) => (
            <PromoRow
              key={c.code}
              code={c}
              onToggle={(next) => toggleActive(c.code, next)}
              onDelete={() => deleteCode(c.code)}
              onOpen={() => setSelected(c)}
            />
          ))}
        </div>
      )}

      {creating && (
        <CreatePromoModal
          onClose={() => setCreating(false)}
          onCreated={() => { setCreating(false); load(); }}
        />
      )}
      {selected && (
        <PromoDetailModal code={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function PromoRow({ code, onToggle, onDelete, onOpen }) {
  const [copied, setCopied] = useState(false);
  const copy = async (e) => {
    e.stopPropagation();
    try { await navigator.clipboard.writeText(code.code); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* graceful */ }
  };
  const usage = code.max_uses ? `${code.redemptions}/${code.max_uses}` : `${code.redemptions}/∞`;
  return (
    <div
      className={`glass-soft p-3 flex items-center gap-3 cursor-pointer transition-colors ${code.active ? '' : 'opacity-55'}`}
      data-testid={`promo-row-${code.code}`}
      onClick={onOpen}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <div className="text-sm font-mono text-[#E8E3D9] truncate">{code.code}</div>
          <button
            onClick={copy}
            title="Copy code"
            className="text-[#8A9A92] hover:text-[#C4A67A] transition-colors"
            data-testid={`promo-copy-${code.code}`}
          >
            {copied ? <Check size={11} className="text-[#72C2AC]" /> : <Copy size={11} />}
          </button>
        </div>
        <div className="text-[11px] text-[#8A9A92] mt-0.5">
          {TYPE_LABELS[code.type] || code.type}
          {code.type === 'comp' && ` · ${code.duration_days}d`}
          {code.type === 'discount' && ` · ${code.percent_off}% off ${code.applies_to}`}
          {code.type === 'referral' && ` · ${code.rep_name || 'partner'}`}
          <span className="mx-1.5 text-[#5A6B65]">·</span>
          <span className="font-mono">{usage}</span>
        </div>
      </div>
      <button
        data-testid={`promo-toggle-${code.code}`}
        onClick={(e) => { e.stopPropagation(); onToggle(!code.active); }}
        className={`px-2 py-1 rounded-md text-[10px] uppercase tracking-widest font-mono transition-colors ${
          code.active
            ? 'bg-[#5C9E8C]/25 text-[#72C2AC] border border-[#5C9E8C]/40'
            : 'bg-[#C4A67A]/10 text-[#C4A67A] border border-[#C4A67A]/30'
        }`}
      >
        {code.active ? 'Active' : 'Paused'}
      </button>
      <button
        data-testid={`promo-delete-${code.code}`}
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="text-[#8A9A92] hover:text-[#D96C6C] transition-colors"
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}

function CreatePromoModal({ onClose, onCreated }) {
  const [type, setType] = useState('comp');
  const [code, setCode] = useState(randomCode());
  const [duration, setDuration] = useState(30);
  const [percent, setPercent] = useState(20);
  const [appliesTo, setAppliesTo] = useState('both');
  const [repName, setRepName] = useState('');
  const [repEmail, setRepEmail] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [maxUses, setMaxUses] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!code.trim()) { setErr('Code required.'); return; }
    setErr(''); setBusy(true);
    const body = {
      code: code.trim().toUpperCase(),
      type,
      active: true,
      expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      max_uses: maxUses ? parseInt(maxUses, 10) : null,
    };
    if (type === 'comp') body.duration_days = parseInt(duration, 10);
    if (type === 'discount') { body.percent_off = parseInt(percent, 10); body.applies_to = appliesTo; }
    if (type === 'referral') { body.rep_name = repName; body.rep_email = repEmail || null; }
    try {
      await api.post('/admin/promo', body);
      onCreated();
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Failed to create code.');
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60" data-testid="promo-create-modal">
      <div className="glass-soft border border-[#5C9E8C]/30 rounded-2xl p-6 max-w-md w-full relative" style={{ backdropFilter: 'blur(16px)' }}>
        <button onClick={onClose} className="absolute top-3 right-3 text-[#5A6B65] hover:text-[#C9DED6]" aria-label="Close">
          <X size={14} />
        </button>
        <div className="label-tiny text-[#C4A67A] mb-4">Create Promo Code</div>

        <label className="text-[11px] text-[#8A9A92] block mb-1">Type</label>
        <div className="grid grid-cols-3 gap-1 mb-4">
          {['comp', 'discount', 'referral'].map((k) => (
            <button
              key={k}
              data-testid={`promo-type-${k}`}
              onClick={() => setType(k)}
              className={`text-[11px] py-1.5 rounded-md tracking-wide transition-colors ${
                type === k ? 'bg-[#5C9E8C]/25 text-[#72C2AC] border border-[#5C9E8C]/40' : 'text-[#8A9A92] border border-[#5C9E8C]/15'
              }`}
            >
              {TYPE_LABELS[k]}
            </button>
          ))}
        </div>

        <label className="text-[11px] text-[#8A9A92] block mb-1">Code</label>
        <div className="flex gap-2 mb-4">
          <input
            data-testid="promo-code-input"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            className="flex-1 bg-black/30 border border-[#5C9E8C]/25 rounded-lg px-3 py-2 text-sm font-mono text-[#E8E3D9] focus:outline-none focus:border-[#72C2AC]/60"
          />
          <button
            data-testid="promo-code-random"
            onClick={() => setCode(randomCode())}
            className="px-3 text-[10px] uppercase tracking-widest font-mono text-[#C4A67A] hover:text-[#E8B872] border border-[#C4A67A]/30 rounded-lg"
          >
            Random
          </button>
        </div>

        {type === 'comp' && (
          <div className="mb-4">
            <label className="text-[11px] text-[#8A9A92] block mb-1">Days of Pro access</label>
            <input
              data-testid="promo-comp-days"
              type="number" min="1" max="3650"
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
              className="w-full bg-black/30 border border-[#5C9E8C]/25 rounded-lg px-3 py-2 text-sm font-mono text-[#E8E3D9] focus:outline-none focus:border-[#72C2AC]/60"
            />
          </div>
        )}
        {type === 'discount' && (
          <>
            <div className="mb-3">
              <label className="text-[11px] text-[#8A9A92] block mb-1">Percent off</label>
              <input
                data-testid="promo-discount-percent"
                type="number" min="1" max="100"
                value={percent}
                onChange={(e) => setPercent(e.target.value)}
                className="w-full bg-black/30 border border-[#5C9E8C]/25 rounded-lg px-3 py-2 text-sm font-mono text-[#E8E3D9] focus:outline-none focus:border-[#72C2AC]/60"
              />
            </div>
            <div className="mb-4">
              <label className="text-[11px] text-[#8A9A92] block mb-1">Applies to</label>
              <div className="grid grid-cols-3 gap-1">
                {['monthly', 'annual', 'both'].map((s) => (
                  <button
                    key={s}
                    data-testid={`promo-applies-${s}`}
                    onClick={() => setAppliesTo(s)}
                    className={`text-[11px] py-1.5 rounded-md capitalize tracking-wide transition-colors ${
                      appliesTo === s ? 'bg-[#5C9E8C]/25 text-[#72C2AC] border border-[#5C9E8C]/40' : 'text-[#8A9A92] border border-[#5C9E8C]/15'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
        {type === 'referral' && (
          <>
            <div className="mb-3">
              <label className="text-[11px] text-[#8A9A92] block mb-1">Rep name</label>
              <input data-testid="promo-rep-name" value={repName} onChange={(e) => setRepName(e.target.value)}
                className="w-full bg-black/30 border border-[#5C9E8C]/25 rounded-lg px-3 py-2 text-sm text-[#E8E3D9] focus:outline-none focus:border-[#72C2AC]/60"
                placeholder="Sarah Chen" />
            </div>
            <div className="mb-4">
              <label className="text-[11px] text-[#8A9A92] block mb-1">Rep email (optional)</label>
              <input data-testid="promo-rep-email" value={repEmail} onChange={(e) => setRepEmail(e.target.value)}
                className="w-full bg-black/30 border border-[#5C9E8C]/25 rounded-lg px-3 py-2 text-sm text-[#E8E3D9] focus:outline-none focus:border-[#72C2AC]/60"
                placeholder="sarah@example.com" />
          </div>
          </>
        )}

        <div className="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label className="text-[11px] text-[#8A9A92] block mb-1">Expires (optional)</label>
            <input
              data-testid="promo-expires"
              type="date"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              className="w-full bg-black/30 border border-[#5C9E8C]/25 rounded-lg px-2 py-2 text-[12px] font-mono text-[#E8E3D9] focus:outline-none focus:border-[#72C2AC]/60"
            />
          </div>
          <div>
            <label className="text-[11px] text-[#8A9A92] block mb-1">Max uses (blank = ∞)</label>
            <input
              data-testid="promo-max-uses"
              type="number" min="1"
              value={maxUses}
              onChange={(e) => setMaxUses(e.target.value)}
              placeholder="Unlimited"
              className="w-full bg-black/30 border border-[#5C9E8C]/25 rounded-lg px-3 py-2 text-sm font-mono text-[#E8E3D9] focus:outline-none focus:border-[#72C2AC]/60"
            />
          </div>
        </div>

        {err && <div className="text-[11px] text-[#D96C6C] mb-3" data-testid="promo-create-error">{err}</div>}

        <button
          data-testid="promo-create-submit"
          onClick={submit}
          disabled={busy}
          className="w-full py-2.5 rounded-lg bg-[#5C9E8C]/25 hover:bg-[#5C9E8C]/40 border border-[#72C2AC]/50 hover:border-[#72C2AC] text-[#72C2AC] text-sm font-medium tracking-wide transition-colors disabled:opacity-40"
        >
          {busy ? 'Creating…' : 'Create Code'}
        </button>
      </div>
    </div>
  );
}

function PromoDetailModal({ code, onClose }) {
  const log = code.redemption_log || [];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60" data-testid="promo-detail-modal">
      <div className="glass-soft border border-[#5C9E8C]/30 rounded-2xl p-6 max-w-lg w-full relative" style={{ backdropFilter: 'blur(16px)' }}>
        <button onClick={onClose} className="absolute top-3 right-3 text-[#5A6B65] hover:text-[#C9DED6]" aria-label="Close">
          <X size={14} />
        </button>
        <div className="label-tiny text-[#C4A67A] mb-1">Redemption Log</div>
        <div className="text-sm font-mono text-[#E8E3D9] mb-4">{code.code} <span className="text-[11px] text-[#8A9A92] ml-2">· {TYPE_LABELS[code.type]}</span></div>
        <div className="flex items-center gap-2 text-[11px] text-[#8A9A92] mb-3">
          <Users size={11} /> {log.length} redemption{log.length !== 1 ? 's' : ''}
        </div>
        {log.length === 0 ? (
          <div className="text-[11px] text-[#8A9A92]">No one has used this code yet.</div>
        ) : (
          <div className="space-y-1.5 max-h-72 overflow-y-auto custom-scrollbar pr-1">
            {log.slice().reverse().map((entry, i) => (
              <div key={i} className="glass-soft p-2.5 text-[12px]">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[#E8E3D9] truncate">{entry.user_name || entry.user_email}</div>
                  <div className="font-mono text-[10px] text-[#72C2AC]">{entry.plan}</div>
                </div>
                <div className="text-[10px] font-mono text-[#8A9A92]">
                  {new Date(entry.redeemed_at).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
