/**
 * Voice-shortcut preset map. Each key is a voice-friendly slug a user can
 * pass via the `?preset=...` URL param (or via the `?intent=...` shortcut on
 * the deep-link route). Keep slugs lowercase, dash-free, and easy to say
 * aloud — these are the words a user will utter to Siri / Google Assistant.
 *
 * Each preset is a self-contained session config that the deep-link route
 * (`/play`) applies to audioEngine + hapticEngine + Sleep Mode in one shot.
 *
 * Custom URLs may bypass this map by supplying explicit ?frequency, ?binaural,
 * ?isochronic, ?soundscape, ?volume, ?pattern, ?duration_min, ?waveform.
 */
export const VOICE_PRESETS = {
  sleep: {
    label: 'Sleep',
    description: '174 Hz deep grounding · gentle rain · slow heartbeat haptic · 1 h sleep mode',
    voice_phrases: ['play my sleep frequency', 'play sleep frequency', 'play sleep'],
    config: {
      frequency: 174,
      waveform: 'sine',
      soundscape: 'rain',
      soundscape_volume: 0.4,
      pattern: 'heartbeat',
      duration_min: 60,
    },
  },
  calm: {
    label: 'Calm',
    description: '432 Hz · ocean waves · 4-7-8 breath haptic · 20 min',
    voice_phrases: ['play my calm frequency', 'play calm frequency', 'play calm'],
    config: {
      frequency: 432,
      waveform: 'sine',
      soundscape: 'ocean',
      soundscape_volume: 0.45,
      pattern: 'breath478',
      duration_min: 20,
    },
  },
  focus: {
    label: 'Focus',
    description: '40 Hz gamma binaural at 432 Hz carrier · 30 min',
    voice_phrases: ['play my focus frequency', 'play focus frequency', 'play focus'],
    config: {
      frequency: 432,
      waveform: 'sine',
      binaural: 40,
      pattern: 'frequency',
      duration_min: 30,
    },
  },
  meditation: {
    label: 'Meditation',
    description: '7.83 Hz Schumann · singing bowls · breath haptic · 20 min',
    voice_phrases: ['play my meditation frequency', 'play meditation', 'start meditation'],
    config: {
      frequency: 432,
      waveform: 'sine',
      binaural: 7.83,
      soundscape: 'bowls',
      soundscape_volume: 0.5,
      pattern: 'breath478',
      duration_min: 20,
    },
  },
  anxiety: {
    label: 'Anxiety relief',
    description: '528 Hz heart · forest · 4-7-8 breath haptic · 15 min',
    voice_phrases: ['play my anxiety frequency', 'play anxiety relief'],
    config: {
      frequency: 528,
      waveform: 'sine',
      soundscape: 'forest',
      soundscape_volume: 0.5,
      pattern: 'breath478',
      duration_min: 15,
    },
  },
  grounding: {
    label: 'Grounding',
    description: '174 Hz · forest · slow heartbeat haptic · 20 min',
    voice_phrases: ['play my grounding frequency', 'play grounding'],
    config: {
      frequency: 174,
      waveform: 'sine',
      soundscape: 'forest',
      soundscape_volume: 0.45,
      pattern: 'heartbeat',
      duration_min: 20,
    },
  },
};

/** Resolve a preset slug to a config, case-insensitive, with a few common
 *  spoken variants ("deep sleep" → sleep, "concentration" → focus). */
export function resolvePreset(slug) {
  if (!slug) return null;
  const key = String(slug).toLowerCase().trim();
  if (VOICE_PRESETS[key]) return { key, ...VOICE_PRESETS[key] };
  const aliases = {
    'deep sleep': 'sleep', 'sleeping': 'sleep', 'bedtime': 'sleep',
    'relax': 'calm', 'relaxation': 'calm', 'calming': 'calm',
    'concentration': 'focus', 'focused': 'focus', 'work': 'focus',
    'meditate': 'meditation',
    'anxious': 'anxiety', 'stress': 'anxiety', 'panic': 'anxiety',
    'ground': 'grounding', 'earth': 'grounding', 'centering': 'grounding',
  };
  const alias = aliases[key];
  if (alias && VOICE_PRESETS[alias]) return { key: alias, ...VOICE_PRESETS[alias] };
  return null;
}
