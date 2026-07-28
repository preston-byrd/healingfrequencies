# Test Credentials — Healing Frequencies

## Admin
- Email: `admin@example.com`
- Password: `JuzlUWlMMOjHM0u#m5qv0ds!oYp8`
- Role: admin

> ⚠️ The previous default `admin123` has been rotated as part of the Feb 2026
> security audit. The seed code NO LONGER auto-resets the admin password on
> restart; the password above is the one currently stored in the DB. To rotate
> in the future: log in as admin and `POST /api/me/password` with the new value.
>
> If you ever need to bootstrap from a fresh DB, set `ADMIN_BOOTSTRAP_RESET=true`
> in `/app/backend/.env`, restart once, then set it back to `false` to lock the
> self-heal off again.

## Test Flow
- Register a new user via `/api/auth/register` with `{email, password, name}` (password ≥ 8 chars)
- Or log in as admin via `/api/auth/login`
- Auth uses JWT in httpOnly cookie (`access_token`) + Authorization Bearer header fallback (token in localStorage)
- JWT lifetime: **24 hours**. Logout / password-change bump `tokens_valid_after`, invalidating all outstanding tokens across devices.
- Login throttle: 8 attempts / IP / ~5 min then 429.
- AI prescription endpoint: 6 requests / user / ~2 min then 429.

## Endpoints (under `/api`)
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET  /api/auth/me`
- `POST /api/me/password`
- `PUT  /api/me/profile`
- `GET  /api/me/subscription`
- `POST /api/me/ai-recommend` (Pro)
- `GET  /api/sessions`
- `POST /api/sessions`
- `DELETE /api/sessions/{id}`
- `POST /api/webhook/stripe` (requires `STRIPE_WEBHOOK_SECRET`)
- `GET  /api/health/stripe` (admin-only)
