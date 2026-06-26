# PRD — Healing Frequencies

## Original problem statement
"Can you create healing frequencies"

## User choices (from initial ask_human)
- Both: preset Solfeggio + custom frequency generator
- Features: timer/session length, background visuals, ambient nature sounds layer mixed with tones, save favorite sessions, breathwork guide synced to tones
- Pure Web Audio API (no audio files)
- Design vibe: blend of calm/minimal, cosmic/mystical, nature/earthy
- Auth required (JWT email + password)

## Architecture
- **Backend:** FastAPI + Motor (async MongoDB). JWT auth in httpOnly cookies (SameSite=None, Secure) with Authorization Bearer fallback.
- **Frontend:** React 19 (CRA + craco). Tailwind CSS, lucide-react icons. Canvas-based visualizer.
- **Audio:** Web Audio API singleton engine. Oscillator for tone (sine/triangle/square/sawtooth) + optional binaural offset via channel-split. Three ambient layers (rain/ocean/forest) generated from pink-ish noise + biquad filters (low-pass for ocean with LFO, high-pass for rain, band-pass for forest).
- **DB collections:** `users` (id, email, name, password_hash, role, created_at), `sessions` (id, user_id, name, frequency, waveform, binaural, duration_minutes, ambient, breathwork, created_at).

## User personas
- Wellness seeker who opens the app for a quick guided sound-bath / meditation session.
- Practitioner / power user who composes custom mixes (frequency + binaural + ambient layers) and saves them.

## Core requirements (static)
- Email/password auth (register, login, logout, /me)
- Solfeggio preset grid (174, 285, 396, 417, 432, 528, 639, 741, 852, 963 Hz)
- Custom frequency slider 20–1200 Hz with waveform selector
- Binaural offset 0–40 Hz
- Tone volume control
- Ambient mixer (rain / ocean / forest) with independent volume
- Session timer 1–60 min with on-screen countdown
- Animated visualizer (canvas; pulses with frequency + play state)
- Breathwork guide overlay (4-4-6 cycle, phase label)
- Saved sessions (CRUD scoped to logged-in user) with one-click load

