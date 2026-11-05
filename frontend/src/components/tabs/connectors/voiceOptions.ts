// The voices offered for a call, with what each costs. Mirrors the backend's
// connectors/voice/tts_models.py for which languages each speaks.
// Prices: LiveKit Inference on the Build and Ship plans, per million characters (livekit.com/pricing/
// inference, read 2026-11-05). ElevenLabs is gone from the list: LiveKit lists it as a plugin needing
// its own key, not an Inference voice, and has no price for it here. A setting saved with one stays.

interface VoiceModel {
  value: string
  name: string
  pricePerMillion: number
  /** Only for a voice that speaks fewer than all of the app's languages. */
  speaks?: readonly string[]
}

export const DEFAULT_VOICE_MODEL = 'cartesia/sonic-3'

const VOICE_MODELS: readonly VoiceModel[] = [
  { value: DEFAULT_VOICE_MODEL, name: 'Cartesia Sonic 3 (default)', pricePerMillion: 50 },
  { value: 'cartesia/sonic-3.6', name: 'Cartesia Sonic 3.6', pricePerMillion: 50 },
  { value: 'inworld/inworld-tts-2', name: 'Inworld 2.0', pricePerMillion: 25 },
  { value: 'inworld/inworld-tts-2-flash', name: 'Inworld 2.0 Flash', pricePerMillion: 15 },
  {
    value: 'assemblyai/assemblyai-tts-1',
    name: 'AssemblyAI TTS 1',
    pricePerMillion: 15,
    speaks: ['en', 'es', 'de', 'fr', 'pt'],
  },
  {
    value: 'fishaudio/s2.1-pro',
    name: 'Fish Audio S2.1 Pro',
    pricePerMillion: 15,
    speaks: ['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'pt'],
  },
]

// Measured on one LiveKit project, November 2026: 77 call minutes spoke 511 characters a minute,
// and hearing plus the front voice cost $0.005 a minute. A call that talks more costs more.
const CHARS_PER_CALL_MINUTE = 511
const OTHER_COST_PER_MINUTE = 0.005
const BUILD_PLAN_CREDIT = 2.5

export function voiceSpeaks(model: string, language: string): boolean {
  const speaks = VOICE_MODELS.find((m) => m.value === model)?.speaks
  return !speaks || speaks.includes(language)
}

/** The voices for this language, cost in the label, plus a saved model no longer on the list. */
export function voiceOptions(language: string, current: string) {
  const options = VOICE_MODELS.filter((m) => voiceSpeaks(m.value, language)).map((m) => ({
    value: m.value,
    label: `${m.name}: $${m.pricePerMillion} per 1M characters`,
  }))
  return VOICE_MODELS.some((m) => m.value === current)
    ? options
    : [...options, { value: current, label: current }]
}

/** What a call on this voice comes to, in the words a person budgets with. */
export function voiceCostLine(model: string): string | undefined {
  const voice = VOICE_MODELS.find((m) => m.value === model)
  if (!voice) return undefined
  const perMinute =
    OTHER_COST_PER_MINUTE + (voice.pricePerMillion * CHARS_PER_CALL_MINUTE) / 1_000_000
  const minutes = Math.round(BUILD_PLAN_CREDIT / perMinute / 10) * 10
  return (
    `About ${(perMinute * 100).toFixed(1)}¢ a call minute with hearing and the front voice, or ` +
    `about ${minutes} minutes on LiveKit's free $${BUILD_PLAN_CREDIT.toFixed(2)} credit. ` +
    `Longer answers cost more, and LiveKit sets the prices.`
  )
}

/** A voice id belongs to one provider: carried to another, the call would have no voice. */
export function voiceIdAfterChange(from: string, to: string, voiceId: string): string {
  return from.split('/')[0] === to.split('/')[0] ? voiceId : ''
}
