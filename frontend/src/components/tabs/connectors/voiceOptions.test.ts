import { describe, expect, it } from 'vitest'
import {
  DEFAULT_VOICE_MODEL,
  languagePatch,
  OTHER_VOICE,
  freeMinutesEstimate,
  isolationStandInLine,
  noiseFilterHelp,
  onLiveKitCloud,
  speakerChoice,
  speakerOptions,
  voiceCostLine,
  voiceIdAfterChange,
  voiceLibrary,
  voiceOptions,
  voiceSpeaks,
} from './voiceOptions'

describe('the voices offered for a call', () => {
  it('shows what each voice costs', () => {
    const labels = voiceOptions('en', 'cartesia/sonic-3').map((o) => o.label)
    expect(labels).toContain('Inworld 2.0 Flash (default): $15 per 1M characters')
    expect(labels).toContain('Cartesia Sonic 3: $50 per 1M characters')
  })

  it('defaults to a voice every language can fall back to', () => {
    expect(DEFAULT_VOICE_MODEL).toBe('inworld/inworld-tts-2-flash')
    for (const language of ['en', 'vi', 'zh', 'ja', 'ko', 'hi', 'id', 'es', 'fr', 'de', 'pt']) {
      expect(voiceSpeaks(DEFAULT_VOICE_MODEL, language)).toBe(true)
    }
  })

  it('offers only the voices that speak the language', () => {
    const vi = voiceOptions('vi', 'cartesia/sonic-3').map((o) => o.value)
    expect(vi).toContain('inworld/inworld-tts-2-flash')
    expect(vi).not.toContain('assemblyai/assemblyai-tts-1')
    expect(vi).not.toContain('fishaudio/s2.1-pro')
    expect(voiceSpeaks('assemblyai/assemblyai-tts-1', 'es')).toBe(true)
    expect(voiceSpeaks('fishaudio/s2.1-pro', 'hi')).toBe(false)
  })

  it('keeps a saved voice that has left the list, and invents no price for it', () => {
    const saved = 'elevenlabs/eleven_flash_v2_5'
    expect(voiceOptions('en', saved).at(-1)).toEqual({ value: saved, label: saved })
    expect(voiceCostLine(saved)).toBeUndefined()
  })

  it('puts the cost of his voice in call minutes', () => {
    expect(voiceCostLine('cartesia/sonic-3')).toContain('about 2.6¢ a call minute')
    expect(voiceCostLine('inworld/inworld-tts-2-flash')).toContain('about 0.8¢ a call minute')
  })

  it('drops a voice id only when the provider changes', () => {
    const cartesia = 'cartesia/sonic-3'
    expect(voiceIdAfterChange(cartesia, 'cartesia/sonic-3.6', 'abc')).toBe('abc')
    expect(voiceIdAfterChange(cartesia, 'inworld/inworld-tts-2', 'abc')).toBe('')
  })
})

describe('the free minutes these settings come to', () => {
  const defaults = {
    stt_model: 'auto',
    tts_model: 'inworld/inworld-tts-2-flash',
    front_model: 'google/gemma-4-31b-it',
    noise_filter: 'standard',
  }

  it('is what the credit lasts, whichever noise cancelling is on', () => {
    const estimate = freeMinutesEstimate(defaults)
    expect(estimate?.headline).toBe('Estimated free minutes: about 170 a month')
    expect(estimate?.detail).toContain('$2.50 monthly credit')
    for (const noise_filter of ['off', 'isolation']) {
      expect(freeMinutesEstimate({ ...defaults, noise_filter })?.headline).toBe(estimate?.headline)
    }
  })

  it('says voice isolation covers the first minutes, then Standard', () => {
    const estimate = freeMinutesEstimate({ ...defaults, noise_filter: 'isolation' })
    expect(estimate?.detail).toContain('About the first 100 use voice isolation, then Standard.')
    expect(freeMinutesEstimate(defaults)?.detail).not.toContain('voice isolation')
  })

  it('leaves that out when a dearer voice spends the credit first', () => {
    const estimate = freeMinutesEstimate({
      ...defaults,
      tts_model: 'cartesia/sonic-3',
      noise_filter: 'isolation',
    })
    expect(estimate?.headline).toBe('Estimated free minutes: about 80 a month')
    expect(estimate?.detail).not.toContain('then Standard')
  })

  it('moves with hearing and the front voice too', () => {
    const headline = (changes: Partial<typeof defaults>) =>
      freeMinutesEstimate({ ...defaults, ...changes })?.headline
    expect(headline({ stt_model: 'assemblyai/universal-streaming' })).toContain('about 200')
    expect(headline({ front_model: 'openai/gpt-4.1-nano' })).toContain('about 190')
  })

  it('says nothing for a voice with no price', () => {
    expect(freeMinutesEstimate({ ...defaults, tts_model: 'elevenlabs/x' })).toBeUndefined()
  })
})

