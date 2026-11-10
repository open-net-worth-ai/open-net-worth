// @vitest-environment jsdom
//
// The Restore control under a user message has three faces, all rebuilt from the server on
// reload: plain Restore, "Restored to before this message · Undo", and NOTHING while a
// restore of an earlier message has rewound past this turn (`checkpointSuperseded`) —
// restoring a superseded turn would replay before-images the earlier restore already
// rewrote. Undoing that earlier restore clears the flag, so the control comes back.

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../../types/chat'

afterEach(cleanup)

function renderMessage(over: Partial<Message>, onRestore = vi.fn()) {
  const message: Message = { id: 'm1', role: 'user', text: 'log a coffee', ...over }
  return render(
    <MessageBubble
      message={message}
      onSendMessage={vi.fn()}
      onPreviewCheckpoint={vi.fn()}
      onRestoreCheckpoint={onRestore}
    />,
  )
}

describe('MessageBubble — an answer never read out on a call', () => {
  const answer = { role: 'assistant' as const, text: 'Your balance is fine.', blocks: [] }

  it('says so, and keeps the answer', () => {
    const { getByText } = renderMessage({ ...answer, unheard: true })
    expect(getByText(/Not read aloud/)).toBeTruthy()
    expect(getByText('Your balance is fine.')).toBeTruthy()
  })

  it('says nothing on an answer that was heard', () => {
    const { queryByText } = renderMessage(answer)
    expect(queryByText(/Not read aloud/)).toBeNull()
  })
})

describe('MessageBubble — Restore control', () => {
  it('shows Restore on a checkpointed message', () => {
    const { container } = renderMessage({ checkpointId: 'cp_1' })
    expect(container.querySelector('.msg-restore-trigger')).not.toBeNull()
  })

  it('shows Restored with a working Undo after a restore', async () => {
    const onRestore = vi.fn().mockResolvedValue({})
    const { container, getByText } = renderMessage(
      { checkpointId: 'cp_1', restored: true, undoCheckpointId: 'cp_pre' },
      onRestore,
    )
    expect(container.querySelector('.msg-restore-done')).not.toBeNull()
    fireEvent.click(getByText('Undo'))
    await waitFor(() => expect(onRestore).toHaveBeenCalledWith('cp_pre', 'data'))
  })

  it('shows Restored without an Undo once the undo checkpoint is gone', () => {
    const { container, queryByText } = renderMessage({ checkpointId: 'cp_1', restored: true })
    expect(container.querySelector('.msg-restore-done')).not.toBeNull()
    expect(queryByText('Undo')).toBeNull()
  })

  it('shows no control at all while a later restore has superseded the turn', () => {
    const { container } = renderMessage({ checkpointId: 'cp_2', checkpointSuperseded: true })
    expect(container.querySelector('.msg-restore')).toBeNull()
    expect(container.querySelector('.msg-restore-done')).toBeNull()
  })

  it('hides a superseded turn’s own Restored · Undo too', () => {
    const { container } = renderMessage({
      checkpointId: 'cp_3',
      restored: true,
      undoCheckpointId: 'cp_pre',
      checkpointSuperseded: true,
    })
    expect(container.querySelector('.msg-restore-done')).toBeNull()
  })
})

