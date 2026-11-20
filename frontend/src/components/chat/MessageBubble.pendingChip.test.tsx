// @vitest-environment jsdom
//
// PendingChip — concurrent-conversations-ui-spec.md §5.3 / acceptance C2: a message sent
// into a busy conversation is queued for injection and must be unmistakably marked as
// not-yet-delivered, clearing exactly on delivery (the `pending` field going away).

import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../../types/chat'

afterEach(cleanup)

const noop = vi.fn()

function renderMessage(over: Partial<Message>) {
  const message: Message = { id: 'm1', role: 'user', text: 'Also check the Roth limit', ...over }
  return render(<MessageBubble message={message} onSendMessage={noop} />)
}

describe('MessageBubble — pending (queued) user message', () => {
  it('renders the queued chip with the mechanism-first copy', () => {
    const { container, getByText } = renderMessage({ pending: 'queued' })
    expect(container.querySelector('.msg-pending-chip')).not.toBeNull()
    expect(getByText('Queued — Lucius will read this at its next step')).toBeTruthy()
  })

  it('dims the bubble text while pending (a supporting cue, not the whole story)', () => {
    const { container } = renderMessage({ pending: 'queued' })
    expect(container.querySelector('.msg-user-pending')).not.toBeNull()
  })

  it('renders nothing extra for an ordinary, already-delivered message', () => {
    const { container } = renderMessage({})
    expect(container.querySelector('.msg-pending-chip')).toBeNull()
    expect(container.querySelector('.msg-user-pending')).toBeNull()
  })

  it('clears on delivery — no pending field, no chip, full-opacity bubble', () => {
    const { container, rerender } = render(
      <MessageBubble
        message={{ id: 'm1', role: 'user', text: 'hi', pending: 'queued' }}
        onSendMessage={noop}
      />,
    )
    expect(container.querySelector('.msg-pending-chip')).not.toBeNull()

    rerender(
      <MessageBubble message={{ id: 'm1', role: 'user', text: 'hi' }} onSendMessage={noop} />,
    )

    expect(container.querySelector('.msg-pending-chip')).toBeNull()
    expect(container.querySelector('.msg-user-pending')).toBeNull()
  })
})

describe('MessageBubble — a queued message that never reached Lucius', () => {
  it('says so instead of "Still queued" forever, and offers Retry (live report 2026-11-18)', () => {
    const onRetryQueued = vi.fn()
    const { getByText, getByRole, queryByText } = render(
      <MessageBubble
        message={{ id: 'm9', role: 'user', text: 'sign-up offers?', pending: 'failed' }}
        onSendMessage={noop}
        onRetryQueued={onRetryQueued}
      />,
    )
    expect(getByText('Not delivered — Lucius never read this')).toBeTruthy()
    expect(queryByText(/Queued/)).toBeNull()
    fireEvent.click(getByRole('button', { name: /Retry/ }))
    expect(onRetryQueued).toHaveBeenCalledWith('m9')
  })
})
