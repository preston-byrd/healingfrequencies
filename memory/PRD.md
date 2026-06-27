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

- **4-payment-method upgrade flow** (Feb 2026): Apple Pay / Google Pay / Card / Payment Link, with device-aware capability detection.
  - Architectural insight: a Stripe Checkout Session URL IS a sharable cross-device URL. So all 4 methods hit the SAME `/api/me/checkout` endpoint and use the SAME Stripe session. Apple Pay / Google Pay / Card redirect this device to Stripe Checkout (which auto-renders Apple Pay / Google Pay buttons inside its hosted page when the browser supports them). Payment Link opens a modal with a QR code (`qrcode.react`) + copy-to-clipboard + open-in-new-tab — same URL, different presentation.
  - NEW `usePaymentMethodSupport.js` hook detects wallets: Apple Pay via `window.ApplePaySession.canMakePayments()`, Google Pay via the `PaymentRequest` API with `google.com/pay` supportedMethods. Returns `{applePay, googlePay, ready}`; AP/GP buttons hidden when unsupported, "wallet-unavailable-hint" shown when both fail.
  - NEW `PaymentLinkModal.jsx` — QR (M error correction, 188px), URL input with auto-select-on-click, copy-with-clipboard-fallback, open-in-new-tab anchor (target=_blank), outside-click + X-button close.
  - Backend `CheckoutIn` gained `payment_method_preference: card|apple_pay|google_pay|link` (Pydantic regex pattern, defaults to 'card'). Forwarded to Stripe metadata for analytics; does NOT change the Stripe call.
  - AccountDashboard upgrade card refactored: 2-card plan selector (Monthly/Annual with aria-pressed) + 4-button payment-methods grid below.
  - Testing iteration 20 — 38/38 backend (12 new payment_methods tests + full regression) + 13/13 frontend e2e (plan switch, wallet hide+hint, link modal w/ QR/copy/open/close, annual title, card-redirect). Zero defects.

- **Stripe blank-checkout production bug — root caused and fixed** (Feb 2026): User reported Stripe Checkout loads a blank skeleton page in production with their `sk_live_...` key. RCA: the `emergentintegrations` library mutates the module-level `stripe.api_base` singleton when the key contains `sk_test_emergent` (lib L107-109) but NEVER resets it for non-emergent keys. Because Python module state persists for the entire uvicorn worker lifetime, ANY prior `sk_test_emergent` call (a startup probe, a previous deployment, or a brief `.env` placeholder) leaves `stripe.api_base` stuck pointing at Emergent's proxy. Subsequent `sk_live_` calls then create Checkout Sessions on Emergent's proxy account → returned session URLs reference IDs that don't exist in the user's real Stripe account → Stripe Checkout fetches and fails silently → blank page.
  - FIX: new helper `_stripe_client(webhook_url)` in `server.py` L455-475 that explicitly normalises `stripe.api_base` BEFORE every `StripeCheckout` instantiation — Emergent's proxy iff key contains `sk_test_emergent`, else `https://api.stripe.com`. All 3 call sites (`POST /me/checkout`, `GET /payments/status/{sid}`, `POST /webhook/stripe`) refactored to use it.
  - Added diagnostic `logger.info` line in `/me/checkout` printing the resolved `api_base` + `key_prefix` for operator-visibility in `/var/log/supervisor/backend.err.log`.
  - Testing iteration 21 — 9 new sticky-state guard tests in `test_stripe_routing.py` (4 unit monkeypatch tests proving deterministic api_base recovery + 4 live HTTP regression + 1 log-line assertion) + 38 regression. All 47/47 pass.

- **Soundscape playback — graceful fade + tap-to-toggle + pause/resume** (Feb 2026): Comprehensive overhaul of the Soundscape player flow.
  - `audioEngine.setAmbient(k, 0)` no longer instant-cuts; it now `cancelScheduledValues` + `setValueAtTime(current)` + `linearRampToValueAtTime(0, +0.8s)` — every ambient stop is smooth, whether triggered by slider drag, soundscape swap, or session stop.
  - `audioEngine.stop()` now snapshots the active ambient mix to `_pendingAmbient`, fades ALL ambient gains to 0 over 0.8s (matched to the tone fade), and uses an 850ms oscillator cleanup window. Snapshot is restored automatically in `start()` so Pause → Play resumes the exact mix — including frequency, ambient layers, and tone volume.
  - Dashboard.jsx added `activeSoundscape` state with three-branch `selectSoundscape` logic: same+playing → graceful stop (snapshot cleared), same+paused → resume (snapshot restored via `start()`), different → discard snapshot + swap mix + start (no silent gap). All explicit-reset paths (manual ambient slider, frequency tap, sleep mode, timer expiry) clear both `_pendingAmbient` and `activeSoundscape` so the next action starts fresh.
  - Visual: active soundscape card shows ring + amber icon + 'PLAYING' badge (animated pulse dot) or 'PAUSED' badge + 'Tap to stop' / 'Tap to resume' hint. `data-active` and `data-playing` attributes on each card for testability.
  - Testing iteration 22 — 9/9 frontend e2e scenarios pass (5 primary + 3 regression + 1 mobile). Fade trajectory verified live on Web Audio gain values: t=50ms=0.49 → t=400ms=0.25 → t=900ms=0.00, byte-identical on desktop (1280×900) and mobile (390×844). Testing agent added `window.__audioEngine` hook in Dashboard.jsx for live engine introspection — harmless in production.

