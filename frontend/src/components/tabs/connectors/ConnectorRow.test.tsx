// @vitest-environment jsdom
//
// Row anatomy (spec §2). The row answers two questions and stops — these tests pin the
// things a later change would plausibly re-add (a toggle, a Configure button, a sub-line)
// and the single status vocabulary.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { ConnectorRow } from './ConnectorRow'
import type { ConnectorEntry } from './model'

afterEach(cleanup)

function entry(overrides: Partial<ConnectorEntry> = {}): ConnectorEntry {
  return {
    id: 'telegram',
    name: 'Telegram',
    kind: 'telegram',
    icon: 'chat',
    hue: 'var(--accent)',
    families: ['Channel'],
    statusText: 'Connected',
    tone: 'good',
    attention: false,
    fixLabel: 'Reconnect',
    enabled: true,
    sub: 'Bot @onw_lucius_bot',
    ...overrides,
  }
}

function renderRow(e: ConnectorEntry) {
  const onOpen = vi.fn()
  const onReconnect = vi.fn()
  const { container } = render(<ConnectorRow entry={e} onOpen={onOpen} onReconnect={onReconnect} />)
  return { container, onOpen, onReconnect }
}

describe('an error says why, in plain view', () => {
  const failing = {
    statusText: 'Error',
    tone: 'bad',
    attention: true,
    lastError: 'The host could not be resolved.',
  } as const

  it('shows the recorded error on the row without a hover', () => {
    const { container } = renderRow(entry(failing))
    const reason = container.querySelector('.cn-rowerror')
    expect(reason?.textContent).toBe(failing.lastError)
    // clipped to one line by CSS, so the whole text stays on hover
    expect(reason?.getAttribute('title')).toBe(failing.lastError)
  })

  it("keeps a server's error text out of what Lucius scans from the screen", () => {
    const { container } = renderRow(entry(failing))
    expect(container.querySelector('.cn-rowerror')?.hasAttribute('data-ctx-ignore')).toBe(true)
  })

  it('adds no line when no reason was recorded', () => {
    const { container } = renderRow(entry({ ...failing, lastError: undefined }))
    expect(container.querySelector('.cn-rowerror')).toBeNull()
  })
})

describe('status is plain coloured text, one vocabulary', () => {
  it('reads "Connected" with the healthy treatment — never "Active"', () => {
    const { container } = renderRow(entry())
    const good = container.querySelector('.cn-statusgood')
    expect(good?.textContent?.trim()).toBe('Connected')
    expect(screen.queryByText('Active')).toBeNull()
  })

  it('uses the error treatment when the connector is erroring', () => {
    const { container } = renderRow(entry({ statusText: 'Error', tone: 'bad', attention: true }))
    expect(container.querySelector('.cn-statusbad')?.textContent?.trim()).toBe('Error')
    expect(container.querySelector('.cn-statusgood')).toBeNull()
  })

  it('uses amber, not the error treatment, for a sign-in to renew', () => {
    const { container } = renderRow(
      entry({
        statusText: 'Auth error',
        tone: 'warn',
        attention: true,
        lastError: 'Sign-in expired',
      }),
    )
    expect(container.querySelector('.cn-statuswarn')?.textContent?.trim()).toBe('Auth error')
    // CSS hides this word in a narrow list; the name needs the room, and the reason says why.
    expect(container.querySelector('.cn-statuswarn .cn-statusbad-label')).toBeTruthy()
    expect(container.querySelector('.cn-statusbad')).toBeNull()
    expect(container.querySelector('.cn-rowerror-warn')?.textContent).toBe('Sign-in expired')
  })

  it('falls back to a neutral badge for a deliberately disconnected connector', () => {
    const { container } = renderRow(entry({ statusText: 'Disconnected', tone: '', enabled: false }))
    expect(container.querySelector('.rd-badge')?.textContent).toBe('Disconnected')
    expect(container.querySelector('.cn-statusgood')).toBeNull()
    expect(container.querySelector('.cn-statusbad')).toBeNull()
  })

  it('never wraps status in a chip when healthy', () => {
    const { container } = renderRow(entry())
    expect(container.querySelector('.rd-badge')).toBeNull()
  })
})

