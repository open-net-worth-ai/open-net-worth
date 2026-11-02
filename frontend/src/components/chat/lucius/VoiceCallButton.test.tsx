// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../hooks/useFlag'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))
// No real room in jsdom: a call that got a token would connect through livekit-client.
vi.mock('livekit-client', () => ({
  Room: vi.fn(),
  RoomEvent: {},
  Track: { Kind: { Audio: 'audio' } },
}))

import { VoiceCallButton } from './VoiceCallButton'

let restore: () => void = () => {}
afterEach(() => {
  restore()
  cleanup()
  authFetch.mockReset()
})

describe('VoiceCallButton', () => {
  it('is not there while the voice_calls flag is off', () => {
    restore = overrideFlagsForTests({ voice_calls: false })
    render(<VoiceCallButton sessionId="s1" />)
    expect(screen.queryByRole('button', { name: /call lucius/i })).toBeNull()
  })

  it('asks for a call INTO the open conversation and says why one could not start', async () => {
    restore = overrideFlagsForTests({ voice_calls: true })
    authFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: 'Set up Voice calls in Connectors first.' }),
    })
    render(<VoiceCallButton sessionId="s1" />)
    fireEvent.click(screen.getByRole('button', { name: /call lucius/i }))
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('Set up Voice calls'),
    )
    const [url, init] = authFetch.mock.calls[0]
    expect(url).toBe('/api/voice/call')
    expect(JSON.parse(init.body)).toEqual({ session_id: 's1' })
    expect(screen.getByRole('button', { name: /call lucius/i })).toBeTruthy()
  })
})
