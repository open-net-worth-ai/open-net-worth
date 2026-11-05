import { describe, expect, it } from 'vitest'
import { voiceCostLine, voiceIdAfterChange, voiceOptions, voiceSpeaks } from './voiceOptions'

describe('the voices offered for a call', () => {
  it('shows what each voice costs', () => {
    const labels = voiceOptions('en', 'cartesia/sonic-3').map((o) => o.label)
    expect(labels).toContain('Cartesia Sonic 3 (default): $50 per 1M characters')
    expect(labels).toContain('Inworld 2.0 Flash: $15 per 1M characters')
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
