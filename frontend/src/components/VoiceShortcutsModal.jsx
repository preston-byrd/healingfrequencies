import React, { useState } from 'react';
import { X, Mic, Copy, Check, Apple, ChevronRight } from 'lucide-react';
import { VOICE_PRESETS } from '@/lib/voicePresets';

/**
 * VoiceShortcutsModal — teaches the user how to wire up Siri (iOS Shortcuts)
 * and Google Assistant (Routines) so they can trigger a Solarisound session
 * by voice without ever opening the app.
 *
 * Each preset card exposes a Copy button that puts the deep-link URL on the
 * clipboard so the user can paste it straight into Shortcuts / Assistant.
 */
export default function VoiceShortcutsModal({ open, onClose }) {
  const [copied, setCopied] = useState(null);
  const [tab, setTab] = useState('ios'); // 'ios' | 'android'

  if (!open) return null;
  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://solarisound.com';

  const urlFor = (key) => `${origin}/play?preset=${key}`;
  const copy = async (text, key) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied(null), 1500);
    } catch (e) {
      console.warn('[VoiceShortcuts] clipboard failed', e);
    }
  };

  return (
    <div
      data-testid="voice-shortcuts-modal"
      className="fixed inset-0 z-[65] flex items-end sm:items-center justify-center bg-black/65 backdrop-blur-sm p-0 sm:p-4"
      role="dialog"
      aria-modal="true"
      onClick={(e) => { if (e.target === e.currentTarget) onClose && onClose(); }}
    >
      <div className="w-full sm:max-w-lg bg-[#0E1F18] border border-[#5C9E8C]/25 rounded-t-3xl sm:rounded-2xl shadow-2xl overflow-hidden flex flex-col" style={{ maxHeight: '88vh' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#5C9E8C]/15">
          <div className="flex items-center gap-2">
            <Mic size={14} className="text-[#C4A67A]" />
            <div className="label-tiny text-[#C4A67A]">Voice Shortcuts</div>
          </div>
          <button
            data-testid="voice-shortcuts-close"
            onClick={onClose}
            className="text-[#8A9A92] hover:text-[#E8E3D9] p-1"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Tabs */}
        <div className="px-5 pt-4 flex items-center gap-2 border-b border-[#5C9E8C]/15">
          <button
            data-testid="vs-tab-ios"
            onClick={() => setTab('ios')}
            className={`px-3 py-2 text-xs uppercase tracking-wider font-mono transition-colors ${tab === 'ios' ? 'text-[#C4A67A] border-b-2 border-[#C4A67A]' : 'text-[#8A9A92] hover:text-[#E8E3D9]'}`}
          >
            <Apple size={12} className="inline mr-1.5 -mt-0.5" /> iPhone (Siri)
          </button>
          <button
            data-testid="vs-tab-android"
            onClick={() => setTab('android')}
            className={`px-3 py-2 text-xs uppercase tracking-wider font-mono transition-colors ${tab === 'android' ? 'text-[#C4A67A] border-b-2 border-[#C4A67A]' : 'text-[#8A9A92] hover:text-[#E8E3D9]'}`}
          >
            Android (Google Assistant)
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 overflow-y-auto custom-scrollbar space-y-5">
          {tab === 'ios' && (
            <div data-testid="vs-ios-content" className="space-y-3">
              <p className="text-sm text-[#E8E3D9]/90 leading-relaxed">
                Wire up one Shortcut per preset. After that, just say <span className="text-[#C4A67A]">&ldquo;Hey Siri, play my sleep frequency&rdquo;</span> with the phone face-down on the nightstand.
              </p>
              <ol className="text-sm space-y-2.5 text-[#E8E3D9]/85">
                <li className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-[11px] font-mono flex items-center justify-center mt-0.5">1</span>
                  Open the <strong className="text-[#C4A67A]">Shortcuts</strong> app on iPhone &rarr; tap <strong>+</strong> to create a new shortcut.
                </li>
                <li className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-[11px] font-mono flex items-center justify-center mt-0.5">2</span>
                  Search for <strong>&ldquo;Open URL&rdquo;</strong> and add that action. Paste one of the preset URLs below into the URL field.
                </li>
                <li className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-[11px] font-mono flex items-center justify-center mt-0.5">3</span>
                  Tap <strong className="text-[#C4A67A]">Add to Siri</strong> (or the &ldquo;Settings&rdquo; icon &rarr; Add to Siri), then record your trigger phrase (e.g. <em>&ldquo;play my sleep frequency&rdquo;</em>).
                </li>
                <li className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-[11px] font-mono flex items-center justify-center mt-0.5">4</span>
                  Done. Saying the phrase will open Solarisound and the session will start. Tip: install the app to your Home Screen first for the smoothest experience.
                </li>
              </ol>
            </div>
          )}

          {tab === 'android' && (
            <div data-testid="vs-android-content" className="space-y-3">
              <p className="text-sm text-[#E8E3D9]/90 leading-relaxed">
                Wire up one Routine per preset in Google Assistant. After that, just say <span className="text-[#C4A67A]">&ldquo;Hey Google, play my sleep frequency&rdquo;</span>.
              </p>
              <ol className="text-sm space-y-2.5 text-[#E8E3D9]/85">
                <li className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-[11px] font-mono flex items-center justify-center mt-0.5">1</span>
                  Open the <strong className="text-[#C4A67A]">Google Home</strong> app &rarr; tap <strong>Routines</strong> &rarr; <strong>+ New</strong>.
                </li>
                <li className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-[11px] font-mono flex items-center justify-center mt-0.5">2</span>
                  Under <strong>Starter</strong>, add <strong>&ldquo;When I say something&rdquo;</strong> and enter your phrase (e.g. <em>&ldquo;play my sleep frequency&rdquo;</em>).
                </li>
                <li className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-[11px] font-mono flex items-center justify-center mt-0.5">3</span>
                  Under <strong>Actions</strong>, choose <strong>&ldquo;Try adding your own&rdquo;</strong> and enter <code className="text-[11px] bg-black/40 px-1 py-0.5 rounded">Open</code> followed by one of the URLs below.
                </li>
                <li className="flex items-start gap-2">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-[#5C9E8C]/20 text-[#72C2AC] text-[11px] font-mono flex items-center justify-center mt-0.5">4</span>
                  Save. Saying the phrase will open Solarisound in your browser and the session will start automatically.
                </li>
              </ol>
            </div>
          )}

          {/* Preset URLs — copyable */}
          <div className="space-y-2 pt-2 border-t border-[#5C9E8C]/15">
            <div className="label-tiny text-[#8A9A92]">Preset deep links</div>
            {Object.entries(VOICE_PRESETS).map(([key, p]) => {
              const url = urlFor(key);
              const phrase = p.voice_phrases[0];
              const isCopied = copied === key;
              return (
                <div
                  key={key}
                  data-testid={`vs-preset-${key}`}
                  className="p-3 rounded-xl border border-[#5C9E8C]/20 bg-black/30 space-y-2"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-[#E8E3D9]">{p.label}</div>
                      <div className="text-[11px] text-[#8A9A92] mt-0.5 leading-relaxed">{p.description}</div>
                      <div className="text-[11px] text-[#C4A67A] mt-1.5 italic">
                        <ChevronRight size={10} className="inline -mt-0.5" /> &ldquo;{phrase}&rdquo;
                      </div>
                    </div>
                    <button
                      data-testid={`vs-copy-${key}`}
                      onClick={() => copy(url, key)}
                      className={`shrink-0 inline-flex items-center gap-1 px-2.5 py-1.5 rounded-full text-[11px] font-mono uppercase tracking-wider transition-colors ${
                        isCopied
                          ? 'bg-[#72C2AC]/25 text-[#72C2AC]'
                          : 'bg-[#5C9E8C]/15 text-[#8A9A92] hover:text-[#72C2AC] hover:bg-[#5C9E8C]/25'
                      }`}
                    >
                      {isCopied ? <><Check size={11} /> Copied</> : <><Copy size={11} /> Copy URL</>}
                    </button>
                  </div>
                  <code className="block text-[10px] text-[#72C2AC] font-mono bg-black/40 rounded px-2 py-1.5 break-all">{url}</code>
                </div>
              );
            })}
          </div>

          {/* Caveats */}
          <ul className="text-[11px] text-[#8A9A92] space-y-1.5 pt-2 border-t border-[#5C9E8C]/15">
            <li>· iOS Safari requires one tap to start audio if it doesn&apos;t detect the navigation as a user gesture — a big &ldquo;Tap to begin&rdquo; button appears in that case.</li>
            <li>· Add Solarisound to your Home Screen first (Install app button) for the cleanest hand-off from voice → audio.</li>
            <li>· The deep link works whether you&apos;re signed in or not. Sign in afterwards to save the session.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
