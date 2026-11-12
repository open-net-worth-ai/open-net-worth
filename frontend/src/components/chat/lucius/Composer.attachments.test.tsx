// @vitest-environment jsdom
//
// Sending with a file attached (QA, 2026-10-14: a CSV plus a typed message "would not send",
// the text stayed in the box with no reason, and the retry typed the message in twice).
//
// A send is never thrown away silently: it waits for an upload that is still running, says why
// when it refuses, and the composer empties completely (text AND chips) after one that goes.

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LuciusComposer } from './Composer'
import { ATTACH_ACCEPT } from './useComposerAttachments'
import type { ComposerParsed, LuciusComposerHandle, LuciusComposerProps } from './Composer'

vi.mock('../../../state/chatComposer', () => ({
  useChatComposer: () => ({
    pendingMentions: [],
    respondingAgent: null,
    pendingSkills: [],
    setPendingSkills: vi.fn(),
    clearPendingMentions: vi.fn(),
  }),
}))
vi.mock('../../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({ enabled: false, pinnedHandleIds: [] }),
}))
vi.mock('../../../lucius/screenContract', () => ({
  getScreenSnapshot: () => ({ title: 'Test', facts: [], raw: '', handles: [] }),
  captureAndSerialize: () => null,
  allHandles: () => [],
}))
vi.mock('../../../lucius/identity', () => ({
  GLYPH_SVG: {},
  TYPE_MARKER_SVG: {},
  hSvg: () => '',
  Spark: () => null,
  Identity: () => null,
  IdentityInner: () => null,
}))
vi.mock('../../../hooks/useMentionables', () => ({
  useMentionables: () => ({ items: [], loading: false }),
}))

// Each upload is a promise the test settles by hand, so "still uploading" is a state it can hold.
const uploads: Array<{ resolve: (r: Response) => void; reject: (e: Error) => void }> = []
vi.mock('../../../api/client', () => ({
  authFetch: () =>
    new Promise<Response>((resolve, reject) => {
      uploads.push({ resolve, reject })
    }),
}))
URL.createObjectURL = () => 'blob:test'
URL.revokeObjectURL = () => {}

const reply = (body: unknown, ok = true) => ({ ok, json: async () => body }) as unknown as Response

function mount(props: Partial<LuciusComposerProps> = {}) {
  const ref = createRef<LuciusComposerHandle>()
  const sent: ComposerParsed[] = []
  const composer = (extra: Partial<LuciusComposerProps>) => (
    <LuciusComposer
      ref={ref}
      // What ChatPanel.handleSend does after dispatching.
      onSend={(p) => {
        sent.push(p)
        ref.current?.clear()
      }}
      visibleIds={new Set<string>()}
      screenAwarenessDisabled
      {...props}
      {...extra}
    />
  )
  const view = render(composer({}))
  return {
    ref,
    sent,
    update: (extra: Partial<LuciusComposerProps>) => view.rerender(composer(extra)),
  }
}

function attach(name: string) {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')!
  Object.defineProperty(input, 'files', {
    value: [new File(['a,b\n1,2\n'], name, { type: 'text/csv' })],
    configurable: true,
  })
  fireEvent.change(input)
}

function type(text: string) {
  const editor = screen.getByRole('textbox')
  editor.textContent = text
  fireEvent.input(editor)
  return editor
}

const sendButton = () => screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement
// The send notice; the per-file reason under the chips is its own note.
const note = () => document.querySelector('.lx-send-note:not([data-upload-failures])')
const failNote = () => document.querySelector('[data-upload-failures]')
const chips = () => document.querySelectorAll('.lx-att')
const finishUpload = (i = 0) =>
  act(async () => {
    uploads[i]!.resolve(reply({ ref_id: `ref-${i}`, kind: 'text', summary: 'CSV, 1 row' }))
    await Promise.resolve()
  })

beforeEach(() => {
  uploads.length = 0
})
afterEach(cleanup)

describe('send while a file is still uploading', () => {
  it('stays enabled and waits instead of doing nothing', () => {
    const { sent } = mount()
    attach('dbs-september.csv')
    type("Here's last month's DBS statement")
    expect(sendButton().disabled).toBe(false)

    fireEvent.click(sendButton())

    expect(sent).toHaveLength(0)
    expect(note()?.textContent).toContain('dbs-september.csv')
    expect(note()?.textContent).toContain('finishes uploading')
    expect(note()?.getAttribute('role')).toBe('status')
  })

  it('sends with the file the moment the upload lands, and leaves nothing behind', async () => {
    const { sent } = mount()
    attach('dbs-september.csv')
    const editor = type("Here's last month's DBS statement")
    fireEvent.click(sendButton())

    await finishUpload()

    expect(sent).toHaveLength(1)
    expect(sent[0]!.text).toBe("Here's last month's DBS statement")
    expect(sent[0]!.attachments.map((a) => a.ref_id)).toEqual(['ref-0'])
    // Text AND chip are gone, and so is the waiting notice.
    expect(editor.textContent).toBe('')
    expect(chips()).toHaveLength(0)
    expect(note()).toBeNull()
  })

  it('Enter waits the same way', async () => {
    const { sent } = mount()
    attach('a.csv')
    const editor = type('please bring it in')
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(sent).toHaveLength(0)
    expect(note()).not.toBeNull()

    await finishUpload()
    expect(sent).toHaveLength(1)
  })

  it('is called off by removing the file it was waiting for', async () => {
    const { sent } = mount()
    attach('a.csv')
    type('hello')
    fireEvent.click(sendButton())

    fireEvent.click(document.querySelector('.lx-att-x')!)
    await finishUpload()

    expect(sent).toHaveLength(0)
    expect(note()).toBeNull()
  })
})