// The popover used to open DOWNWARD inside the thread, so on the last message at a short
// window height it slid under the composer and past the window edge — its buttons could not
// be clicked (visual QA, 2026-10-12). It is now portaled to <body> and placed against the
// trigger: above it when there is no room below.
describe('MessageBubble — Restore popover placement', () => {
  const box = (top: number, height: number, right: number, width: number) =>
    ({
      top,
      bottom: top + height,
      left: right - width,
      right,
      width,
      height,
      x: right - width,
      y: top,
      toJSON: () => ({}),
    }) as DOMRect

  let innerHeight: number
  beforeEach(() => {
    innerHeight = window.innerHeight
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 600 })
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 })
  })
  afterEach(() => {
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: innerHeight })
    vi.restoreAllMocks()
  })

  function geometry(triggerTop: number) {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: HTMLElement,
    ) {
      if (this.classList.contains('msg-restore-trigger')) return box(triggerTop, 20, 780, 70)
      if (this.classList.contains('msg-restore-popover')) return box(0, 190, 260, 260)
      return box(0, 0, 0, 0)
    })
  }

  async function openPopover(onRestore = vi.fn().mockResolvedValue({})) {
    const onPreview = vi.fn().mockResolvedValue({ changes: [], cross_session_changes: [] })
    const utils = render(
      <MessageBubble
        message={{ id: 'm1', role: 'user', text: 'rename it', checkpointId: 'cp_3' }}
        onSendMessage={vi.fn()}
        onPreviewCheckpoint={onPreview}
        onRestoreCheckpoint={onRestore}
      />,
    )
    fireEvent.click(utils.container.querySelector('.msg-restore-trigger') as HTMLElement)
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    const popover = document.body.querySelector('.msg-restore-popover') as HTMLElement
    return { ...utils, popover, onRestore }
  }

  it('is portaled out of the thread, so the composer cannot cover it', async () => {
    geometry(100)
    const { container, popover } = await openPopover()
    expect(popover).not.toBeNull()
    expect(container.contains(popover)).toBe(false)
    expect(popover.parentElement).toBe(document.body)
  })

  it('opens below a message with room under it', async () => {
    geometry(100)
    const { popover } = await openPopover()
    await waitFor(() => expect(popover.style.top).toBe('124px'))
    expect(popover.style.visibility).not.toBe('hidden')
  })

  it('opens ABOVE the last message when the window has no room below it', async () => {
    geometry(389)
    const { popover } = await openPopover()
    await waitFor(() => expect(popover.style.top).toBe(`${389 - 4 - 190}px`))
  })

  it('its Restore data button still restores', async () => {
    geometry(389)
    const { popover, onRestore } = await openPopover()
    fireEvent.click(within(popover).getByText('Restore data'))
    await waitFor(() => expect(onRestore).toHaveBeenCalledWith('cp_3', 'data'))
  })

  it('a click inside it does not close it; a click elsewhere does', async () => {
    geometry(100)
    const { popover } = await openPopover()
    fireEvent.pointerDown(popover)
    expect(document.body.querySelector('.msg-restore-popover')).not.toBeNull()
    fireEvent.pointerDown(document.body)
    expect(document.body.querySelector('.msg-restore-popover')).toBeNull()
  })

  it('Escape closes it', async () => {
    geometry(100)
    await openPopover()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(document.body.querySelector('.msg-restore-popover')).toBeNull()
  })

  it('Cancel closes it', async () => {
    geometry(100)
    const { popover } = await openPopover()
    fireEvent.click(within(popover).getByText('Cancel'))
    expect(document.body.querySelector('.msg-restore-popover')).toBeNull()
  })
})

describe('the Restore preview reads in words', () => {
  async function previewText(preview: object): Promise<string> {
    const onPreview = vi.fn().mockResolvedValue(preview)
    const utils = render(
      <MessageBubble
        message={{ id: 'm1', role: 'user', text: 'book the rows', checkpointId: 'cp_1' }}
        onSendMessage={vi.fn()}
        onPreviewCheckpoint={onPreview}
        onRestoreCheckpoint={vi.fn()}
      />,
    )
    fireEvent.click(utils.container.querySelector('.msg-restore-trigger') as HTMLElement)
    await waitFor(() =>
      expect(document.body.querySelector('.msg-restore-preview')?.textContent).not.toBe(
        'Checking impact…',
      ),
    )
    return (document.body.querySelector('.msg-restore-popover') as HTMLElement).textContent ?? ''
  }

  it('names database tables, never db:<table>', async () => {
    const text = await previewText({
      changes: [
        { file: 'db:transactions', current_count: 5, restored_count: 2, delta: -3 },
        { file: 'db:import_batches', current_count: 1, restored_count: 0, delta: -1 },
        { file: 'db:automation_runs', current_count: 1, restored_count: 1, delta: 0 },
      ],
      cross_session_changes: [],
    })
    expect(text).toContain(
      'remove 3 transactions, remove 1 import batches, revert Automation run history.',
    )
    expect(text).not.toContain('db:')
  })

  it('shows an Automation with no message as its name alone, without empty quotes', async () => {
    const text = await previewText({
      changes: [],
      cross_session_changes: [
        { session_label: 'the Email receipts Automation', message_preview: '', created_at: 'x' },
      ],
    })
    expect(text).toContain('the Email receipts Automation')
    expect(text).not.toContain('“”')
  })
})
