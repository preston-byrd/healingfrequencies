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
- Backend + frontend smoke tested end-to-end (testing agent iteration 1: 100% pass)

## Backlog (P1 → P2)
- P1: Persisted "last used config" auto-restore on login
- P1: A/B switch between equal-temperament and Verdi-A=432 reference
- P2: Optional sleep timer (fade-out over N min)
- P2: Daily session streak + minutes-listened stats
- P2: Share a session config via short URL
- P2: Real recorded ambient tracks (rain/ocean/forest) for premium quality (would need licensed assets)
- P2: PWA install + offline mode

## Next tasks (suggested)
1. Hook "last used config" into localStorage (no backend change needed)
2. Add A/B switch for 432Hz reference
3. Daily streak counter (new MongoDB collection)
