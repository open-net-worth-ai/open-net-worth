// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))
const chat = vi.hoisted(() => ({
  activeId: 's1',
  conversationModel: 'acme-model',
  effort: 'medium',
  sendMessage: vi.fn(async () => {}),
  switchConversation: vi.fn(),
}))
vi.mock('../../../state/luciusChat', () => ({ useLuciusChat: () => chat }))
const call = vi.hoisted(() => ({ sessionId: 's1' as string | null }))
vi.mock('./voiceCall', () => ({ useVoiceCall: () => call }))

import { VoiceCallAttach } from './VoiceCallAttach'

const file = new File(['x'], 'acme.pdf', { type: 'application/pdf' })
const pickFile = (container: HTMLElement) =>
  fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } })

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:x')
  authFetch.mockResolvedValue({ ok: true, json: async () => ({ ref_id: 'r1', kind: 'text' }) })
})
afterEach(() => {
  cleanup()
  authFetch.mockReset()
  chat.sendMessage.mockClear()
  chat.switchConversation.mockClear()
  chat.activeId = 's1'
  call.sessionId = 's1'
})

describe('VoiceCallAttach', () => {
  it('sends the file to Lucius with no words of its own, and says so meanwhile', async () => {
    const { container } = render(<VoiceCallAttach disabled={false} busy={false} />)
    pickFile(container)
    expect(screen.getByRole('status').textContent).toBe('Sending your file…')
    await waitFor(() => expect(chat.sendMessage).toHaveBeenCalledOnce())
    const [text, attachments, effort, silent, model] = chat.sendMessage.mock.calls[0] as unknown[]
    expect([text, effort, silent, model]).toEqual(['', 'medium', false, 'acme-model'])
    expect((attachments as { ref_id: string }[])[0]?.ref_id).toBe('r1')
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
  })

  it('waits for Lucius to finish his reply', async () => {
    const { container, rerender } = render(<VoiceCallAttach disabled={false} busy />)
    pickFile(container)
    await waitFor(() => expect(authFetch).toHaveBeenCalledOnce())
    await new Promise((done) => setTimeout(done, 20))
    expect(chat.sendMessage).not.toHaveBeenCalled()
    rerender(<VoiceCallAttach disabled={false} busy={false} />)
    await waitFor(() => expect(chat.sendMessage).toHaveBeenCalledOnce())
  })

  it('says when a file could not be uploaded, instead of leaving you to think he has it', async () => {
    authFetch.mockResolvedValue({ ok: false })
    const { container } = render(<VoiceCallAttach disabled={false} busy={false} />)
    pickFile(container)
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toBe("Couldn't send acme.pdf."),
    )
    expect(chat.sendMessage).not.toHaveBeenCalled()
  })

  it("moves to the call's conversation, and sends nothing until it is there", async () => {
    chat.activeId = 'other'
    const { container, rerender } = render(<VoiceCallAttach disabled={false} busy={false} />)
    fireEvent.click(screen.getByRole('button', { name: 'Attach a file' }))
    expect(chat.switchConversation).toHaveBeenCalledWith('s1')
    pickFile(container)
    await waitFor(() => expect(authFetch).toHaveBeenCalledOnce())
    await new Promise((done) => setTimeout(done, 20))
    expect(chat.sendMessage).not.toHaveBeenCalled()
    chat.activeId = 's1'
    rerender(<VoiceCallAttach disabled={false} busy={false} />)
    await waitFor(() => expect(chat.sendMessage).toHaveBeenCalledOnce())
  })
})
