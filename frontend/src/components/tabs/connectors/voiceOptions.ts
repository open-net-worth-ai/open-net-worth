// The voices offered for a call, with what each costs. Mirrors the backend's
// connectors/voice/tts_models.py for which languages each speaks.
// Prices: LiveKit Inference on the Build and Ship plans, per million characters (livekit.com/pricing/
// inference, read 2026-11-05). ElevenLabs is gone from the list: LiveKit lists it as a plugin needing
// its own key, not an Inference voice, and has no price for it here. A setting saved with one stays.

interface Speaker {
  id: string
  name: string
  /** Only where LiveKit's docs state it: never guessed from a name. */
  gender?: 'female' | 'male'
}

interface VoiceModel {
  value: string
  name: string
  pricePerMillion: number
  /** Only for a voice that speaks fewer than all of the app's languages. */
  speaks?: readonly string[]
  /** The voices LiveKit's docs name for this model (read 2026-11-06), a sample of each library. */
  voices?: readonly Speaker[]
  /** What a blank voice id gets; matches the backend's tts_models.py. None: LiveKit chooses. */
  defaultVoice?: string
}

const INWORLD_VOICES: readonly Speaker[] = [
  { id: 'Ashley', name: 'Ashley', gender: 'female' },
  { id: 'Olivia', name: 'Olivia', gender: 'female' },
  { id: 'Edward', name: 'Edward', gender: 'male' },
  { id: 'Diego', name: 'Diego', gender: 'male' },
]

// The cheapest voice that speaks every language; matches the backend's CONFIG_DEFAULTS.
export const DEFAULT_VOICE_MODEL = 'inworld/inworld-tts-2-flash'

const VOICE_MODELS: readonly VoiceModel[] = [
  {
    value: DEFAULT_VOICE_MODEL,
    name: 'Inworld 2.0 Flash (default)',
    pricePerMillion: 15,
    voices: INWORLD_VOICES,
    defaultVoice: 'Ashley',
  },
  {
    value: 'inworld/inworld-tts-2',
    name: 'Inworld 2.0',
    pricePerMillion: 25,
    voices: INWORLD_VOICES,
    defaultVoice: 'Ashley',
  },
  {
    value: 'cartesia/sonic-3',
    name: 'Cartesia Sonic 3',
    pricePerMillion: 50,
    voices: [
      { id: '5c5ad5e7-1020-476b-8b91-fdcbe9cc313c', name: 'Daniela', gender: 'female' },
      { id: '9626c31c-bec5-4cca-baa8-f8ba9e84c8bc', name: 'Jacqueline', gender: 'female' },
      { id: 'f31cc6a7-c1e8-4764-980c-60a361443dd1', name: 'Robyn', gender: 'female' },
      { id: 'a167e0f3-df7e-4d52-a9c3-f949145efdab', name: 'Blake', gender: 'male' },
    ],
  },
  { value: 'cartesia/sonic-3.6', name: 'Cartesia Sonic 3.6', pricePerMillion: 50 },
  {
    value: 'assemblyai/assemblyai-tts-1',
    name: 'AssemblyAI TTS 1',
    pricePerMillion: 15,
    speaks: ['en', 'es', 'de', 'fr', 'pt'],
    voices: [{ id: '49c443a4-8a1d-5f7e-b70b-98bdbd9823c3', name: 'Audrey' }],
    defaultVoice: '49c443a4-8a1d-5f7e-b70b-98bdbd9823c3',
  },
  {
    value: 'fishaudio/s2.1-pro',
    name: 'Fish Audio S2.1 Pro',
    pricePerMillion: 15,
    speaks: ['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'pt'],
    voices: [
      { id: 'b347db033a6549378b48d00acb0d06cd', name: 'Selene', gender: 'female' },
      { id: 'e3cd384158934cc9a01029cd7d278634', name: 'Laura', gender: 'female' },
      { id: 'bf322df2096a46f18c579d0baa36f41d', name: 'Adrian', gender: 'male' },
      { id: '536d3a5e000945adb7038665781a4aca', name: 'Ethan' },
      { id: '9a9cf47702da476aa4629e2506d4a857', name: 'Hannah' },
      { id: '79d0bd3e4e5444b18f7b6d89b5927bf1', name: 'Jordan' },
      { id: '933563129e564b19a115bedd57b7406a', name: 'Sarah' },
    ],
    defaultVoice: 'b347db033a6549378b48d00acb0d06cd',
  },
]

// Where to find a voice the list doesn't name; each library is far longer than our sample.
const VOICE_LIBRARIES: Record<string, { name: string; url: string }> = {
  cartesia: { name: 'Cartesia', url: 'https://play.cartesia.ai/voices' },
  inworld: { name: 'Inworld', url: 'https://docs.inworld.ai/docs/tts/tts-playground' },
  assemblyai: { name: 'AssemblyAI', url: 'https://www.assemblyai.com/docs/tts/voices' },
  fishaudio: { name: 'Fish Audio', url: 'https://fish.audio/app/discovery/' },
}

export const OTHER_VOICE = 'other'

function speakerLabel(speaker: Speaker, isDefault: boolean): string {
  const details = [speaker.gender, isDefault && 'default'].filter(Boolean)
  return details.length ? `${speaker.name} (${details.join(', ')})` : speaker.name
}

/** The voices to pick from on this model: its default first, then "another voice" for the rest. */
export function speakerOptions(model: string) {
  const { voices = [], defaultVoice } = VOICE_MODELS.find((m) => m.value === model) ?? {}
  const standing = voices.find((s) => s.id === defaultVoice)
  return [
    {
      value: '',
      label: standing ? speakerLabel(standing, true) : 'Default (LiveKit chooses)',
    },
    ...voices
      .filter((s) => s !== standing)
      .map((s) => ({ value: s.id, label: speakerLabel(s, false) })),
    { value: OTHER_VOICE, label: 'Another voice…' },
  ]
}

/** Which menu entry a saved voice id is. An id we don't name is "another voice", and stays. */
export function speakerChoice(model: string, voiceId: string, typing: boolean): string {
  if (typing) return OTHER_VOICE
  const { voices = [], defaultVoice } = VOICE_MODELS.find((m) => m.value === model) ?? {}
  const id = voiceId.trim()
  if (!id || id === defaultVoice) return ''
  return voices.some((s) => s.id === id) ? id : OTHER_VOICE
}

export function voiceLibrary(model: string) {
  return VOICE_LIBRARIES[model.split('/')[0]]
}

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
