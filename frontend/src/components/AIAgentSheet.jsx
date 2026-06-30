import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, Send, X, Lock, Play, HeartPulse } from 'lucide-react';
import api from '@/lib/api';
import audioEngine from '@/lib/audioEngine';
import haptic from '@/lib/hapticEngine';

/**
 * Conversational check-in / companion sheet. Now a CONTROLLED component:
 * the host (Dashboard) owns the `open` flag and the opening `greeting` so
 * it can drive both the once-per-session auto-open AND the manual
 * "AI Companion" button (greeting: "How can I help you?").
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
  isPro,
  onClose,
  onOpenAccount,
  onTriggerAIPrescription,
}) {
  const [messages, setMessages] = useState([]); // [{role:'user'|'assistant', text, suggestions?}]
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
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
    setMessages([{ id: mkId(), role: 'assistant', text: greeting || 'How can I help you?', suggestions: [] }]);
    setInput('');
    setErr('');
    sessionIdRef.current = `agent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }, [open, greeting]);

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
      setMessages((prev) => [
        ...prev,
        { id: mkId(), role: 'assistant', text: data.message, suggestions: data.suggestions || [] },
      ]);
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
        detail: { kind: s.kind, label: s.label },
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
            <div className="label-tiny text-[#C4A67A]">AI Companion</div>
          </div>
          <button
            data-testid="ai-agent-close"
            onClick={close}
            className="text-[#8A9A92] hover:text-[#E8E3D9] p-1"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

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