## Implemented (Jan 2026)
- All core requirements above
- Aurora/grain ambient backdrop, glassmorphism panels, custom sliders (teal for tone, amber for ambient)
- JWT cookie + Bearer fallback (so cross-origin works seamlessly)
- Admin seeded automatically on startup (admin@example.com / admin123)
- **Daily check-in streak** (Jan 2026): `/api/streak` + `/api/streak/checkin`. Auto check-in after ≥60s of continuous playback. Streak/longest/total-min displayed in left sidebar.
- **Gentle daily reminder** via Web Notifications API. User opts in, picks a time, and a daily notification is scheduled in-browser ("A quiet moment awaits 🌿").
- **Golden Ratio (φ) Stack** (Jan 2026): dedicated preset (144 · 233 · 377 Hz Fibonacci chord) + toggle that stacks φ¹ and φ² harmonics on any base frequency.
- **PWA** (Jan 2026): manifest, service worker for offline shell, app icons, installable on iOS/Android/desktop.
- **Subscription & billing** (Jan 2026): Basic (free, 3-session cap) + Pro Monthly ($9.99/mo) + Pro Annual ($60/yr). 7-day free trial. Stripe Checkout via emergentintegrations. `/api/me/checkout`, `/api/payments/status/{id}`, `/api/webhook/stripe`. Pro unlocks Golden Stack, ambient layers, breathwork, custom freq generator, unlimited saves.
- **Account dashboard** (Jan 2026): change password, current plan + days left, upgrade flow, billing history, admin price editor.
- **Admin user management** (Jan 2026): `/api/admin/users` list + search by email; `/api/admin/users/{id}/grant-pro` (extend from existing pro_until if active); `/api/admin/users/{id}/revoke-pro`; **DELETE /api/admin/users/{id}** (cascade-deletes sessions + streaks + transactions; cannot delete self or other admins). UI in AccountDashboard with per-row days input, Grant/Extend + Revoke + Delete buttons.
- **Admin = lifetime Pro** (Jan 2026): admins automatically bypass paywall (sub.pro=true, plan=admin). Self-healing admin seeding always sets role=admin on restart.
- **More frequencies** (Jan 2026): 10 new "Brainwave & Specials" presets (Delta 2 Hz, Theta 6 Hz, Schumann 7.83 Hz, Alpha 10 Hz, Gamma 40 Hz, 111, 222, Tesla 369, Angel 444, 1111 Hz). Custom freq slider now 1–1200 Hz (was 20–1200).
- **More ambient layers** (Jan 2026): 5 new layers added (wind, crickets, singing bowls, brown noise, white noise) → 8 total. Each generated live via filtered noise + per-layer LFO modulation.
- **Ocean-at-0% bug fix** (Jan 2026): refactored ambient audio graph so the LFO modulates an inner `modGain` node, never the user-controlled `userGain`. `setAmbient(kind, 0)` now does `cancelScheduledValues` + `setValueAtTime(0)` for an instant hard mute.
- **Sleep Mode** (Jan 2026): one-tap preset = 4 Hz Theta–Delta + brown noise @ 45% + 30-min timer with 60-second linear fade-to-silence on tone AND every ambient gain. Auto-clear gated on `remaining === 0` (avoids race with deferred audio start). Pro-gated.
- **Brainwave & Specials Pro paywall** (Jan 2026): all 10 special-freq presets (Delta, Theta, Schumann, Alpha, Gamma, 111, 222, Tesla 369, Angel 444, 1111) are now Pro-gated via `selectFrequency(hz, {special:true})`. Locked-state UI: Lock icon next to title, PRO badge, dimmed grid + pointer-events disabled, centered "Included in Pro" CTA. Admin and Pro users get unrestricted access.
- **Audio reliability fix** (Jan 2026): `_ensureCtx()` async + awaits `ctx.resume()`; `start()` async with `_starting` guard; `stop()` uses local osc refs + immediate null of instance refs (race-window safe); window-level first-gesture unlock listener (`pointerdown/touchstart/keydown`) → audioEngine.unlock() for iOS Safari.
- Backend + frontend tested end-to-end (testing agent iterations 1–9: 100% pass)

## Implemented (Feb 2026)
- **Tap-to-toggle frequency** (Feb 2026): Tapping any frequency button (Solfeggio / Brainwave & Specials / φ Golden Stack) while that same selection is currently playing now STOPS playback. Tapping a DIFFERENT frequency while playing live-retunes (does not stop). Implemented in `Dashboard.jsx::selectFrequency` via `sameFreq` (`Math.abs(audioEngine.frequency - hz) < 0.05`) + `sameMode` (matching Golden-Stack state).
- **Code quality cleanup** (Feb 2026): Memoized `AuthContext` + `SubscriptionContext` provider values with `useMemo` / `useCallback` (prevents unnecessary consumer re-renders). Replaced silent `catch {}` blocks with `console.warn(...)` logging in `Dashboard.jsx`, `AccountDashboard.jsx`, and both contexts. Backend pytest admin credentials are now env-driven (`ADMIN_TEST_EMAIL` / `ADMIN_TEST_PASSWORD` with safe defaults). `audioEngine.js` left intentionally untouched (its noop catches handle iOS unlock + cleanup races).
- Testing: iteration 14 — 12/12 frontend scenarios pass, 44/47 pytest pass (2 pre-existing stale checkout-tx tests, 1 intentional skip).

