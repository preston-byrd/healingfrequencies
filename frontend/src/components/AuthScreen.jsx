import React, { useState, useEffect, useRef } from 'react';
import { RefreshCw, Eye, EyeOff, ArrowLeft, ShieldCheck, PhoneCall } from 'lucide-react';
import PhoneInput, { isValidPhoneNumber } from 'react-phone-number-input';
import 'react-phone-number-input/style.css';
import { useAuth } from '@/contexts/AuthContext';
import api, { formatApiError, warmBackend } from '@/lib/api';
import ForgotPasswordModal from '@/components/ForgotPasswordModal';
import { LOGIN } from '@/constants/testIds/auth';

// Axios throws either `Network Error` (browser refused / DNS / TLS died) or
// aborts the request with `ECONNABORTED` when the 45-second timeout hits.
// Both mean "the server was never reached", i.e. safe to retry with the
// same body — as opposed to a 401 (wrong password) or 429 (rate-limited)
// where retrying is the wrong move. Only these two surfaces the Retry chip.
function isNetworkError(err) {
  if (!err) return false;
  if (err.response) return false;
  if (err.code === 'ECONNABORTED') return true;
  if (err.message === 'Network Error') return true;
  return false;
}

// Signup uses a two-step flow so we can verify the phone number before
// committing the account:
//   'form'   → user fills email / password / name / phone
//   'verify' → user enters 6-digit SMS OTP; on success we complete
//              /auth/register with the phone_verification_token
const REGISTER_STEP_FORM = 'form';
const REGISTER_STEP_VERIFY = 'verify';

