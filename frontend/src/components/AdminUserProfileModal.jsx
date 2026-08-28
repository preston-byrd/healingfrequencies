import React, { useEffect, useMemo, useState } from 'react';
import {
  X, UserCog, Save, AlertTriangle, CheckCircle, Loader2,
  Mail, Shield, BellRing, FileText, RotateCcw, Info, Phone,
} from 'lucide-react';
import api, { formatApiError } from '@/lib/api';

/**
 * AdminUserProfileModal — Full admin-facing profile editor for a single user.
 *
 * Opens from the admin user-management row ("Profile" button). Loads the
 * target user's editable profile via GET /api/admin/users/{id}/profile,
 * groups fields by section (Account · Nudges · Notifications · Advanced),
 * and PATCHes changes via PUT with per-field auditing. Sensitive fields
 * (email, role) require an inline "confirm" checkbox before Save fires so
 * a mis-click never re-roles someone.
 */
export default function AdminUserProfileModal({ userId, open, onClose, onSaved }) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const [profile, setProfile] = useState(null);
  const [form, setForm] = useState({});
  const [saveMsg, setSaveMsg] = useState('');
  const [confirmSensitive, setConfirmSensitive] = useState(false);
  const [tab, setTab] = useState('account');

  useEffect(() => {
    if (!open || !userId) return undefined;
    let cancelled = false;
    setLoading(true);
    setErr('');
    setSaveMsg('');
    setConfirmSensitive(false);
    (async () => {
      try {
        const { data } = await api.get(`/admin/users/${userId}/profile`);
        if (cancelled) return;
        setProfile(data);
        // Seed the form from the loaded doc.
        setForm({
          name: data.name || '',
          email: data.email || '',
          role: data.role || 'user',
          phone_number: data.phone_number || '',
          phone_verified: !!data.phone_verified,
          plan_notes: data.plan_notes || '',
          nudge_cadence: data.nudge_cadence || 'default',
          nudge_unsubscribed: !!data.nudge_unsubscribed,
          notification_prefs: {
            enabled: data.notification_prefs?.enabled !== false,
            push_enabled: !!data.notification_prefs?.push_enabled,
            max_per_day: data.notification_prefs?.max_per_day ?? 4,
          },
        });
      } catch (e) {
        if (!cancelled) setErr(formatApiError(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open, userId]);

  // Diff form vs loaded profile — used to compute the payload + gate Save.
  const diff = useMemo(() => {
    if (!profile) return { changes: [], sensitive: false };
    const out = [];
    const sensitiveTouched = [];
    if ((form.name || '') !== (profile.name || '')) out.push(['name', form.name]);
    if ((form.email || '').toLowerCase() !== (profile.email || '').toLowerCase()) {
      out.push(['email', form.email]);
      sensitiveTouched.push('email');
    }
    if ((form.role || 'user') !== (profile.role || 'user')) {
      out.push(['role', form.role]);
      sensitiveTouched.push('role');
    }
    // Phone number — normalise "empty" both sides so an untouched blank
    // field never registers as a diff. Trim whitespace so trailing spaces
    // don't falsely trigger the sensitive-confirm gate.
    const curPhone = (profile.phone_number || '').trim();
    const nextPhone = (form.phone_number || '').trim();
    if (nextPhone !== curPhone) {
      out.push(['phone_number', nextPhone]);
      sensitiveTouched.push('phone_number');
    }
    if (!!form.phone_verified !== !!profile.phone_verified) {
      out.push(['phone_verified', !!form.phone_verified]);
      sensitiveTouched.push('phone_verified');
    }
    if ((form.plan_notes || '') !== (profile.plan_notes || '')) out.push(['plan_notes', form.plan_notes]);
    if ((form.nudge_cadence || 'default') !== (profile.nudge_cadence || 'default')) out.push(['nudge_cadence', form.nudge_cadence]);
    if (!!form.nudge_unsubscribed !== !!profile.nudge_unsubscribed) out.push(['nudge_unsubscribed', form.nudge_unsubscribed]);
    const np = profile.notification_prefs || {};
    const fnp = form.notification_prefs || {};
    const npDelta = {};
    if ((fnp.enabled !== undefined) && (fnp.enabled !== (np.enabled !== false))) npDelta.enabled = fnp.enabled;
    if ((fnp.push_enabled !== undefined) && (!!fnp.push_enabled !== !!np.push_enabled)) npDelta.push_enabled = !!fnp.push_enabled;
    if ((fnp.max_per_day !== undefined) && (Number(fnp.max_per_day) !== Number(np.max_per_day ?? 4))) {
      npDelta.max_per_day = Number(fnp.max_per_day);
    }
    if (Object.keys(npDelta).length > 0) out.push(['notification_prefs', npDelta]);
    return { changes: out, sensitive: sensitiveTouched.length > 0, sensitiveTouched };
  }, [profile, form]);

  const canSave = !saving && !loading && diff.changes.length > 0 && (!diff.sensitive || confirmSensitive);

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setErr('');
    setSaveMsg('');
    try {
      const payload = { confirm: !!confirmSensitive };
      diff.changes.forEach(([k, v]) => { payload[k] = v; });
      const { data } = await api.put(`/admin/users/${userId}/profile`, payload);
      setProfile((prev) => ({ ...(prev || {}), ...(data.user || {}) }));
      setSaveMsg(`${data.changes?.length || 0} field${(data.changes?.length || 0) === 1 ? '' : 's'} updated.`);
      setConfirmSensitive(false);
      if (onSaved) onSaved(data.user);
      // Auto-clear the success flash after 3s so the modal doesn't linger
      // with stale confirmation text between edits.
      setTimeout(() => setSaveMsg(''), 3000);
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setSaving(false);
    }
  };

  const runReset = async (kind) => {
    // kind: 'reset_hearing_profile' | 'reset_prefs'
    // eslint-disable-next-line no-alert
    if (!window.confirm(
      kind === 'reset_hearing_profile'
        ? 'Clear this user\'s hearing calibration profile? They\'ll need to re-run the audiogram test.'
        : 'Clear this user\'s dashboard preferences (frequency, ambient mix, waveform)?'
    )) return;
    setSaving(true);
    setErr('');
    try {
      const { data } = await api.put(`/admin/users/${userId}/profile`, { [kind]: true });
      setSaveMsg(`${data.changes?.length || 0} field${(data.changes?.length || 0) === 1 ? '' : 's'} reset.`);
      // Refresh loaded profile so the modal reflects the cleared state.
      const fresh = await api.get(`/admin/users/${userId}/profile`);
      setProfile(fresh.data);
      setTimeout(() => setSaveMsg(''), 3000);
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div
      data-testid="admin-user-profile-modal"
      className="fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto bg-black/70 backdrop-blur-sm p-4 pt-10"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-3xl bg-[#0F1F1B] border border-white/10 rounded-2xl shadow-2xl"
      >
        <div className="flex items-center justify-between p-5 border-b border-white/5">
          <div className="flex items-center gap-2">
            <UserCog size={18} className="text-[#72C2AC]" />
            <div>
              <div className="text-sm text-[#E7EFEA] font-medium">Admin · Edit user profile</div>
              <div className="text-[11px] text-[#8A9A92]">
                {profile ? profile.email : 'Loading…'}
                {profile?._plan_label && (
                  <span className={`ml-2 px-1.5 py-0.5 rounded-full text-[10px] ${
                    profile._plan_label === 'admin' ? 'bg-[#5C9E8C]/20 text-[#72C2AC]'
                      : profile._plan_label === 'pro' ? 'bg-[#C4A67A]/20 text-[#C4A67A]'
                      : 'bg-[#1A332A]/60 text-[#8A9A92]'
                  }`}>
                    {profile._plan_label.toUpperCase()}
                    {profile._pro ? ` · ${profile._days_left}d` : ''}
                  </span>
                )}
              </div>
            </div>
          </div>
          <button
            data-testid="admin-user-profile-close"
            onClick={onClose}
            className="p-1.5 rounded-full text-[#8A9A92] hover:text-[#E7EFEA] hover:bg-white/5 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {loading && (
          <div className="p-8 text-center text-[#8A9A92] text-sm flex items-center justify-center gap-2">
            <Loader2 size={14} className="animate-spin" /> Loading profile…
          </div>
        )}

        {!loading && profile && (
          <>
            <div className="flex items-center gap-1 px-5 pt-4 border-b border-white/5 -mb-px">
              {[
                { key: 'account', label: 'Account', Icon: Mail },
                { key: 'nudges', label: 'Nudges', Icon: BellRing },
                { key: 'notifications', label: 'Notifications', Icon: BellRing },
                { key: 'advanced', label: 'Advanced', Icon: RotateCcw },
              ].map(({ key, label, Icon }) => (
                <button
                  key={key}
                  onClick={() => setTab(key)}
                  data-testid={`admin-profile-tab-${key}`}
                  className={`px-3 py-2 text-xs inline-flex items-center gap-1 border-b-2 transition-colors ${
                    tab === key
                      ? 'border-[#72C2AC] text-[#E7EFEA]'
                      : 'border-transparent text-[#8A9A92] hover:text-[#E7EFEA]'
                  }`}
                >
                  <Icon size={11} />
                  {label}
                </button>
              ))}
            </div>

            <div className="p-5 space-y-4">
              {tab === 'account' && (
                <>
                  <Field label="Name">
                    <input
                      data-testid="admin-profile-name"
                      type="text"
                      maxLength={80}
                      value={form.name}
                      onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                      className="admin-input"
                    />
                  </Field>
                  <Field label="Email" hint="Sensitive — requires confirm">
                    <input
                      data-testid="admin-profile-email"
                      type="email"
                      value={form.email}
                      onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                      className="admin-input"
                    />
                  </Field>
                  <Field
                    label={
                      <span className="inline-flex items-center gap-1.5">
                        <Phone size={11} className="text-[#8A9A92]" /> Phone number
                        {form.phone_verified ? (
                          <span
                            data-testid="admin-profile-phone-verified-badge"
                            className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] bg-[#5C9E8C]/20 text-[#72C2AC]"
                          >
                            <CheckCircle size={9} /> Verified
                          </span>
                        ) : (
                          <span
                            data-testid="admin-profile-phone-unverified-badge"
                            className="px-1.5 py-0.5 rounded-full text-[9px] bg-[#1A332A]/60 text-[#8A9A92]"
                          >
                            Unverified
                          </span>
                        )}
                      </span>
                    }
                    hint="Sensitive — E.164 (e.g. +14155552671). Leave empty to clear."
                  >
                    <input
                      data-testid="admin-profile-phone"
                      type="tel"
                      maxLength={20}
                      placeholder="+14155552671"
                      value={form.phone_number}
                      onChange={(e) => setForm((f) => ({ ...f, phone_number: e.target.value }))}
                      className="admin-input font-mono"
                    />
                    <label className="mt-2 inline-flex items-center gap-2 text-[11px] text-[#8A9A92]">
                      <input
                        data-testid="admin-profile-phone-verified-toggle"
                        type="checkbox"
                        checked={!!form.phone_verified}
                        onChange={(e) => setForm((f) => ({ ...f, phone_verified: e.target.checked }))}
                      />
                      Manually mark phone as verified (bypasses SMS round-trip)
                    </label>
                  </Field>
                  <Field label="Role" hint="Sensitive — requires confirm">
                    <select
                      data-testid="admin-profile-role"
                      value={form.role}
                      onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
                      className="admin-input"
                    >
                      <option value="user">User</option>
                      <option value="admin">Admin</option>
                    </select>
                  </Field>
                  <Field label="Plan notes" hint="Internal only. Users never see this.">
                    <textarea
                      data-testid="admin-profile-plan-notes"
                      rows={3}
                      maxLength={2000}
                      value={form.plan_notes}
                      onChange={(e) => setForm((f) => ({ ...f, plan_notes: e.target.value }))}
                      placeholder="e.g. VIP referral · granted trial extension on 2026-02-14 · comp Pro until end of Q2"
                      className="admin-input resize-y"
                    />
                  </Field>
                </>
              )}

              {tab === 'nudges' && (
                <>
                  <Field label="Nudge cadence">
                    <select
                      data-testid="admin-profile-nudge-cadence"
                      value={form.nudge_cadence}
                      onChange={(e) => setForm((f) => ({ ...f, nudge_cadence: e.target.value }))}
                      className="admin-input"
                    >
                      <option value="default">Default (every 72h)</option>
                      <option value="weekly">Weekly (every 7 days)</option>
                      <option value="paused">Paused (no re-engagement emails)</option>
                    </select>
                  </Field>
                  <Field label="Unsubscribed from all re-engagement">
                    <label className="inline-flex items-center gap-2 text-xs text-[#E7EFEA]">
                      <input
                        data-testid="admin-profile-nudge-unsub"
                        type="checkbox"
                        checked={!!form.nudge_unsubscribed}
                        onChange={(e) => setForm((f) => ({ ...f, nudge_unsubscribed: e.target.checked }))}
                      />
                      Yes — silence every re-engagement email
                    </label>
                  </Field>
                </>
              )}

              {tab === 'notifications' && (
                <>
                  <Field label="Notifications enabled">
                    <label className="inline-flex items-center gap-2 text-xs text-[#E7EFEA]">
                      <input
                        data-testid="admin-profile-notif-enabled"
                        type="checkbox"
                        checked={form.notification_prefs?.enabled !== false}
                        onChange={(e) => setForm((f) => ({
                          ...f,
                          notification_prefs: { ...f.notification_prefs, enabled: e.target.checked },
                        }))}
                      />
                      Master toggle
                    </label>
                  </Field>
                  <Field label="Push notifications">
                    <label className="inline-flex items-center gap-2 text-xs text-[#E7EFEA]">
                      <input
                        data-testid="admin-profile-notif-push"
                        type="checkbox"
                        checked={!!form.notification_prefs?.push_enabled}
                        onChange={(e) => setForm((f) => ({
                          ...f,
                          notification_prefs: { ...f.notification_prefs, push_enabled: e.target.checked },
                        }))}
                      />
                      Browser push opt-in
                    </label>
                  </Field>
                  <Field label="Max notifications per day">
                    <input
                      data-testid="admin-profile-notif-max"
                      type="number"
                      min={0}
                      max={50}
                      value={form.notification_prefs?.max_per_day ?? 4}
                      onChange={(e) => setForm((f) => ({
                        ...f,
                        notification_prefs: { ...f.notification_prefs, max_per_day: Number(e.target.value) },
                      }))}
                      className="admin-input w-24"
                    />
                  </Field>
                </>
              )}

              {tab === 'advanced' && (
                <>
                  <Field label="Read-only account info">
                    <div className="text-[11px] text-[#8A9A92] space-y-1">
                      <div>User ID: <span className="font-mono text-[#E7EFEA]">{profile.id}</span></div>
                      <div>Created: <span className="text-[#E7EFEA]">{profile.created_at || '—'}</span></div>
                      <div>Last login: <span className="text-[#E7EFEA]">{profile.last_login_at || '—'}</span></div>
                      <div>Stripe customer: <span className="text-[#E7EFEA]">{profile.stripe_customer_id || '—'}</span></div>
                      <div>Hearing profile: <span className="text-[#E7EFEA]">{profile.hearing_profile ? 'captured' : '—'}</span></div>
                      <div>Dashboard prefs: <span className="text-[#E7EFEA]">{profile.prefs ? 'saved' : '—'}</span></div>
                    </div>
                  </Field>
                  <Field label="Reset actions" hint="Destructive · cannot be undone">
                    <div className="flex flex-col gap-2">
                      <button
                        type="button"
                        data-testid="admin-profile-reset-hearing"
                        onClick={() => runReset('reset_hearing_profile')}
                        disabled={saving || !profile.hearing_profile}
                        className="admin-btn-destructive disabled:opacity-30"
                      >
                        <RotateCcw size={11} /> Reset hearing calibration
                      </button>
                      <button
                        type="button"
                        data-testid="admin-profile-reset-prefs"
                        onClick={() => runReset('reset_prefs')}
                        disabled={saving || !profile.prefs}
                        className="admin-btn-destructive disabled:opacity-30"
                      >
                        <RotateCcw size={11} /> Reset dashboard preferences
                      </button>
                    </div>
                  </Field>
                  <Field label="Note">
                    <div className="text-[10px] text-[#8A9A92] flex items-start gap-1">
                      <Info size={11} className="mt-0.5 flex-shrink-0" />
                      Pro grant / revoke lives in the main user list. Passwords cannot be
                      set by admins — direct the user to the &quot;Forgot password&quot; flow.
                      Harmonic Blueprint data is user-owned and never edited from here.
                    </div>
                  </Field>
                </>
              )}
            </div>

            <div className="p-5 border-t border-white/5 space-y-3">
              {err && (
                <div className="text-xs text-[#D96C6C] flex items-center gap-1" data-testid="admin-profile-error">
                  <AlertTriangle size={12} /> {err}
                </div>
              )}
              {saveMsg && (
                <div className="text-xs text-[#72C2AC] flex items-center gap-1" data-testid="admin-profile-success">
                  <CheckCircle size={12} /> {saveMsg}
                </div>
              )}

              {diff.sensitive && (
                <label className="flex items-start gap-2 text-[11px] text-[#EFB067] p-2 rounded bg-[#EFB067]/5 border border-[#EFB067]/20">
                  <input
                    data-testid="admin-profile-confirm-sensitive"
                    type="checkbox"
                    checked={confirmSensitive}
                    onChange={(e) => setConfirmSensitive(e.target.checked)}
                    className="mt-0.5"
                  />
                  <span>
                    <strong>Sensitive change:</strong> you&apos;re about to update{' '}
                    <code className="text-[#E7EFEA]">{(diff.sensitiveTouched || []).join(', ')}</code>.
                    Tick to confirm.
                  </span>
                </label>
              )}

              <div className="flex items-center justify-between">
                <div className="text-[11px] text-[#8A9A92]">
                  {diff.changes.length === 0
                    ? 'No unsaved changes.'
                    : `${diff.changes.length} unsaved change${diff.changes.length === 1 ? '' : 's'}`}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={onClose}
                    className="px-3 py-1.5 text-xs text-[#8A9A92] hover:text-[#E7EFEA] transition-colors"
                  >
                    Close
                  </button>
                  <button
                    type="button"
                    data-testid="admin-profile-save"
                    onClick={handleSave}
                    disabled={!canSave}
                    className={`px-4 py-1.5 rounded-full text-xs inline-flex items-center gap-1 transition-colors ${
                      canSave
                        ? 'bg-[#72C2AC]/20 hover:bg-[#72C2AC]/40 border border-[#72C2AC]/40 text-[#72C2AC]'
                        : 'bg-white/5 text-[#8A9A92] cursor-not-allowed'
                    }`}
                  >
                    {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
                    {saving ? 'Saving…' : 'Save changes'}
                  </button>
                </div>
              </div>
            </div>
          </>
        )}

        {!loading && !profile && err && (
          <div className="p-8 text-center">
            <div className="text-xs text-[#D96C6C] flex items-center justify-center gap-1">
              <AlertTriangle size={12} /> {err}
            </div>
          </div>
        )}
      </div>

      <style>{`
        .admin-input {
          width: 100%;
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 8px;
          padding: 8px 10px;
          color: #E7EFEA;
          font-size: 12px;
          outline: none;
          transition: border-color 0.15s;
        }
        .admin-input:focus { border-color: #72C2AC; }
        .admin-btn-destructive {
          display: inline-flex; align-items: center; gap: 4px;
          padding: 6px 10px; border-radius: 999px;
          border: 1px solid rgba(217,108,108,0.4);
          color: #D96C6C; background: transparent;
          font-size: 11px; cursor: pointer;
          transition: background 0.15s;
        }
        .admin-btn-destructive:hover:not(:disabled) { background: rgba(217,108,108,0.1); }
      `}</style>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label className="text-[11px] text-[#8A9A92]">{label}</label>
        {hint && <span className="text-[10px] text-[#8A9A92] italic">{hint}</span>}
      </div>
      {children}
    </div>
  );
}