## Implemented (Feb 2026 — cont.)
- **Edit user name** (Feb 2026): Users can now edit their display name from AccountDashboard. New endpoint `PUT /api/me/profile` with Pydantic `ProfileUpdateIn` (min_length=1, max_length=80, whitespace-trim). `AuthContext` exposes `setUserName(name)` so the in-memory user object updates immediately and propagates to the Dashboard sidebar greeting. Inline edit UI with Edit / Save / Cancel and auto-focus on the input. Testing iteration 15: 6/6 backend + 10/10 frontend scenarios pass.

- **Stripe checkout — surface real errors instead of silent fail** (Feb 2026): When a user replaces `sk_test_emergent` with their own `sk_live_...` or real `sk_test_...` key, the emergentintegrations library correctly routes to `api.stripe.com` (per library L107-109). If Stripe rejects the key (invalid / restricted account / unsupported currency), `/api/me/checkout` now catches the exception, logs the traceback, and returns HTTP 502 with the underlying message (e.g., `"Stripe checkout failed: Invalid API Key provided..."`). Backend also rejects empty `session.url` responses with 502. Frontend `upgrade()` in `AccountDashboard.jsx` guards against `undefined data.url` and scrolls to top on any error so the small `account-err` div is visible. Testing iteration 16: 6/6 backend + 5/5 frontend scenarios pass.

- **Stale pytest cleanup + Thank-You celebration + Persisted last-used config** (Feb 2026): Three features shipped together.
  - Removed 2 stale `test_checkout_{monthly,annual}_creates_transaction` tests from `test_subscription.py` (superseded by `test_billing_history.py`); also removed unused `import time`.
  - NEW component `ThankYouCelebration.jsx` — cosmic celebration overlay with 36 CSS-only confetti particles + 3 pulsing rings + a φ glyph. Fires from `AccountDashboard` payment-status polling when `payment_status === 'paid'`. CTA "Start your first Pro session" closes the modal and returns to the player. Plan label reads "Pro Monthly" / "Pro Annual" based on the original tx.plan (added to `/api/payments/status/{sid}` response).
  - NEW endpoints `GET /api/me/prefs` and `PUT /api/me/prefs` with Pydantic-validated `PrefsIn` (frequency 0.1-20000, duration 1-180, waveform sine/triangle/square/sawtooth, binaural 0-40, tone_volume 0-1, ambient as Dict[str, float], golden_stack/breathwork booleans). Stored in `users.prefs` subdoc with dotted-path `$set` merge so partial PUTs preserve other fields. Backend defense-in-depth: PUT silently strips Pro-only fields (`golden_stack`, `breathwork`, `binaural`, `ambient`) when the user isn't Pro, so a stale UI state can't clobber saved Pro values after a downgrade.
  - Dashboard.jsx restores prefs on mount (waits for `/me/subscription` to resolve before evaluating `isPro`, then runs exactly once via `restoreStartedRef`). Debounced auto-save (1.2s) fires on any knob change; payload omits Pro-only fields when `isPro=false`. Auto-save effect deps include `isPro` so it re-runs immediately if the user upgrades mid-session.
  - Testing iterations 17/18/19 — converged on full pass: backend 24/24 (15 prefs + 8 subscription + 1 payments-status), frontend 100% on the full upgrade flow (saved Pro knobs survive logout/login + restore correctly after trial upgrade). Two race conditions caught and fixed: (i) iter-17 stale `isPro` closure in restore effect, (ii) iter-18 auto-save clobber of Pro-only fields with React defaults for non-Pro users.

## Backlog (P1 → P2)
- P1: Persisted "last used config" auto-restore on login
- P1: A/B switch between equal-temperament and Verdi-A=432 reference
- P2: Optional sleep timer (fade-out over N min)
- P2: Share a session config via short URL
- P2: Real recorded ambient tracks (rain/ocean/forest) for premium quality (would need licensed assets)
- P2: PWA install + offline mode + service-worker-driven reminders (for when tab is closed)

## Next tasks (suggested)
1. Hook "last used config" into localStorage (no backend change needed)
2. Add A/B switch for 432Hz reference
3. Daily streak counter (new MongoDB collection)
