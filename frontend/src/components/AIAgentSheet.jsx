import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, Send, X, Lock, Play, HeartPulse, Settings } from 'lucide-react';
import api from '@/lib/api';
import audioEngine from '@/lib/audioEngine';
import haptic from '@/lib/hapticEngine';

/**
 * Conversational check-in / companion sheet. Now a CONTROLLED component:
 * the host (Dashboard) owns the `open` flag and the opening `greeting` so
 * it can drive both the once-per-session auto-open AND the manual
 * "Wellness Assistant" button (greeting: "How can I help you?").
 *
 * Suggestion taps:
 *   - apply the choice to the existing audio engine (preset/soundscape) OR
 *     dispatch a window event (sleep) OR call onTriggerAIPrescription
 *     (ai_prescription).
 *   - persist the (mood → chosen suggestion) pair to MongoDB via
 *     POST /me/agent/checkin so future check-ins can reference it.
 */
export default function AIAgentSheet({
  open,
  greeting,
  initialSuggestion,
  initialSuggestions,
  isPro,
  onClose,
  onOpenAccount,
  onTriggerAIPrescription,
}) {
  const [messages, setMessages] = useState([]); // [{role:'user'|'assistant', text, suggestions?}]
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  // Phase 8 — assistant settings (Harmonic Blueprint influence on/off).
  // Loaded once on sheet open; changes are optimistic + persisted server-side.
  const [settings, setSettings] = useState({ harmonic_influence_enabled: true });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const scrollRef = useRef(null);
  const sessionIdRef = useRef(`agent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);

  // (Re)seed the conversation whenever the sheet transitions from closed → open
  // with a new greeting. We keep messages cleared between sessions so the
  // companion always starts fresh — old turns aren't reloaded on reopen.
  // Stable id generator for messages — keeps React keys deterministic so
  // appending new turns never re-mounts existing bubbles (which would
  // disrupt the autoscroll + animation), and protects against any future
  // splice / filter use of the messages array.
  const nextIdRef = useRef(0);
  const mkId = () => { nextIdRef.current += 1; return `m-${nextIdRef.current}`; };

  useEffect(() => {
    if (!open) return;
    nextIdRef.current = 0;
    // HF-041: honour any host-supplied initial suggestions so the first
    // assistant turn can offer one-tap actions (e.g. the Weekly Alignment
    // Check-in's "Yes / Not now" pair). Falls back to the single
    // `initialSuggestion` prop for pre-HF-041 callers, then an empty list.
    const seedSuggestions = Array.isArray(initialSuggestions) && initialSuggestions.length > 0
      ? initialSuggestions
      : (initialSuggestion ? [initialSuggestion] : []);
    setMessages([{ id: mkId(), role: 'assistant', text: greeting || 'How can I help you?', suggestions: seedSuggestions }]);
    setInput('');
    setErr('');
    setSettingsOpen(false);
    sessionIdRef.current = `agent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    // Fetch current assistant settings — cheap idempotent call. Ignored
    // failures leave the local optimistic default (HB on) in place.
    (async () => {
      try {
        const { data } = await api.get('/me/settings');
        if (data && typeof data === 'object') setSettings(data);
      } catch (_) { /* graceful */ }
    })();
  }, [open, greeting]);

  const toggleHarmonicInfluence = async () => {
    const next = !settings.harmonic_influence_enabled;
    setSettings((prev) => ({ ...prev, harmonic_influence_enabled: next }));
    setSettingsSaving(true);
    try {
      const { data } = await api.post('/me/settings', { harmonic_influence_enabled: next });
      if (data && typeof data === 'object') setSettings(data);
    } catch (_) {
      // Revert on failure so the toggle reflects real server state.
      setSettings((prev) => ({ ...prev, harmonic_influence_enabled: !next }));
    } finally {
      setSettingsSaving(false);
    }
  };

  // Autoscroll to the latest message.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  // Helper: pull the most recent user-typed message out of the conversation.
  // Used as the "mood" field when persisting a check-in.
  const lastUserMessage = () => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'user') return messages[i].text;
    }
    return '';
  };

  // Detect an affirmative reply so we can auto-apply the previous
  // assistant turn's suggestion if the LLM's confirmation reply forgets
  // to re-attach the actionable card. Keep the list conservative — only
  // strings that unambiguously mean "yes, do that". A false positive
  // here would surprise the user with unexpected audio.
  const _AFFIRMATIVE_RE = /^(yes\b|yeah\b|yep\b|yup\b|sure\b|ok\b|okay\b|k\b|please\b|sounds good\b|let'?s do it\b|let'?s go\b|do it\b|start\b|go for it\b|go ahead\b|go\b|great\b|perfect\b)([\s.!?]|$)/i;
  const isAffirmative = (text) => _AFFIRMATIVE_RE.test(String(text || '').trim());

  // Pick the most recent assistant turn's *actionable* suggestion — one
  // we know how to execute in applySuggestion. Ignores plain informational
  // suggestions with no kind we handle.
  const lastActionableSuggestion = (msgs) => {
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      const m = msgs[i];
      if (m.role !== 'assistant' || !Array.isArray(m.suggestions)) continue;
      const s = m.suggestions.find((x) => (
        x && (x.kind === 'preset' || x.kind === 'soundscape' || x.kind === 'sleep' || x.kind === 'haptic_combo' || x.kind === 'ai_prescription')
      ));
      if (s) return s;
    }
    return null;
  };

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setErr('');
    setInput('');
    const nextMessages = [...messages, { id: mkId(), role: 'user', text }];
    setMessages(nextMessages);
    setLoading(true);
    try {
      const history = nextMessages.map((m) => ({ role: m.role, text: m.text }));
      const { data } = await api.post('/me/agent/chat', {
        message: text,
        history,
        session_id: sessionIdRef.current,
      });
      const suggestions = data.suggestions || [];
      setMessages((prev) => [
        ...prev,
        {
          id: mkId(),
          role: 'assistant',
          text: data.message,
          suggestions,
          // Phase 9 — LLM was invited to weave a gentle HB setup nudge. UI
          // reflects this with a soft "not now" affordance on the message.
          hbNudgeShown: !!data.hb_nudge_shown,
        },
      ]);
      // Guardrail: if the user just replied affirmatively (e.g. "Let's
      // do it") to a prior suggestion, and the LLM's follow-up talks
      // as if action was taken ("Perfect! Starting Rain for you now")
      // but forgot to re-attach the actionable suggestion, auto-apply
      // the pending one so playback actually starts. Without this, the
      // Assistant becomes a chat that *narrates* actions instead of
      // *taking* them, which is the bug users report as "Rain never
      // played after I said Let's do it".
      if (isAffirmative(text) && suggestions.length === 0) {
        const pending = lastActionableSuggestion(nextMessages);
        if (pending) {
          // applySuggestion also closes the sheet, so the user sees the
          // main player fire up rather than staying trapped in chat.
          await applySuggestion(pending);
        }
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Could not reach the agent';
      setErr(typeof msg === 'string' ? msg : 'Agent error');
    } finally {
      setLoading(false);
    }
  };

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  // Apply a suggestion to the engine and close. Reuses the existing
  // setFrequency / setAmbient / etc public surface — fully compatible with
  // Smart Fade Timer, the playback contract, layered audio, etc.
  const applySuggestion = async (s) => {
    if (s.pro_only && !isPro) {
      onOpenAccount && onOpenAccount();
      close();
      return;
    }
    // Persist the (mood → choice) pair BEFORE we leave the sheet so the
    // next /me/agent/chat call can reference it as PRIOR_INSIGHT. Fire-and-
    // forget — never block UX on the persistence call.
    const mood = lastUserMessage();
    if (mood) {
      // Also stash the mood locally so the next completed session picks
      // it up when it writes a Wellness Journey entry (consumed-once).
      try { localStorage.setItem('solar:last_agent_mood', mood.slice(0, 300)); } catch (_) { /* graceful */ }
      api.post('/me/agent/checkin', {
        message: mood,
        suggestion: s,
        session_id: sessionIdRef.current,
      }).catch((e) => console.warn('[AIAgentSheet] checkin persist failed', e));
    }
    try {
      if (s.kind === 'preset') {
        audioEngine.setBinaural(0);
        audioEngine.setIsochronic(0);
        audioEngine.setFrequency(s.frequency);
        audioEngine.setWaveform(s.waveform || 'sine');
        // Light ambient under tones so it doesn't feel naked
        ['rain', 'ocean', 'forest', 'wind', 'crickets', 'bowls', 'brown', 'white']
          .forEach((k) => audioEngine.setAmbient(k, 0));
        if (!audioEngine.playing) await audioEngine.start();
      } else if (s.kind === 'soundscape') {
        ['rain', 'ocean', 'forest', 'wind', 'crickets', 'bowls', 'brown', 'white']
          .forEach((k) => audioEngine.setAmbient(k, 0));
        audioEngine.setAmbient(s.soundscape, s.volume ?? 0.5);
        if (!audioEngine.playing) await audioEngine.start();
      } else if (s.kind === 'sleep') {
        // Delegate up to the Dashboard's Sleep Mode start logic via a custom
        // event — the host wires this onto startSleepMode + sets duration.
        window.dispatchEvent(new CustomEvent('sf:agent:sleep', { detail: { duration_min: s.duration_min } }));
      } else if (s.kind === 'ai_prescription') {
        onTriggerAIPrescription && onTriggerAIPrescription(s.intent);
      } else if (s.kind === 'harmonic_blueprint') {
        // HF-041 Weekly Alignment Check-in: user tapped "Yes, run it".
        // Delegate to the Dashboard which owns HarmonicBlueprintSheet
        // state — the sheet opens on the intro step and (if the user
        // hasn't opted out) routes through the tips ritual on Begin.
        window.dispatchEvent(new CustomEvent('sf:agent:open-blueprint'));
      } else if (s.kind === 'alignment_snooze') {
        // HF-041: user tapped "Not now". Persist the snooze then continue
        // the conversation with a normal check-in greeting so the user
        // isn't kicked out of the assistant. `close()` at the end of
        // applySuggestion is skipped via the early-return below.
        const tzOff = new Date().getTimezoneOffset();
        api.post('/me/alignment-checkin/snooze', { tz_offset_minutes: tzOff })
          .catch((e) => console.warn('[AIAgentSheet] alignment snooze failed', e));
        setMessages((prev) => [
          ...prev,
          {
            id: mkId(),
            role: 'assistant',
            text: "No problem — I'll circle back next Sunday. Meanwhile, how are you feeling right now?",
            suggestions: [],
          },
        ]);
        return;
      } else if (s.kind === 'haptic_combo') {
        // One-tap card: turn haptics on with the chosen pattern, then lay
        // down the (optional) carrier sound underneath, then either start a
        // plain session or hand off to Sleep Mode if duration_min is one of
        // the sleep durations. Auto-enables haptics for the user when they
        // accept this combo — the modal toggle is the manual surface.
        haptic.setEnabled(true);
        haptic.setPattern(s.pattern || 'auto');
        // Reset audio state so the combo lands on a known baseline.
        audioEngine.setBinaural(0);
        audioEngine.setIsochronic(0);
        ['rain', 'ocean', 'forest', 'wind', 'crickets', 'bowls', 'brown', 'white']
          .forEach((k) => audioEngine.setAmbient(k, 0));
        if (typeof s.frequency === 'number' && s.frequency > 0) {
          audioEngine.setFrequency(s.frequency);
          audioEngine.setWaveform('sine');
        }
        if (s.soundscape) {
          // Honour an LLM-supplied volume on the haptic_combo layer when
          // provided (same range as the regular soundscape kind: 0..1);
          // default 0.5 keeps the carrier audible without overpowering the
          // tone or the haptic.
          const vol = typeof s.volume === 'number' ? Math.max(0, Math.min(1, s.volume)) : 0.5;
          audioEngine.setAmbient(s.soundscape, vol);
        }
        // Sleep durations we know about (30/60/120/240/480 min) route through
        // Sleep Mode so the timer + fade + Pro gating apply. Shorter durations
        // just start the session — Smart Fade will still taper the last 5 min.
        if (s.duration_min && [30, 60, 120, 240, 480].includes(s.duration_min)) {
          window.dispatchEvent(new CustomEvent('sf:agent:sleep', { detail: { duration_min: s.duration_min } }));
        } else if (!audioEngine.playing) {
          await audioEngine.start();
        }
      }
    } catch (e) {
      console.warn('[AIAgentSheet] applySuggestion failed', e);
    }
    // Broadcast that the user just accepted a suggestion. The Dashboard
    // listens for this and starts the 30s-after onboarding transition
    // (Step 2 + Step 3 of the onboarding strategy). Fire-and-forget event
    // — kept here rather than at every individual kind so we never miss it.
    try {
      window.dispatchEvent(new CustomEvent('sf:agent:suggestion-taken', {
        detail: {
          kind: s.kind,
          label: s.label,
          frequency: (typeof s.frequency === 'number' && s.frequency > 0) ? s.frequency : undefined,
        },
      }));
    } catch (e) { /* event dispatch shouldn't ever throw, but be safe */ }
    close();
  };

  const close = () => {
    onClose && onClose();
  };

  if (!open) return null;

  return (
    <div
      data-testid="ai-agent-sheet"
      className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center bg-black/65 backdrop-blur-sm p-0 sm:p-4"
      role="dialog"
      aria-modal="true"
      onClick={(e) => { if (e.target === e.currentTarget) close(); }}
    >
      <div className="w-full sm:max-w-md bg-[#0E1F18] border border-[#5C9E8C]/25 rounded-t-3xl sm:rounded-2xl shadow-2xl overflow-hidden flex flex-col" style={{ maxHeight: '85vh' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#5C9E8C]/15">
          <div className="flex items-center gap-2">
            <Sparkles size={14} className="text-[#C4A67A]" />
            <div className="label-tiny text-[#C4A67A]">Wellness Assistant</div>
          </div>
          <div className="flex items-center gap-1">
            <button
              data-testid="ai-agent-settings-toggle"
              onClick={() => setSettingsOpen((v) => !v)}
              aria-label={settingsOpen ? 'Close settings' : 'Open settings'}
              aria-expanded={settingsOpen}
              className={`p-1.5 rounded-md transition-colors ${
                settingsOpen ? 'text-[#C4A67A] bg-[#C4A67A]/12' : 'text-[#8A9A92] hover:text-[#E8E3D9]'
              }`}
            >
              <Settings size={15} />
            </button>
            <button
              data-testid="ai-agent-close"
              onClick={close}
              className="text-[#8A9A92] hover:text-[#E8E3D9] p-1"
              aria-label="Close"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Collapsible settings panel */}
        {settingsOpen && (
          <div
            className="border-b border-[#5C9E8C]/15 px-5 py-3 bg-black/25"
            data-testid="ai-agent-settings-panel"
          >
            <label className="flex items-start justify-between gap-4 cursor-pointer group">
              <div className="min-w-0">
                <div className="text-[13px] text-[#E8E3D9] font-medium">Harmonic Blueprint influence</div>
                <div className="text-[11px] text-[#8A9A92] leading-relaxed mt-0.5">
                  Weight suggestions by your saved resonance profile. Turn off for
                  neutral suggestions.
                </div>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={!!settings.harmonic_influence_enabled}
                onClick={toggleHarmonicInfluence}
                disabled={settingsSaving}
                data-testid="ai-agent-toggle-harmonic-influence"
                className={`shrink-0 relative inline-flex h-5 w-9 rounded-full border transition-colors ${
                  settings.harmonic_influence_enabled
                    ? 'bg-[#5C9E8C]/50 border-[#72C2AC]'
                    : 'bg-black/40 border-[#5C9E8C]/25'
                } ${settingsSaving ? 'opacity-60' : ''}`}
              >
                <span
                  aria-hidden="true"
                  className={`inline-block h-3.5 w-3.5 my-[2px] rounded-full bg-[#E8E3D9] shadow transform transition-transform ${
                    settings.harmonic_influence_enabled ? 'translate-x-[18px]' : 'translate-x-[2px]'
                  }`}
                />
              </button>
            </label>
          </div>
        )}

        {/* Messages */}
        <div
          ref={scrollRef}
          data-testid="ai-agent-messages"
          className="flex-1 overflow-y-auto px-5 py-4 space-y-4 custom-scrollbar"
        >
          {messages.map((m, i) => (
            <div key={m.id} className={m.role === 'user' ? 'flex justify-end' : ''}>
              <div
                className={
                  m.role === 'user'
                    ? 'max-w-[80%] rounded-2xl rounded-br-md px-3.5 py-2 bg-[#5C9E8C]/20 text-[#E8E3D9] text-sm'
                    : 'max-w-[90%] text-[#E8E3D9] text-sm leading-relaxed'
                }
                data-testid={m.role === 'user' ? 'agent-user-msg' : 'agent-assistant-msg'}
              >
                {m.text}
              </div>
              {m.role === 'assistant' && Array.isArray(m.suggestions) && m.suggestions.length > 0 && (
                <div className="mt-3 flex flex-col gap-2" data-testid="agent-suggestions">
                  {m.suggestions.map((s, j) => (
                    <button
                      key={`${i}-${j}`}
                      data-testid={`agent-suggestion-${s.kind}-${j}`}
                      onClick={() => applySuggestion(s)}
                      className="group flex items-center gap-3 text-left px-3.5 py-2.5 rounded-xl border border-[#5C9E8C]/25 bg-black/30 hover:border-[#72C2AC]/50 hover:bg-[#5C9E8C]/10 transition-colors"
                    >
                      <Play
                        size={14}
                        className={`shrink-0 ${s.pro_only && !isPro ? 'text-[#8A9A92]' : 'text-[#72C2AC]'}`}
                        style={s.kind === 'haptic_combo' ? { display: 'none' } : undefined}
                      />
                      {s.kind === 'haptic_combo' && (
                        <HeartPulse size={14} className="shrink-0 text-[#C4A67A]" />
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="text-[#E8E3D9] text-sm truncate">{s.label}</div>
                        <div className="text-[10px] text-[#8A9A92] uppercase tracking-wider font-mono">
                          {s.kind.replace('_', ' ')}
                          {s.kind === 'preset' && s.frequency && ` · ${s.frequency} Hz`}
                          {s.kind === 'soundscape' && s.soundscape && ` · ${s.soundscape}`}
                          {s.kind === 'sleep' && s.duration_min && ` · ${s.duration_min >= 60 ? `${s.duration_min / 60}h` : `${s.duration_min}m`}`}
                          {s.kind === 'haptic_combo' && (
                            <>
                              {s.pattern && ` · ${s.pattern}`}
                              {s.frequency && ` · ${s.frequency} Hz`}
                              {s.soundscape && ` · ${s.soundscape}`}
                              {s.duration_min && ` · ${s.duration_min >= 60 ? `${s.duration_min / 60}h` : `${s.duration_min}m`}`}
                            </>
                          )}
                        </div>
                        {s.harmonic_note && (
                          <div
                            className="mt-1 text-[11px] leading-snug italic text-[#98C1B0]"
                            data-testid="agent-suggestion-harmonic-note"
                          >
                            {s.harmonic_note}
                          </div>
                        )}
                      </div>
                      {s.pro_only && !isPro && (
                        <span className="flex items-center gap-1 text-[10px] text-[#C4A67A] font-mono">
                          <Lock size={10} /> Pro
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
              {m.role === 'assistant' && m.hbNudgeShown && (
                <div className="mt-2 flex items-center gap-2 text-[10px] uppercase tracking-[0.14em] text-[#5A6B65]">
                  <span>Harmonic Blueprint hint above · </span>
                  <button
                    type="button"
                    data-testid="agent-hb-nudge-dismiss"
                    onClick={() => {
                      // Hide the affordance immediately so the user knows
                      // it registered. Persist the dismiss server-side so
                      // this session won't nudge again.
                      setMessages((prev) => prev.map((mm) =>
                        mm.id === m.id ? { ...mm, hbNudgeShown: false } : mm
                      ));
                      api.post('/me/hb-nudge/dismiss', {
                        session_id: sessionIdRef.current,
                      }).catch(() => {});
                    }}
                    className="text-[#C4A67A]/80 hover:text-[#C4A67A] transition-colors underline-offset-2 hover:underline"
                  >
                    not now
                  </button>
                </div>
              )}
            </div>
          ))}
          {loading && (
            <div data-testid="agent-loading" className="text-[#8A9A92] text-xs italic">…thinking</div>
          )}
          {err && (
            <div data-testid="agent-error" className="text-[#E07A5F] text-xs">{err}</div>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-[#5C9E8C]/15 p-3 flex items-end gap-2">
          <textarea
            data-testid="agent-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            placeholder="Type how you're feeling…"
            rows={1}
            disabled={loading}
            className="flex-1 bg-black/30 border border-[#5C9E8C]/20 rounded-xl px-3 py-2 text-sm text-[#E8E3D9] placeholder-[#5A6B65] focus:outline-none focus:border-[#72C2AC]/50 resize-none"
            style={{ maxHeight: 100 }}
          />
          <button
            data-testid="agent-send"
            onClick={send}
            disabled={loading || !input.trim()}
            className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
              loading || !input.trim()
                ? 'bg-[#5C9E8C]/10 text-[#5A6B65] cursor-not-allowed'
                : 'bg-[#72C2AC]/25 text-[#72C2AC] hover:bg-[#72C2AC]/35'
            }`}
            aria-label="Send"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