- **Subscription model + Cloudflare timeout protection** (Feb 2026): Migrated Pro from one-time payments to true Stripe Subscriptions with a 7-day trial that requires card upfront. Two-bug fix in one release.
  - **Cloudflare 520 origin error**: every Stripe SDK call is now wrapped in `asyncio.wait_for(asyncio.to_thread(...), timeout=25)` via new `_stripe_call` helper. Guarantees a JSON HTTP 502 response within 25s — Cloudflare's 100s threshold is never approached. New `_normalise_stripe_api_base()` rebinds `stripe.api_base` per request (defense against iter-21 sticky-state regressions).
  - **Trial-requires-card**: `/me/checkout` now creates `mode='subscription'` Stripe Checkout Sessions with `subscription_data.trial_period_days=7` (gated by `user.trial_used` — Stripe rejects double-trials). New `_get_or_create_stripe_customer` ensures a stable Customer ID before checkout. After successful session: `/payments/status/{sid}` retrieves the Subscription and projects status / `current_period_end` / `trial_end` onto the user via new `_sync_subscription_to_user` helper. Source of truth is now Stripe.
  - **Webhook lifecycle** — multiplexes `checkout.session.completed`, `customer.subscription.{created,updated,deleted}`, `invoice.payment_{succeeded,failed}`. Unsigned-fallback parsing for environments without `STRIPE_WEBHOOK_SECRET`. Failed payments flag `user.payment_failed_at`; successful payments clear it.
  - **Cancellation** — Stripe Customer Portal (user choice (a)): new `POST /me/billing-portal` returns a portal URL for self-service (cancel, update card, view invoices). Backup `POST /me/cancel-subscription` performs one-click `cancel_at_period_end=true` cancellation for in-app UX.
  - **Legacy `/me/trial`** — now returns HTTP 410 with a clear redirection message pointing clients at `/me/checkout`.
  - **`/me/subscription`** — gains 6 new fields: `stripe_subscription_status`, `in_trial`, `trial_end`, `cancel_at_period_end`, `has_billing_portal`, `payment_failed_at`. Drives all the new UI banners.
  - **Frontend** — removed "no card required" trial CTA. New `[data-testid=trial-billing-info]` card explains: *"First 7 days are free. A payment method is required to start — you will NOT be charged until day 8. Cancel anytime."* New `[data-testid=active-sub-management]` block with `trial-active-banner` (shows the exact charge date), `cancellation-banner` (after cancel), `payment-failed-banner`, `manage-billing-button` (opens portal), `cancel-subscription-button` (one-click).
  - Testing iterations 23 + 24 — **107/107 backend pytest pass + full frontend e2e**. Iter-23 surfaced a real bug (Customer.create error path returned 500 instead of 502); fix applied in iter-24 (unified try/except around both Customer.create and Session.create) and re-validated to 100% green. End-to-end verified: registered fresh user → trial-billing-info renders → pay-card-button → real Stripe Checkout session created (`cs_test_b1i7Mivz...`) → redirect successful.

- **Iter 25 — defensive Cloudflare-520 hardening + diagnostic endpoint** (Feb 2026): User reported the production 520 persisted even after iter-24 redeploy. RCA hypothesis: SOMETHING was letting an exception escape the inner try/except OR the connection was being killed mid-response.
  - **Outer guard**: `/me/checkout` refactored into a thin outer handler that wraps `_create_checkout_impl` in a top-level try/except. HTTPException re-raised intact; any other exception becomes a structured `HTTPException(502, detail="Checkout failed unexpectedly (rid=<8hex>): <ErrorClass>: <msg>")` — Cloudflare always sees a clean HTTP response, never an empty/half-open socket.
  - **NEW `GET /api/health/stripe`**: diagnostic endpoint that calls `stripe.Balance.retrieve` via the same `_stripe_call` path used by checkout. Returns 200 + `{ok, api_base, key_prefix, timeout_seconds}` on success, or 200 + `{ok:false, error, stage, api_base, key_prefix}` on any failure. No auth required (read-only). Doesn't echo the full key. One-curl production triage — instantly tells you if STRIPE_API_KEY is missing, mis-routed, or connectivity-broken.
  - Testing iteration 25 — **113/113 backend pytest pass** (107 prior + 6 new in `test_iter25_defensive.py`). Verified the outer guard converts non-HTTPException to 502, preserves inner HTTPException, AND that `/api/health/stripe` returns graceful JSON in every failure mode.

- **"Powered by silence" landing page** (Feb 2026): cinematic root URL splash for unauthenticated visitors.
  - NEW `/app/frontend/src/components/LandingPage.jsx` — always-on visuals (24-bar wave visualizer, breathing orb with radial gradient, 4 concentric pulse rings, gold CTA with breathing glow). 1-line value prop "**Tune in. Settle down. *Resonate.***" + sub-copy + "**Start tuning →**" CTA + "7-DAY FREE TRIAL · CANCEL ANYTIME" microcopy + "POWERED BY SILENCE" footer.
  - Routing in `App.js` Shell(): unauth + landing-not-dismissed → LandingPage. CTA click writes `sessionStorage['solarisound:landing_dismissed']='1'` and reveals AuthScreen. Stripe-return URL params (`stripe_session_id` / `stripe_canceled`) auto-bypass the landing for any visitor. Authed users always skip. Safari private-mode safe (sessionStorage wrapped in try/catch).
  - Testing iteration 26 — **7/7 frontend PRIMARY scenarios pass + 113/113 backend regression**. Verified: copy + testids, CTA → AuthScreen + sessionStorage persistence, reload-preserves-dismissal, admin-skip-landing, Stripe-return bypass (authed → AccountDashboard, unauth → AuthScreen), bar/orb animations active, mobile 390×844 layout fits without horizontal scroll. Tiny ring-centering polish applied post-test.

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
