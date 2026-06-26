import React, { useEffect, useState } from 'react';
import { ArrowLeft, Sparkles, Check, X, Loader2, Settings, Receipt, Users, Search } from 'lucide-react';
import api, { formatApiError } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

function fmtDate(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
  catch { return iso; }
}

function fmtMoney(amount, currency = 'usd') {
  try { return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency.toUpperCase() }).format(amount); }
  catch { return `$${amount}`; }
}

export default function AccountDashboard({ onBack }) {
  const { user } = useAuth();
  const [sub, setSub] = useState(null);
  const [plan, setPlan] = useState(null);
  const [tx, setTx] = useState([]);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  // password
  const [curPw, setCurPw] = useState('');
  const [newPw, setNewPw] = useState('');

  // admin price form
  const [monthly, setMonthly] = useState('');
  const [annual, setAnnual] = useState('');
  const [trial, setTrial] = useState('');

  // admin users panel
  const [users, setUsers] = useState([]);
  const [userQuery, setUserQuery] = useState('');
  const [grantDays, setGrantDays] = useState({});

  const loadUsers = async (q = '') => {
    try {
      const { data } = await api.get('/admin/users', { params: q ? { q } : {} });
      setUsers(data);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('loadUsers failed', e?.response?.status, e?.message);
    }
  };

  const load = async () => {
    try {
      const [s, p, t] = await Promise.all([
        api.get('/me/subscription'),
        api.get('/plan/config'),
        api.get('/me/transactions'),
      ]);
      setSub(s.data); setPlan(p.data); setTx(t.data);
      if (s.data.is_admin) {
        const ap = await api.get('/admin/plan-prices');
        setMonthly(String(ap.data.monthly.price));
        setAnnual(String(ap.data.annual.price));
        setTrial(String(ap.data.trial_days));
        await loadUsers();
      }
    } catch (e) { setErr(formatApiError(e)); }
  };

  useEffect(() => { load(); }, []);

  // Handle Stripe return: ?stripe_session_id=...
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sid = params.get('stripe_session_id');
    const canceled = params.get('stripe_canceled');
    if (canceled) {
      setMsg('Checkout canceled — no charges made.');
      window.history.replaceState({}, '', window.location.pathname);
    } else if (sid) {
      setBusy('polling');
      setMsg('Verifying payment…');
      let attempts = 0;
      const poll = async () => {
        attempts++;
        try {
          const { data } = await api.get(`/payments/status/${sid}`);
          if (data.payment_status === 'paid') {
            setMsg('Payment confirmed — welcome to Pro!');
            setBusy('');
            window.history.replaceState({}, '', window.location.pathname);
            load();
            return;
          }
          if (data.status === 'expired' || attempts > 8) {
            setMsg('Payment did not complete. If you were charged, contact support.');
            setBusy('');
            window.history.replaceState({}, '', window.location.pathname);
            return;
          }
          setTimeout(poll, 2000);
        } catch (e) {
          setErr(formatApiError(e));
          setBusy('');
        }
      };
      poll();
    }
  }, []);

  const startTrial = async () => {
    setBusy('trial'); setErr(''); setMsg('');
    try {
      await api.post('/me/trial');
      setMsg('7-day free trial started. Enjoy Pro!');
      await load();
    } catch (e) { setErr(formatApiError(e)); }
    finally { setBusy(''); }
  };

  const upgrade = async (planKey) => {
    setBusy(planKey); setErr(''); setMsg('');
    try {
      const { data } = await api.post('/me/checkout', {
        plan: planKey,
        origin_url: window.location.origin,
      });
      window.location.href = data.url;
    } catch (e) {
      setErr(formatApiError(e));
      setBusy('');
    }
  };

  const changePassword = async (e) => {
    e.preventDefault();
    setBusy('pw'); setErr(''); setMsg('');
    try {
      await api.post('/me/password', { current_password: curPw, new_password: newPw });
      setMsg('Password updated.');
      setCurPw(''); setNewPw('');
    } catch (e2) { setErr(formatApiError(e2)); }
    finally { setBusy(''); }
  };

  const saveAdminPrices = async (e) => {
    e.preventDefault();
    setBusy('admin'); setErr(''); setMsg('');
    try {
      await api.put('/admin/plan-prices', {
        monthly_price: parseFloat(monthly),
        annual_price: parseFloat(annual),
        trial_days: parseInt(trial, 10),
      });
      setMsg('Plan prices updated.');
      await load();
    } catch (e2) { setErr(formatApiError(e2)); }
    finally { setBusy(''); }
  };

  const grantPro = async (uid) => {
    const days = parseInt(grantDays[uid] || '365', 10);
    setBusy(`grant-${uid}`); setErr(''); setMsg('');
    try {
      const { data } = await api.post(`/admin/users/${uid}/grant-pro`, { days });
      setMsg(`Granted Pro to ${data.email} (+${data.days_added} days).`);
      await loadUsers(userQuery);
    } catch (e2) { setErr(formatApiError(e2)); }
    finally { setBusy(''); }
  };

  const revokePro = async (uid, email) => {
    setBusy(`revoke-${uid}`); setErr(''); setMsg('');
    try {
      await api.post(`/admin/users/${uid}/revoke-pro`);
      setMsg(`Revoked Pro from ${email}.`);
      await loadUsers(userQuery);
    } catch (e2) { setErr(formatApiError(e2)); }
    finally { setBusy(''); }
  };

  const searchUsers = (e) => {
    e.preventDefault();
    loadUsers(userQuery);
  };

  if (!sub || !plan) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="aurora-bg" /><div className="grain" />
        <div className="relative font-display text-xl text-[#8A9A92]">Loading…</div>
      </div>
    );
  }

  const proFeatures = [
    'Unlimited saved sessions',
    'φ Golden Stack harmonic mode',
    'Ambient layers (rain · ocean · forest)',
    'Breathwork guide',
    'Custom frequency generator',
  ];

  return (
    <div className="relative min-h-screen w-full overflow-x-hidden pb-20">
      <div className="aurora-bg" /><div className="grain" />

      <div className="relative z-10 max-w-4xl mx-auto px-4 lg:px-8 py-8">
        <button
          data-testid="account-back-button"
          onClick={onBack}
          className="text-[#8A9A92] hover:text-[#72C2AC] flex items-center gap-2 mb-6 transition-colors"
        >
          <ArrowLeft size={16} /> Back to player
        </button>

        <div className="mb-8">
          <div className="label-tiny mb-2">Account</div>
          <h1 className="font-display text-4xl font-light text-[#E8E3D9]">Hello, {user.name}</h1>
          <p className="text-sm text-[#8A9A92] mt-1">{user.email}</p>
        </div>

        {msg && <div data-testid="account-msg" className="glass-soft p-3 mb-4 text-sm text-[#72C2AC]">{msg}</div>}
        {err && <div data-testid="account-err" className="glass-soft p-3 mb-4 text-sm text-[#D96C6C]">{err}</div>}

        {/* Plan card */}
        <div className="glass p-6 mb-6" data-testid="plan-card">
          <div className="flex items-start justify-between mb-4">
            <div>
              <div className="label-tiny mb-1">Current Plan</div>
              <div className="flex items-center gap-3">
                <div className="font-display text-3xl text-[#E8E3D9] capitalize">
                  {sub.plan === 'trial' ? 'Pro (Trial)' : sub.plan}
                </div>
                {sub.pro && <Sparkles size={20} className="text-[#C4A67A]" />}
              </div>
              {sub.pro_until && (
                <div className="text-xs text-[#8A9A92] mt-2">
                  {sub.plan === 'trial' ? 'Trial ends' : 'Renews on'} <span className="text-[#E8E3D9]">{fmtDate(sub.pro_until)}</span>
                  {' · '}{sub.days_left} day{sub.days_left === 1 ? '' : 's'} left
                </div>
              )}
              {!sub.pro && (
                <div className="text-xs text-[#8A9A92] mt-2">Up to 3 saved sessions on Basic.</div>
              )}
            </div>
          </div>

          {!sub.pro && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
              <button
                data-testid="upgrade-monthly-button"
                onClick={() => upgrade('monthly')}
                disabled={!!busy}
                className="glass-soft p-4 text-left hover:-translate-y-0.5 transition-all border border-[#72C2AC]/20 hover:border-[#72C2AC]/60 disabled:opacity-50"
              >
                <div className="label-tiny text-[#72C2AC]">Monthly</div>
                <div className="font-mono text-2xl text-[#E8E3D9] mt-1">
                  {fmtMoney(plan.monthly.price, plan.currency)}
                  <span className="text-xs text-[#8A9A92] ml-1">/ month</span>
                </div>
                <div className="text-[11px] text-[#8A9A92] mt-2">
                  {busy === 'monthly' ? 'Redirecting…' : 'Cancel anytime'}
                </div>
              </button>
              <button
                data-testid="upgrade-annual-button"
                onClick={() => upgrade('annual')}
                disabled={!!busy}
                className="glass-soft p-4 text-left hover:-translate-y-0.5 transition-all border border-[#C4A67A]/30 hover:border-[#C4A67A]/60 disabled:opacity-50 relative"
              >
                <div className="absolute top-3 right-3 text-[9px] tracking-widest text-[#C4A67A] bg-[#C4A67A]/10 px-2 py-1 rounded-full">
                  BEST VALUE
                </div>
                <div className="label-tiny text-[#C4A67A]">Annual</div>
                <div className="font-mono text-2xl text-[#E8E3D9] mt-1">
                  {fmtMoney(plan.annual.price, plan.currency)}
                  <span className="text-xs text-[#8A9A92] ml-1">/ year</span>
                </div>
                <div className="text-[11px] text-[#8A9A92] mt-2">
                  {busy === 'annual' ? 'Redirecting…' : `Save vs ${fmtMoney(plan.monthly.price * 12, plan.currency)}`}
                </div>
              </button>

              {!sub.trial_used && (
                <button
                  data-testid="start-trial-button"
                  onClick={startTrial}
                  disabled={!!busy}
                  className="md:col-span-2 py-3 rounded-full border border-[#72C2AC]/40 text-[#72C2AC] hover:bg-[#5C9E8C]/15 transition-colors disabled:opacity-50"
                >
                  {busy === 'trial' ? <Loader2 size={14} className="inline animate-spin mr-2" /> : null}
                  Start {plan.trial_days}-day free trial — no card required
                </button>
              )}
            </div>
          )}

          {/* Feature list */}
          <div className="mt-6 pt-4 border-t border-[rgba(92,158,140,0.15)]">
            <div className="label-tiny mb-3">Pro unlocks</div>
            <ul className="space-y-1.5 text-sm">
              {proFeatures.map((f) => (
                <li key={f} className="flex items-center gap-2 text-[#E8E3D9]">
                  <Check size={14} className={sub.pro ? 'text-[#72C2AC]' : 'text-[#8A9A92]'} />
                  <span className={sub.pro ? '' : 'text-[#8A9A92]'}>{f}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Change password */}
        <div className="glass p-6 mb-6">
          <div className="flex items-center gap-2 mb-4">
            <Settings size={14} className="text-[#72C2AC]" />
            <div className="label-tiny">Change Password</div>
          </div>
          <form onSubmit={changePassword} className="space-y-3 max-w-sm">
            <input
              data-testid="current-password-input"
              type="password" required value={curPw}
              onChange={(e) => setCurPw(e.target.value)}
              placeholder="Current password"
              className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] text-sm"
            />
            <input
              data-testid="new-password-input"
              type="password" required minLength={6} value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              placeholder="New password (min 6 chars)"
              className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] text-sm"
            />
            <button
              data-testid="change-password-button"
              type="submit" disabled={busy === 'pw'}
              className="px-5 py-2 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] text-sm font-medium transition-colors disabled:opacity-50"
            >
              {busy === 'pw' ? 'Updating…' : 'Update password'}
            </button>
          </form>
        </div>

        {/* Billing history */}
        <div className="glass p-6 mb-6">
          <div className="flex items-center gap-2 mb-4">
            <Receipt size={14} className="text-[#72C2AC]" />
            <div className="label-tiny">Billing History</div>
          </div>
          {tx.length === 0 ? (
            <div className="text-xs text-[#8A9A92]">No transactions yet.</div>
          ) : (
            <div className="space-y-2">
              {tx.map((t) => (
                <div key={t.session_id} className="glass-soft p-3 flex items-center justify-between text-sm">
                  <div>
                    <div className="text-[#E8E3D9] capitalize">{t.plan} plan</div>
                    <div className="text-[11px] text-[#8A9A92]">{fmtDate(t.created_at)}</div>
                  </div>
                  <div className="text-right">
                    <div className="font-mono text-[#E8E3D9]">{fmtMoney(t.amount, t.currency)}</div>
                    <div className={`text-[11px] flex items-center gap-1 justify-end ${
                      t.payment_status === 'paid' ? 'text-[#72C2AC]' : 'text-[#8A9A92]'
                    }`}>
                      {t.payment_status === 'paid' ? <Check size={11} /> : <X size={11} />}
                      {t.payment_status}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Admin: plan prices */}
        {sub.is_admin && (
          <div className="glass p-6 border border-[#C4A67A]/30" data-testid="admin-prices-card">
            <div className="flex items-center gap-2 mb-4">
              <Sparkles size={14} className="text-[#C4A67A]" />
              <div className="label-tiny text-[#C4A67A]">Admin · Plan Prices</div>
            </div>
            <form onSubmit={saveAdminPrices} className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-2xl">
              <div>
                <label className="label-tiny block mb-1">Monthly (USD)</label>
                <input
                  data-testid="admin-monthly-price-input"
                  type="number" step="0.01" min="0.5" required
                  value={monthly} onChange={(e) => setMonthly(e.target.value)}
                  className="w-full bg-transparent border-b border-[rgba(196,166,122,0.3)] focus:border-[#C4A67A] outline-none py-2 text-[#E8E3D9] font-mono"
                />
              </div>
              <div>
                <label className="label-tiny block mb-1">Annual (USD)</label>
                <input
                  data-testid="admin-annual-price-input"
                  type="number" step="0.01" min="0.5" required
                  value={annual} onChange={(e) => setAnnual(e.target.value)}
                  className="w-full bg-transparent border-b border-[rgba(196,166,122,0.3)] focus:border-[#C4A67A] outline-none py-2 text-[#E8E3D9] font-mono"
                />
              </div>
              <div>
                <label className="label-tiny block mb-1">Trial days</label>
                <input
                  data-testid="admin-trial-days-input"
                  type="number" step="1" min="0" max="90" required
                  value={trial} onChange={(e) => setTrial(e.target.value)}
                  className="w-full bg-transparent border-b border-[rgba(196,166,122,0.3)] focus:border-[#C4A67A] outline-none py-2 text-[#E8E3D9] font-mono"
                />
              </div>
              <div className="md:col-span-3">
                <button
                  data-testid="admin-save-prices-button"
                  type="submit" disabled={busy === 'admin'}
                  className="px-5 py-2 rounded-full bg-[#C4A67A] hover:bg-[#d6b88c] text-[#08120F] text-sm font-medium transition-colors disabled:opacity-50"
                >
                  {busy === 'admin' ? 'Saving…' : 'Save prices'}
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Admin: user management */}
        {sub.is_admin && (
          <div className="glass p-6 border border-[#C4A67A]/30 mt-6" data-testid="admin-users-card">
            <div className="flex items-center gap-2 mb-4">
              <Users size={14} className="text-[#C4A67A]" />
              <div className="label-tiny text-[#C4A67A]">Admin · User Management</div>
            </div>

            <form onSubmit={searchUsers} className="flex items-center gap-2 mb-4 max-w-md">
              <Search size={14} className="text-[#8A9A92]" />
              <input
                data-testid="admin-user-search-input"
                value={userQuery}
                onChange={(e) => setUserQuery(e.target.value)}
                placeholder="Search by email…"
                className="flex-1 bg-transparent border-b border-[rgba(196,166,122,0.3)] focus:border-[#C4A67A] outline-none py-2 text-[#E8E3D9] text-sm"
              />
              <button
                data-testid="admin-user-search-button"
                type="submit"
                className="text-[11px] text-[#C4A67A] hover:text-[#72C2AC] px-3 py-1 transition-colors"
              >
                Search
              </button>
            </form>

            {users.length === 0 ? (
              <div className="text-xs text-[#8A9A92]">No users found.</div>
            ) : (
              <div className="space-y-2 max-h-[420px] overflow-y-auto custom-scrollbar pr-1">
                {users.map((u) => (
                  <div
                    key={u.id}
                    data-testid={`admin-user-row-${u.id}`}
                    className="glass-soft p-3 flex flex-col md:flex-row md:items-center md:justify-between gap-3"
                  >
                    <div className="min-w-0">
                      <div className="text-sm text-[#E8E3D9] truncate">{u.name || '—'}</div>
                      <div className="text-[11px] text-[#8A9A92] truncate">{u.email}</div>
                      <div className="text-[10px] mt-1 flex items-center gap-2">
                        <span className={`px-1.5 py-0.5 rounded-full ${
                          u.pro ? 'bg-[#C4A67A]/20 text-[#C4A67A]' : 'bg-[#1A332A]/60 text-[#8A9A92]'
                        }`}>
                          {u.pro ? `PRO · ${u.days_left}d` : 'BASIC'}
                        </span>
                        {u.role === 'admin' && (
                          <span className="px-1.5 py-0.5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC]">ADMIN</span>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 flex-shrink-0">
                      <input
                        data-testid={`grant-days-${u.id}`}
                        type="number" min="1" max="3650"
                        value={grantDays[u.id] ?? '365'}
                        onChange={(e) => setGrantDays({ ...grantDays, [u.id]: e.target.value })}
                        className="w-16 bg-transparent border-b border-[rgba(196,166,122,0.3)] focus:border-[#C4A67A] outline-none py-1 text-[#E8E3D9] text-xs font-mono text-center"
                        title="Days to add"
                      />
                      <span className="text-[10px] text-[#8A9A92]">days</span>
                      <button
                        data-testid={`grant-pro-${u.id}`}
                        onClick={() => grantPro(u.id)}
                        disabled={busy === `grant-${u.id}`}
                        className="px-3 py-1.5 rounded-full bg-[#C4A67A]/20 hover:bg-[#C4A67A]/40 border border-[#C4A67A]/40 text-[#C4A67A] text-[11px] transition-colors disabled:opacity-50"
                      >
                        {busy === `grant-${u.id}` ? '…' : (u.pro ? 'Extend' : 'Grant Pro')}
                      </button>
                      {u.pro && (
                        <button
                          data-testid={`revoke-pro-${u.id}`}
                          onClick={() => revokePro(u.id, u.email)}
                          disabled={busy === `revoke-${u.id}`}
                          className="px-3 py-1.5 rounded-full border border-[#D96C6C]/40 text-[#D96C6C] text-[11px] hover:bg-[#D96C6C]/10 transition-colors disabled:opacity-50"
                        >
                          {busy === `revoke-${u.id}` ? '…' : 'Revoke'}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
