// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))
vi.mock('../../../hooks/useModels', () => ({
  useModels: () => ({ models: [], defaultModel: 'acme-default' }),
}))
const chat = vi.hoisted(() => ({
  activeId: 's1',
  conversationModel: '',
  conversations: [] as { id: string; model: string }[],
  setConversationModel: vi.fn(),
}))
vi.mock('../../../state/luciusChat', () => ({ useLuciusChat: () => chat }))
const call = vi.hoisted(() => ({ sessionId: 's1' as string | null }))
vi.mock('./voiceCall', () => ({ useVoiceCall: () => call }))
const picker = vi.hoisted(() => ({
  onChange: (_next: string) => {},
  noSearchFocus: false as boolean | undefined,
}))
vi.mock('../../ui', () => ({
  ModelPicker: (p: {
    value: string
    defaultModelId: string
    noSearchFocus?: boolean
    onChange: (v: string) => void
  }) => {
    picker.onChange = p.onChange
    picker.noSearchFocus = p.noSearchFocus
    return <span>{p.value || p.defaultModelId}</span>
  },
}))

import { VoiceCallModel } from './VoiceCallModel'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  chat.setConversationModel.mockClear()
  chat.activeId = 's1'
  chat.conversationModel = ''
  chat.conversations = []
  call.sessionId = 's1'
})

describe('VoiceCallModel', () => {
  it("names the conversation's model", () => {
    chat.conversationModel = 'acme-chat'
    render(<VoiceCallModel disabled={false} />)
    expect(screen.getByText('acme-chat')).toBeTruthy()
  })

  it('names the default model for a conversation with none of its own', () => {
    render(<VoiceCallModel disabled={false} />)
    expect(screen.getByText('acme-default')).toBeTruthy()
  })

  it("reads the call's conversation when another one is open", () => {
    chat.activeId = 'other'
    chat.conversationModel = 'other-model'
    chat.conversations = [{ id: 's1', model: 'call-model' }]
    render(<VoiceCallModel disabled={false} />)
    expect(screen.getByText('call-model')).toBeTruthy()
  })

  it('opens without the keyboard, which would shove the call up behind it', () => {
    render(<VoiceCallModel disabled={false} />)
    expect(picker.noSearchFocus).toBe(true)
  })

  it("changes the conversation's model, and saves it at once for his next answer", async () => {
    authFetch.mockResolvedValue({ ok: true })
    render(<VoiceCallModel disabled={false} />)
    picker.onChange('acme:fast')
    expect(chat.setConversationModel).toHaveBeenCalledWith('s1', 'acme:fast')
    await waitFor(() => expect(authFetch).toHaveBeenCalledOnce())
    const [url, init] = authFetch.mock.calls[0]
    expect(url).toBe('/api/sessions/s1')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ model: 'acme:fast' })
  })

  it('goes back to the saved model when the save fails', async () => {
    chat.conversationModel = 'acme-chat'
    authFetch.mockResolvedValue({ ok: false, status: 422 })
    render(<VoiceCallModel disabled={false} />)
    picker.onChange('acme:fast')
    await waitFor(() =>
      expect(chat.setConversationModel).toHaveBeenLastCalledWith('s1', 'acme-chat'),
    )
  })
})
