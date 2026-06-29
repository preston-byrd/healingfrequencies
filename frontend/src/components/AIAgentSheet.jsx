import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, Send, X, Lock, Play } from 'lucide-react';
import api from '@/lib/api';
import audioEngine from '@/lib/audioEngine';

/**
 * Conversational check-in agent. Renders as a centered modal sheet that
 * appears on first dashboard mount per browser session.
 *
 * Flow:
 *   - Opens with a personalised greeting using the user's saved name (or a
 *     generic prompt when missing).
 *   - User types how they feel; we POST /api/me/agent/chat with the running
 *     conversation history. Backend returns {message, suggestions[]}.
 *   - Each suggestion is a tappable card (preset / soundscape / sleep /
 *     ai_prescription). Tapping applies it to the existing audio engine and
 *     starts playback, then closes the sheet.
 *   - User can keep chatting after the first set (decline → "want others?" →
 *     new options). All multi-turn state lives in this component.
 *
 * Pro gating: backend already tags `pro_only` per suggestion. We render a
 * lock badge and route taps to the Account upgrade flow instead of starting
 * playback when the user is non-Pro.
 */
const SESSION_KEY = 'sf_agent_seen_v1';

export default function AIAgentSheet({ user, isPro, onClose, onOpenAccount, onTriggerAIPrescription }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]); // [{role:'user'|'assistant', text, suggestions?}]
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const scrollRef = useRef(null);
  const sessionIdRef = useRef(`agent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);

  // Open ONCE per browser session. Shown after login lands on the dashboard.
  useEffect(() => {
    if (!user) return;
    if (sessionStorage.getItem(SESSION_KEY) === '1') return;
    const name = (user.name || '').trim();
    const greeting = name
      ? `Hello ${name}, how are you feeling right now?`
      : 'Hello, how are you feeling right now?';
    setMessages([{ role: 'assistant', text: greeting, suggestions: [] }]);
    setOpen(true);
    sessionStorage.setItem(SESSION_KEY, '1');
  }, [user]);

  // Autoscroll to the latest message.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setErr('');
    setInput('');
    const nextMessages = [...messages, { role: 'user', text }];
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
        { role: 'assistant', text: data.message, suggestions: data.suggestions || [] },
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
      }
    } catch (e) {
      console.warn('[AIAgentSheet] applySuggestion failed', e);
    }
    close();
  };

  const close = () => {
    setOpen(false);
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
            <div className="label-tiny text-[#C4A67A]">Healing Companion</div>
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
            <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
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
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-[#E8E3D9] text-sm truncate">{s.label}</div>
                        <div className="text-[10px] text-[#8A9A92] uppercase tracking-wider font-mono">
                          {s.kind.replace('_', ' ')}
                          {s.kind === 'preset' && s.frequency && ` · ${s.frequency} Hz`}
                          {s.kind === 'soundscape' && s.soundscape && ` · ${s.soundscape}`}
                          {s.kind === 'sleep' && s.duration_min && ` · ${s.duration_min >= 60 ? `${s.duration_min / 60}h` : `${s.duration_min}m`}`}
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