describe('noise cancelling', () => {
  it('explains the option picked', () => {
    expect(noiseFilterHelp('standard')).toContain('keeps every voice')
    expect(noiseFilterHelp('isolation')).toContain('100 minutes a month')
  })

  it('says when new calls are standing in on Standard, and until when', () => {
    const now = Date.parse('2026-11-08T10:00:00+08:00')
    expect(isolationStandInLine('', now)).toBe('')
    expect(isolationStandInLine('2026-11-08T11:00:00+08:00', now)).toContain('within the hour')
    const nextMonth = isolationStandInLine('2026-12-01T00:00:00+08:00', now)
    expect(nextMonth).toContain('new calls use Standard')
    expect(nextMonth).toContain('Dec')
  })

  it('is offered on LiveKit Cloud only', () => {
    expect(onLiveKitCloud('wss://acme.livekit.cloud')).toBe(true)
    expect(onLiveKitCloud('')).toBe(true)
    expect(onLiveKitCloud('wss://livekit.example.com')).toBe(false)
    expect(onLiveKitCloud('wss://acme.livekit.cloud.example.com')).toBe(false)
  })
})

describe('who speaks on a voice model', () => {
  const flash = 'inworld/inworld-tts-2-flash'
  const labels = (model: string) => speakerOptions(model).map((o) => o.label)

  it('offers male and female voices, its default first and marked', () => {
    expect(labels(flash)).toEqual([
      'Dennis (male, default)',
      'Edward (male)',
      'Diego (male)',
      'Victor',
      'Theodore',
      'Ashley (female)',
      'Olivia (female)',
      'Another voice…',
    ])
    expect(speakerOptions(flash)[0]?.value).toBe('')
    expect(speakerOptions(flash)[1]?.value).toBe('Edward')
  })

  it('states no gender the docs do not state', () => {
    expect(labels('assemblyai/assemblyai-tts-1')[0]).toBe('Audrey (default)')
    expect(labels('fishaudio/s2.1-pro')).toContain('Ethan')
  })

  it('defaults to a man wherever one is documented', () => {
    expect(labels('cartesia/sonic-3')[0]).toBe('Daniel (male, default)')
    expect(labels('cartesia/sonic-3')).toContain('Blake (male)')
    expect(labels('cartesia/sonic-3.6')[0]).toBe('Daniel (male, default)')
    expect(labels('fishaudio/s2.1-pro')[0]).toBe('Adrian (male, default)')
  })

  it('says when LiveKit chooses, on a model we know no voices for', () => {
    expect(labels('elevenlabs/eleven_flash_v2_5')).toEqual([
      'Default (LiveKit chooses)',
      'Another voice…',
    ])
  })

  it('reads a saved id back as the right entry', () => {
    expect(speakerChoice(flash, '', false)).toBe('')
    expect(speakerChoice(flash, 'Dennis', false)).toBe('')
    expect(speakerChoice(flash, 'Edward', false)).toBe('Edward')
    expect(speakerChoice(flash, 'Someone-Else', false)).toBe(OTHER_VOICE)
    expect(speakerChoice('cartesia/sonic-3.6', 'abc', false)).toBe(OTHER_VOICE)
  })

  it('points at the library of the model it is on', () => {
    expect(voiceLibrary(flash)?.name).toBe('Inworld')
    expect(voiceLibrary('elevenlabs/eleven_flash_v2_5')).toBeUndefined()
  })
})

describe('what changes with the language', () => {
  const flash = 'inworld/inworld-tts-2-flash'

  it('changes nothing when the models already handle it', () => {
    expect(
      languagePatch({ stt_model: 'auto', tts_model: flash, tts_voice: 'Edward' }, 'vi'),
    ).toEqual({})
  })

  it('moves an English-only ear to Automatic, except for English', () => {
    const stt_model = 'assemblyai/universal-streaming'
    expect(languagePatch({ stt_model, tts_model: flash }, 'vi').stt_model).toBe('auto')
    expect(languagePatch({ stt_model, tts_model: flash }, 'en')).toEqual({})
  })

  it("moves a model that hears some languages when it can't hear this one", () => {
    const stt_model = 'assemblyai/universal-streaming-multilingual'
    expect(languagePatch({ stt_model, tts_model: flash }, 'vi').stt_model).toBe('auto')
    expect(languagePatch({ stt_model, tts_model: flash }, 'es')).toEqual({})
  })

  it("moves a voice that can't speak it to the default, dropping the other provider's id", () => {
    const patch = languagePatch(
      { stt_model: 'auto', tts_model: 'assemblyai/assemblyai-tts-1', tts_voice: 'abc' },
      'vi',
    )
    expect(patch).toEqual({ tts_model: DEFAULT_VOICE_MODEL, tts_voice: '' })
  })
})
