// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { LuciusMobile } from './LuciusMobile'

const mockChat: { conversations: { id: string; title: string }[]; [k: string]: unknown } = {
  conversations: [],
  activeId: 'a',
  createConversation: vi.fn(),
  switchConversation: vi.fn(),
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  statusFor: () => 'idle' as const,
  stopConversation: vi.fn(),
  messages: [],
}
vi.mock('../../../state/luciusChat', () => ({ useLuciusChat: () => mockChat }))
vi.mock('../../chat/ChatPanel', () => ({
  ChatPanel: () => <div data-testid="chat-panel" />,
  conversationGroups: () => [],
  conversationWhen: () => '',
}))
vi.mock('../../vault/VaultPanel', () => ({
  VaultPanel: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="vault-panel">
      <button type="button" onClick={onClose}>
        Close Documents
      </button>
    </div>
  ),
}))

afterEach(() => {
  mockChat.conversations = []
  mockChat.activeId = 'a'
  mockChat.messages = []
  vi.mocked(mockChat.createConversation as () => void).mockClear()
  cleanup()
  sessionStorage.clear()
})

describe('LuciusMobile — Documents', () => {
  it('opens as a sheet over the chat, which stays behind it', async () => {
    render(<LuciusMobile />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    const sheet = await screen.findByRole('dialog', { name: 'Documents' })
    expect(within(sheet).getByTestId('vault-panel')).toBeTruthy()
    expect(screen.getByTestId('chat-panel')).toBeTruthy()
    fireEvent.click(within(sheet).getByRole('button', { name: 'Close Documents' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('gets out of the way when Lucius is asked from a long-press menu', async () => {
    render(<LuciusMobile />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await screen.findByRole('dialog', { name: 'Documents' })
    act(() => {
      window.dispatchEvent(new Event('lx-dock-open'))
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(sessionStorage.getItem('luciusDocumentsOpen')).toBeNull()
  })
})

describe('LuciusMobile — conversation title', () => {
  it('names the open conversation, and tapping it opens the conversation menu', () => {
    mockChat.conversations = [{ id: 'a', title: 'Cash drift after March import' }]
    render(<LuciusMobile />)
    fireEvent.click(screen.getByRole('button', { name: 'Cash drift after March import' }))
    expect(screen.getByRole('dialog', { name: 'Conversations' })).toBeTruthy()
  })

  it('shows no title on an unsaved new conversation', () => {
    mockChat.conversations = [{ id: 'b', title: 'Some other chat' }]
    render(<LuciusMobile />)
    expect(screen.queryByRole('button', { name: 'Some other chat' })).toBeNull()
    expect(document.querySelector('.lucius-mobile-head-title')).toBeNull()
  })
})

describe('LuciusMobile — New conversation', () => {
  it('does nothing on an empty draft', () => {
    render(<LuciusMobile />)
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(mockChat.createConversation).not.toHaveBeenCalled()
  })

  it('starts one after a voice call filled a conversation the list has not caught up with', () => {
    mockChat.messages = [{ role: 'user', content: 'Check my ACME performance' }]
    render(<LuciusMobile />)
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(mockChat.createConversation).toHaveBeenCalledTimes(1)
  })
})