describe('a send that is refused says why', () => {
  it('a failed upload blocks the send, names the file and keeps the text', async () => {
    const { sent } = mount()
    attach('statement.pdf')
    const editor = type('here you go')
    await act(async () => {
      uploads[0]!.resolve(reply({ detail: 'too large' }, false))
      await Promise.resolve()
    })
    expect(document.querySelector('.lx-att--failed')).not.toBeNull()
    // The reason shows the moment the upload fails — before any Send.
    expect(failNote()?.textContent).toBe("statement.pdf wasn't uploaded. too large")

    fireEvent.click(sendButton())

    expect(sent).toHaveLength(0)
    expect(note()?.textContent).toBe("Remove statement.pdf to send this — it wasn't uploaded.")
    expect(note()?.getAttribute('role')).toBe('alert')
    expect(editor.textContent).toBe('here you go')
  })

  it('a network failure is the same refusal', async () => {
    const { sent } = mount()
    attach('statement.pdf')
    type('here you go')
    await act(async () => {
      uploads[0]!.reject(new Error('offline'))
      await Promise.resolve()
    })
    expect(failNote()?.textContent).toContain('The connection dropped before it finished.')
    fireEvent.click(sendButton())
    expect(sent).toHaveLength(0)
  })

  it('a refusal with no reason from the server still says what happened', async () => {
    mount()
    attach('statement.pdf')
    type('here you go')
    await act(async () => {
      uploads[0]!.resolve({ ok: false, status: 413, json: async () => null } as unknown as Response)
      await Promise.resolve()
    })
    expect(failNote()?.textContent).toContain('over the 25 MB limit')
  })

  it('the file picker offers what the server reads, and not Word documents', () => {
    expect(ATTACH_ACCEPT).toContain('.xls')
    expect(ATTACH_ACCEPT).toContain('.ofx')
    expect(ATTACH_ACCEPT).not.toContain('.docx')
  })

  it('removing the failed file lets the message go, and editing clears the notice', async () => {
    const { sent } = mount()
    attach('statement.pdf')
    type('here you go')
    await act(async () => {
      uploads[0]!.resolve(reply({}, false))
      await Promise.resolve()
    })
    fireEvent.click(sendButton())
    expect(note()).not.toBeNull()

    fireEvent.click(document.querySelector('.lx-att-x')!)
    expect(note()).toBeNull()
    fireEvent.click(sendButton())
    expect(sent).toHaveLength(1)
    expect(sent[0]!.attachments).toEqual([])
  })

  it('a busy conversation with multi-turn off says so instead of ignoring Enter', () => {
    const { sent } = mount({ sendBlocked: true })
    const editor = type('while you are replying')
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(sent).toHaveLength(0)
    expect(note()?.textContent).toContain('still replying')
    expect(editor.textContent).toBe('while you are replying')
  })
})

describe('a refusal because a reply is running', () => {
  it('goes away when the reply ends, and the next Enter sends the text exactly once', () => {
    // Persona QA round 5 (Retry): the text sat in the box under "still replying" long after
    // the retried reply had finished, and a second typing of it went out beside the first.
    const { sent, update } = mount({ sendBlocked: true })
    const editor = type('and my cards?')
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(note()?.textContent).toContain('still replying')

    update({ sendBlocked: false })
    expect(note()).toBeNull()
    expect(editor.textContent).toBe('and my cards?')

    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(sent.map((p) => p.text)).toEqual(['and my cards?'])
    expect(editor.textContent).toBe('')
  })

  it('does not drop a refusal that is about something else, such as a failed upload', () => {
    const { update } = mount({ sendBlocked: false })
    attach('a.csv')
    const editor = type('here')
    act(() => {
      uploads[0]!.resolve(reply({}, false))
    })
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(note()).not.toBeNull()
    update({ sendBlocked: false })
    expect(note()).not.toBeNull()
  })
})

describe('the send button', () => {
  it('is announced as Send', () => {
    mount()
    expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy()
  })
})
