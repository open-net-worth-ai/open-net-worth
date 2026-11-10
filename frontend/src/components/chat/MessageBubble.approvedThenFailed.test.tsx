// @vitest-environment jsdom
//
// REGRESSION (demo instance, 2026-10-26): the user tapped "Allow once" on a create_automation
// card, the card switched to a green "✓ Approved", and only then did the handler come back with
// `{"ok": false, "error": "unknown automation schedule type None …"}`. The failure showed on the
// tool row alone; the card went on asserting an approval that achieved nothing. The card and
// the call share one tool_use_id, so the card reads its call's own result.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'
import type { Message, ProposedChange } from '../../types/chat'

afterEach(cleanup)

const ERROR = "unknown automation schedule type None — a schedule trigger needs a 'type' key"

function renderAnswered(status: 'confirmed' | 'dismissed', result: string) {
  const change: ProposedChange = {
    type: 'tool_permission',
    grant_tool: 'create_automation',
    id: 'toolu-a',
    session_id: 'sess-1',
    status,
  }
  const message: Message = {
    id: 'm1',
    role: 'assistant',
    text: '',
    blocks: [
      { type: 'tool', name: 'create_automation', id: 'toolu-a', status: 'done', result },
      { type: 'change', change },
    ],
    proposedChanges: [change],
  }
  return render(<MessageBubble message={message} onSendMessage={vi.fn()} />)
}

const resolvedRow = (c: HTMLElement) => c.querySelector('.change-preview .change-confirmed')

describe('MessageBubble — an approved call that failed says so on its card', () => {
  it('shows the failure, not a green tick, when the approved call returned an error', () => {
    const { container } = renderAnswered('confirmed', JSON.stringify({ ok: false, error: ERROR }))
    const row = resolvedRow(container)
    expect(row?.textContent).toBe(`✗ Approved, but it failed: ${ERROR}`)
    expect(row?.classList.contains('change-confirmed--failed')).toBe(true)
  })

  it('keeps "✓ Approved" when the approved call succeeded', () => {
    const { container } = renderAnswered('confirmed', JSON.stringify({ ok: true }))
    const row = resolvedRow(container)
    expect(row?.textContent).toBe('✓ Approved')
    expect(row?.classList.contains('change-confirmed--failed')).toBe(false)
  })

  it('a denied card stays "✗ Denied" — its declined result is not a failure of the call', () => {
    const declined = JSON.stringify({ ok: false, error: 'the user declined create_automation' })
    const { container } = renderAnswered('dismissed', declined)
    expect(resolvedRow(container)?.textContent).toBe('✗ Denied')
  })
})