export default function AuthScreen() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [err, setErr] = useState('');
  const [canRetry, setCanRetry] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showForgot, setShowForgot] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // Signup 2-step wizard state.
  const [registerStep, setRegisterStep] = useState(REGISTER_STEP_FORM);
  const [otpCode, setOtpCode] = useState('');
  const [phoneVerificationToken, setPhoneVerificationToken] = useState('');
  const [verifiedPhone, setVerifiedPhone] = useState(''); // exactly-as-sent E.164
  // Resend cooldown — Twilio Verify already throttles at the API layer,
  // but a client-side cooldown prevents the user from mashing "Resend"
  // and racking up 429s. 45s is the sweet spot for SMS delivery jitter.
  const [resendCooldown, setResendCooldown] = useState(0);
  const cooldownTimerRef = useRef(null);
  // Voice-call fallback: when Twilio Lookup rejects SMS for a given number
  // (error 60200 / 60205 / 60600) the backend replies with
  // `detail.can_retry_by_call = true`. We surface a "Get a phone call
  // instead" chip so the user isn't stranded on a valid-but-SMS-hostile
  // number (landlines, some VoIP carriers, stale carrier DB entries).
  const [canRetryByCall, setCanRetryByCall] = useState(false);
  // Which channel the OTP was ultimately delivered through — used to
  // customise the verify-step subhead ("we called you" vs "we texted you").
  const [otpChannel, setOtpChannel] = useState('sms');

  useEffect(() => {
    warmBackend();
  }, []);

  // Cooldown ticker.
  useEffect(() => {
    if (resendCooldown <= 0) return undefined;
    cooldownTimerRef.current = setTimeout(() => setResendCooldown((s) => s - 1), 1000);
    return () => clearTimeout(cooldownTimerRef.current);
  }, [resendCooldown]);

  const resetSignupWizard = () => {
    setRegisterStep(REGISTER_STEP_FORM);
    setOtpCode('');
    setPhoneVerificationToken('');
    setVerifiedPhone('');
    setResendCooldown(0);
    setCanRetryByCall(false);
    setOtpChannel('sms');
  };

  const attemptLogin = async () => {
    setErr('');
    setCanRetry(false);
    setBusy(true);
    try {
      await login(email, password);
    } catch (e) {
      setErr(formatApiError(e));
      setCanRetry(isNetworkError(e));
    } finally {
      setBusy(false);
    }
  };

  // Step 1: user filled the form → send OTP via SMS (default) or voice call.
  // The optional `channel` argument lets us reuse this from the "Get a
  // phone call instead" fallback after a Twilio SMS Lookup rejection.
  const attemptSendOtp = async (channel = 'sms') => {
    setErr('');
    setCanRetry(false);
    setCanRetryByCall(false);
    // Client-side format check up front so we don't waste a Twilio API
    // call on obviously-broken input.
    if (!phone || !isValidPhoneNumber(phone)) {
      setErr("Please enter a valid phone number including country code.");
      return;
    }
    if (!email || !password) {
      setErr("Please fill in every field.");
      return;
    }
    if (password.length < 8) {
      setErr("Password must be at least 8 characters.");
      return;
    }
    setBusy(true);
    try {
      await api.post('/auth/phone/send-code', { phone_number: phone, channel });
      setVerifiedPhone(phone);
      setOtpChannel(channel);
      setRegisterStep(REGISTER_STEP_VERIFY);
      setResendCooldown(45);
    } catch (e) {
      setErr(formatApiError(e));
      setCanRetry(isNetworkError(e));
      // Detect the structured retry-hint from `/auth/phone/send-code`.
      const hint = e?.response?.data?.detail;
      if (hint && typeof hint === 'object' && hint.can_retry_by_call) {
        setCanRetryByCall(true);
      }
    } finally {
      setBusy(false);
    }
  };

  // Step 2a: user entered OTP → verify.
  const attemptVerifyOtp = async () => {
    setErr('');
    if (!otpCode || otpCode.replace(/\s/g, '').length < 4) {
      setErr("Please enter the code from your text message.");
      return;
    }
    setBusy(true);
    try {
      const { data } = await api.post('/auth/phone/verify-code', {
        phone_number: verifiedPhone,
        code: otpCode.trim(),
      });
      setPhoneVerificationToken(data.phone_verification_token);
      // Now finalize registration with the freshly-minted proof.
      await register(email, password, name, {
        phone_number: verifiedPhone,
        phone_verification_token: data.phone_verification_token,
      });
      // AuthContext will flip user, App unmounts this component.
    } catch (e) {
      setErr(formatApiError(e));
      setCanRetry(isNetworkError(e));
    } finally {
      setBusy(false);
    }
  };

  // Step 2b: user asked for a new code — reuse whichever channel we
  // successfully sent on the first pass so the UX stays coherent.
  const attemptResendOtp = async () => {
    if (resendCooldown > 0 || busy) return;
    setErr('');
    setBusy(true);
    try {
      await api.post('/auth/phone/send-code', {
        phone_number: verifiedPhone, channel: otpChannel,
      });
      setResendCooldown(45);
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const submit = (e) => {
    e.preventDefault();
    if (mode === 'login') return attemptLogin();
    if (registerStep === REGISTER_STEP_FORM) return attemptSendOtp();
    return attemptVerifyOtp();
  };

  const heading = (() => {
    if (mode === 'login') return 'Welcome back';
    if (registerStep === REGISTER_STEP_VERIFY) return 'Verify your phone';
    return 'Begin your journey';
  })();
  const subhead = (() => {
    if (mode === 'login') return 'Tune in. Settle down. Resonate.';
    if (registerStep === REGISTER_STEP_VERIFY) {
      const last4 = verifiedPhone.slice(-4);
      const delivery = otpChannel === 'call'
        ? `We're calling the number ending in ${last4} with your 6-digit code`
        : `We sent a code to the number ending in ${last4}`;
      return `${delivery}. Enter it below to finish creating your account.`;
    }
    return 'A quiet space to listen, breathe, and restore.';
  })();

  return (
    <div className="min-h-screen flex items-center justify-center relative px-4">
      <div className="aurora-bg" />
      <div className="grain" />
      <div className="relative z-10 w-full max-w-md glass p-10">
        <div className="text-center mb-8">
          <div className="label-tiny mb-3">Healing Frequencies</div>
          <h1 className="font-display text-5xl font-light tracking-tight text-[#E8E3D9]">
            {heading}
          </h1>
          <p className="text-[#8A9A92] mt-3 text-sm">
            {subhead}
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4">
          {mode === 'register' && registerStep === REGISTER_STEP_FORM && (
            <>
              <div>
                <label className="label-tiny block mb-2">Name</label>
                <input
                  data-testid="auth-name-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
                  placeholder="Your name"
                />
              </div>
              <div>
                <label className="label-tiny block mb-2">Email</label>
                <input
                  data-testid="auth-email-input"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
                  placeholder="you@example.com"
                />
              </div>
              <div>
                <label className="label-tiny block mb-2">Password</label>
                <div className="relative">
                  <input
                    data-testid="auth-password-input"
                    type={showPassword ? 'text' : 'password'}
                    required
                    minLength={8}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 pr-9 text-[#E8E3D9] transition-colors"
                    placeholder="At least 8 characters"
                  />
                  <button
                    type="button"
                    data-testid="auth-password-toggle"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    aria-pressed={showPassword}
                    tabIndex={-1}
                    className="absolute right-1 top-1/2 -translate-y-1/2 p-1.5 text-[#8A9A92] hover:text-[#C9DED6] transition-colors"
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>
              <div>
                <label className="label-tiny block mb-2">Phone number</label>
                <div className="phone-input-wrap">
                  <PhoneInput
                    data-testid="auth-phone-input"
                    international
                    defaultCountry="US"
                    value={phone}
                    onChange={(v) => setPhone(v || '')}
                    placeholder="+1 415 555 2671"
                    className="w-full"
                  />
                </div>
                <p className="text-[10px] text-[#8A9A92] mt-1.5 leading-relaxed">
                  We'll text you a 6-digit code to confirm this number. Standard
                  message rates apply. We never share your number.
                </p>
              </div>
            </>
          )}

          {mode === 'register' && registerStep === REGISTER_STEP_VERIFY && (
            <>
              <div>
                <label className="label-tiny block mb-2">Verification code</label>
                <input
                  data-testid="auth-otp-input"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  required
                  maxLength={10}
                  value={otpCode}
                  onChange={(e) => setOtpCode(e.target.value.replace(/[^\d]/g, ''))}
                  className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] text-2xl tracking-[0.4em] font-mono text-center transition-colors"
                  placeholder="••••••"
                />
                <div className="mt-3 flex items-center justify-between text-xs text-[#8A9A92]">
                  <button
                    type="button"
                    data-testid="auth-otp-back"
                    onClick={() => { resetSignupWizard(); setErr(''); }}
                    className="inline-flex items-center gap-1 hover:text-[#72C2AC] transition-colors"
                  >
                    <ArrowLeft size={12} />
                    Change number
                  </button>
                  <button
                    type="button"
                    data-testid="auth-otp-resend"
                    onClick={attemptResendOtp}
                    disabled={resendCooldown > 0 || busy}
                    className="hover:text-[#72C2AC] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {resendCooldown > 0 ? `Resend in ${resendCooldown}s` : 'Resend code'}
                  </button>
                </div>
              </div>
            </>
          )}

          {mode === 'login' && (
            <>
              <div>
                <label className="label-tiny block mb-2">Email</label>
                <input
                  data-testid="auth-email-input"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
                  placeholder="you@example.com"
                />
              </div>
              <div>
                <label className="label-tiny block mb-2">Password</label>
                <div className="relative">
                  <input
                    data-testid="auth-password-input"
                    type={showPassword ? 'text' : 'password'}
                    required
                    minLength={6}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 pr-9 text-[#E8E3D9] transition-colors"
                    placeholder="•••••••"
                  />
                  <button
                    type="button"
                    data-testid="auth-password-toggle"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    aria-pressed={showPassword}
                    tabIndex={-1}
                    className="absolute right-1 top-1/2 -translate-y-1/2 p-1.5 text-[#8A9A92] hover:text-[#C9DED6] transition-colors"
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>
            </>
          )}

          {err && (
            <div className="space-y-2" data-testid="auth-error-block">
              <div data-testid="auth-error" className="text-[#D96C6C] text-sm">{err}</div>
              {canRetry && (
                <button
                  type="button"
                  onClick={() => {
                    if (mode === 'login') return attemptLogin();
                    if (registerStep === REGISTER_STEP_FORM) return attemptSendOtp();
                    return attemptVerifyOtp();
                  }}
                  disabled={busy}
                  data-testid="auth-retry-button"
                  className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.14em] text-[#72C2AC] hover:text-[#C4A67A] transition-colors disabled:opacity-40"
                >
                  <RefreshCw size={12} className={busy ? 'animate-spin' : ''} />
                  {busy ? 'Retrying…' : 'Retry'}
                </button>
              )}
              {canRetryByCall && mode === 'register' && registerStep === REGISTER_STEP_FORM && (
                <button
                  type="button"
                  onClick={() => attemptSendOtp('call')}
                  disabled={busy}
                  data-testid="auth-retry-by-call"
                  className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.14em] text-[#C4A67A] hover:text-[#72C2AC] transition-colors disabled:opacity-40"
                >
                  <PhoneCall size={12} />
                  {busy ? 'Calling…' : 'Get a phone call instead'}
                </button>
              )}
            </div>
          )}

          <button
            data-testid="auth-submit-button"
            type="submit"
            disabled={busy}
            className="w-full mt-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors duration-300 disabled:opacity-50 inline-flex items-center justify-center gap-2"
          >
            {busy ? 'One moment…'
              : mode === 'login' ? 'Sign in'
              : registerStep === REGISTER_STEP_FORM ? 'Send verification code'
              : (<><ShieldCheck size={16} /> Verify &amp; create account</>)}
          </button>
        </form>

        {mode === 'login' && (
          <div className="mt-4 text-center text-sm">
            <button
              data-testid={LOGIN.forgotPasswordLink}
              type="button"
              onClick={() => { setErr(''); setShowForgot(true); }}
              className="text-[#8A9A92] hover:text-[#72C2AC] transition-colors"
            >
              Forgot your password?
            </button>
          </div>
        )}

        <div className="mt-6 text-center text-sm text-[#8A9A92]">
          {mode === 'login' ? "New here?" : 'Already have an account?'}{' '}
          <button
            data-testid="auth-mode-toggle"
            type="button"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login');
              setErr('');
              resetSignupWizard();
            }}
            className="text-[#72C2AC] hover:text-[#C4A67A] transition-colors"
          >
            {mode === 'login' ? 'Create an account' : 'Sign in'}
          </button>
        </div>
      </div>

      {showForgot && (
        <ForgotPasswordModal
          initialEmail={email}
          onClose={() => setShowForgot(false)}
        />
      )}

      {/* Themed overrides for react-phone-number-input to match the rest
          of the auth form (transparent + underline). Kept scoped here so
          the raw library CSS still ships default styles elsewhere. */}
      <style>{`
        .phone-input-wrap .PhoneInput {
          display: flex;
          align-items: center;
          gap: 8px;
          border-bottom: 1px solid rgba(92,158,140,0.25);
          padding: 8px 0;
          transition: border-color 0.2s;
        }
        .phone-input-wrap .PhoneInput:focus-within {
          border-bottom-color: #72C2AC;
        }
        .phone-input-wrap .PhoneInputCountry {
          display: flex;
          align-items: center;
          gap: 4px;
          color: #E8E3D9;
        }
        .phone-input-wrap .PhoneInputInput {
          flex: 1;
          background: transparent;
          border: none;
          color: #E8E3D9;
          outline: none;
          font-size: 1rem;
          padding: 0;
        }
        .phone-input-wrap .PhoneInputCountrySelect {
          color: #E8E3D9;
          background: transparent;
        }
        .phone-input-wrap .PhoneInputCountrySelectArrow {
          color: #8A9A92;
        }
      `}</style>
    </div>
  );
}