describe('the one row action', () => {
  it('offers Reconnect only on an erroring connector', () => {
    renderRow(entry({ tone: 'bad', statusText: 'Error', attention: true }))
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeTruthy()
  })

  it('says Authenticate when the fix is a sign-in', () => {
    renderRow(
      entry({ tone: 'warn', statusText: 'Auth error', attention: true, fixLabel: 'Authenticate' }),
    )
    expect(screen.getByRole('button', { name: 'Authenticate' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull()
  })

  it('offers no action on a healthy connector', () => {
    renderRow(entry())
    expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull()
  })

  it('carries no toggle, no Configure and no Test button', () => {
    renderRow(entry())
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByRole('button', { name: /configure/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /test/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /disconnect/i })).toBeNull()
  })

  it('reconnects without also opening the detail page', () => {
    const { onOpen, onReconnect } = renderRow(
      entry({ tone: 'bad', statusText: 'Error', attention: true }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(onReconnect).toHaveBeenCalledTimes(1)
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('keeps status and action in ONE flush-right group', () => {
    const { container } = renderRow(entry({ tone: 'bad', statusText: 'Error', attention: true }))
    const meta = container.querySelector('.cn-meta')
    expect(meta?.querySelector('.cn-statusbad')).toBeTruthy()
    expect(meta?.textContent).toContain('Reconnect')
  })
})

describe('the row IS the affordance', () => {
  it('opens the detail page on click', () => {
    const { container, onOpen } = renderRow(entry())
    fireEvent.click(container.querySelector('.cn-row')!)
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('opens on Enter and Space for a keyboard user', () => {
    const { container, onOpen } = renderRow(entry())
    const row = container.querySelector('.cn-row')!
    fireEvent.keyDown(row, { key: 'Enter' })
    fireEvent.keyDown(row, { key: ' ' })
    expect(onOpen).toHaveBeenCalledTimes(2)
  })

  it('ignores other keys', () => {
    const { container, onOpen } = renderRow(entry())
    fireEvent.keyDown(container.querySelector('.cn-row')!, { key: 'a' })
    expect(onOpen).not.toHaveBeenCalled()
  })
})

describe('the row stays a Lucius entity (@-mention + drag-to-Lucius)', () => {
  // The outgoing tab wrapped every connector card in `EntityRow`. Rebuilding the row
  // without it silently removed two shipped capabilities, which no visual check catches.
  it('is the `.cn-row` itself, not an extra layer inside `.cn-list`', () => {
    const { container } = renderRow(entry())
    const row = container.querySelector('.cn-row')!
    // an extra wrapper would make every row the only child of its own parent, which
    // turns `.cn-row:last-child { border-bottom: none }` into "no separators at all"
    expect(row.parentElement).toBe(container)
  })

  it('stamps the resolvable entity id', () => {
    const { container } = renderRow(entry())
    expect(container.querySelector('.cn-row')!.getAttribute('data-entity-id')).toBe(
      'connector:telegram',
    )
  })

  it('is draggable into the Lucius composer', () => {
    const { container } = renderRow(entry())
    expect(container.querySelector('.cn-row')!.getAttribute('draggable')).toBe('true')
  })

  it('publishes the connector descriptor fields for the mention popover', () => {
    const { container } = renderRow(entry({ enabled: true }))
    const fields = container.querySelector('.cn-row')!.getAttribute('data-ctx-fields') ?? ''
    expect(container.querySelector('.cn-row')!.getAttribute('data-ctx-type')).toBe('connector')
    for (const expected of ['Telegram', 'telegram', 'on', 'Connected']) {
      expect(fields).toContain(expected)
    }
  })

  it('keeps the row click working through the wrapper', () => {
    const { container, onOpen } = renderRow(entry())
    fireEvent.click(container.querySelector('.cn-row')!)
    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})

describe('name and tag', () => {
  it('shows the name and an optional inline tag, and no sub-line', () => {
    const { container } = renderRow(entry({ tag: 'Default' }))
    expect(container.querySelector('.cn-name > b')?.textContent).toBe('Telegram')
    expect(container.querySelector('.cn-tag')?.textContent).toBe('Default')
    // the sub-line belongs to the detail page — the row is exactly one line
    expect(container.textContent).not.toContain('Bot @onw_lucius_bot')
  })

  it('omits the tag when there is none', () => {
    const { container } = renderRow(entry())
    expect(container.querySelector('.cn-tag')).toBeNull()
  })
})
