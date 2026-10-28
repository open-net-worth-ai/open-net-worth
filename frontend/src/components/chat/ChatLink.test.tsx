// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

const navigate = vi.fn()
const switchConversation = vi.fn()
let chat: { switchConversation: typeof switchConversation } | null = null

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
vi.mock('../../state/luciusChat', () => ({ useLuciusChatOptional: () => chat }))

import { ChatLink } from './ChatLink'

beforeEach(() => {
  navigate.mockReset()
  switchConversation.mockReset()
  chat = { switchConversation }
})

describe('ChatLink', () => {
  it('switches the chat it sits in to a past conversation, without navigating', () => {
    render(<ChatLink href="/lucius?conversation=chat%2Fold">Car insurance renewal</ChatLink>)

    fireEvent.click(screen.getByText('Car insurance renewal'))

    expect(switchConversation).toHaveBeenCalledWith('chat/old')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('navigates to the deep link when there is no chat to switch', () => {
    chat = null
    render(<ChatLink href="/lucius?conversation=abc">Old chat</ChatLink>)

    fireEvent.click(screen.getByText('Old chat'))

    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/lucius', search: { conversation: 'abc' } }),
    )
  })

  it('leaves a modified click to the browser and other in-app links to the router', () => {
    render(
      <>
        <ChatLink href="/lucius?conversation=abc">New tab</ChatLink>
        <ChatLink href="/accounts">Accounts</ChatLink>
      </>,
    )

    fireEvent.click(screen.getByText('New tab'), { metaKey: true })
    fireEvent.click(screen.getByText('Accounts'))

    expect(switchConversation).not.toHaveBeenCalled()
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ to: '/accounts' }))
  })
})
