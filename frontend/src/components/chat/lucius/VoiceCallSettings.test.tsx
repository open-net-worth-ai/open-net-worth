// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))
const invalidate = vi.hoisted(() => vi.fn())
vi.mock('../../../state/queryCache', () => ({ invalidateQueryCache: invalidate }))
const config = vi.hoisted(() => ({
  current: {
    ready: true,
    language: 'en',
    stt_model: 'assemblyai/universal-streaming',
    tts_model: 'inworld/inworld-tts-2-flash',
    tts_voice: '',
  },
}))
vi.mock('./VoiceCallButton', () => ({
  useVoiceReadiness: () => config.current,
  VOICE_CONFIG_KEY: 'voice-config',
}))
const restartCall = vi.hoisted(() => vi.fn(async (change: () => Promise<void>) => change()))
vi.mock('./voiceCall', () => ({ restartCall }))

import { VoiceCallSettings } from './VoiceCallSettings'

const open = () => fireEvent.click(screen.getByRole('button', { name: 'Voice settings' }))
const pick = (label: string, value: string) =>
  fireEvent.change(screen.getByRole('combobox', { name: label }), { target: { value } })
const saved = () => JSON.parse(authFetch.mock.calls[0]![1].body)

beforeEach(() => {
  authFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
})
afterEach(() => {
  cleanup()
  authFetch.mockReset()
  restartCall.mockClear()
  invalidate.mockClear()
  config.current.tts_voice = ''
})

describe('VoiceCallSettings', () => {
  it('opens on the current language and voice', () => {
    render(<VoiceCallSettings ended={false} />)
    open()
    const sheet = screen.getByRole('dialog', { name: 'Voice settings' })
    expect(sheet.textContent).toContain('English')
    expect(sheet.textContent).toContain('Dennis (male, default)')
  })

  it('marks the language as beta, and only the language', () => {
    render(<VoiceCallSettings ended={false} />)
    open()
    expect(screen.getAllByText('Beta')).toHaveLength(1)
    expect(screen.getByText('Beta').closest('label')?.textContent).toContain('Language')
  })

  it('restarts the call in the new language, with an ear that can hear it', async () => {
    render(<VoiceCallSettings ended={false} />)
    open()
    pick('Language', 'vi')
    expect(restartCall).toHaveBeenCalledOnce()
    await waitFor(() => expect(authFetch).toHaveBeenCalledOnce())
    expect(authFetch.mock.calls[0]![0]).toBe('/api/voice/config')
    expect(saved()).toEqual({ language: 'vi', stt_model: 'auto' })
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith('voice-config'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('restarts the call with the new voice', async () => {
    render(<VoiceCallSettings ended={false} />)
    open()
    pick('Voice', 'Edward')
    await waitFor(() => expect(authFetch).toHaveBeenCalledOnce())
    expect(saved()).toEqual({ tts_voice: 'Edward' })
  })

  it('keeps a voice id from settings on the menu, and offers no way to type one here', () => {
    config.current.tts_voice = 'Someone-Else'
    render(<VoiceCallSettings ended={false} />)
    open()
    const sheet = screen.getByRole('dialog', { name: 'Voice settings' })
    expect(sheet.textContent).toContain('Your own voice')
    expect(sheet.textContent).not.toContain('Another voice')
  })

  it('leaves the call alone when you close it without changing anything', () => {
    render(<VoiceCallSettings ended={false} />)
    open()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    open()
    fireEvent.click(screen.getByRole('button', { name: 'Close voice settings' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(restartCall).not.toHaveBeenCalled()
  })

  it('closes when pulled down, like every other sheet', async () => {
    render(<VoiceCallSettings ended={false} />)
    open()
    const sheet = screen.getByRole('dialog', { name: 'Voice settings' })
    Object.defineProperty(sheet, 'offsetHeight', { value: 300, configurable: true })
    fireEvent.pointerDown(sheet, { clientY: 200, pointerId: 1 })
    await new Promise((done) => setTimeout(done, 150))
    fireEvent.pointerMove(sheet, { clientY: 440, pointerId: 1 })
    fireEvent.pointerUp(sheet, { clientY: 440, pointerId: 1 })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('closes as the call ends', () => {
    const { rerender } = render(<VoiceCallSettings ended={false} />)
    open()
    rerender(<VoiceCallSettings ended />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
