import { describe, expect, it } from 'vitest'
import {
  DEFAULT_VOICE_MODEL,
  OTHER_VOICE,
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

  it('puts the cost in call minutes and in the free credit', () => {
    expect(voiceCostLine('cartesia/sonic-3')).toContain('About 3.1¢ a call minute')
    expect(voiceCostLine('cartesia/sonic-3')).toContain('about 80 minutes')
    expect(voiceCostLine('inworld/inworld-tts-2')).toContain('about 140 minutes')
    expect(voiceCostLine('inworld/inworld-tts-2-flash')).toContain('about 200 minutes')
  })

  it('drops a voice id only when the provider changes', () => {
    const cartesia = 'cartesia/sonic-3'
    expect(voiceIdAfterChange(cartesia, 'cartesia/sonic-3.6', 'abc')).toBe('abc')
    expect(voiceIdAfterChange(cartesia, 'inworld/inworld-tts-2', 'abc')).toBe('')
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
    expect(speakerOptions(flash)[0].value).toBe('')
    expect(speakerOptions(flash)[1].value).toBe('Edward')
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
