// @vitest-environment jsdom
//
// The endpoint list of a REST API's form. Each endpoint is a tool, so the list is laid out as
// an MCP server's tools are: read-only, then write & delete, a permission on each row and a
// "set them all" on the group (owner, 2026-11-11: "why are we not splitting these into read and
// write already here, with permission settings right here?").
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../hooks/useFlag'
import { RestEndpointsEditor } from './RestEndpointsEditor'
import { editedEndpoint, shownLevel } from './restApi'
import type { RestEndpoint } from './restApi'

afterEach(cleanup)

const ENDPOINTS: RestEndpoint[] = [
  { method: 'POST', path: '/transfers', description: 'Move money', params: [] },
  { method: 'GET', path: '/accounts', description: 'Accounts', params: [], level: 'always_allow' },
  { method: 'DELETE', path: '/payees/{id}', description: 'Delete a payee', params: [] },
  { method: 'GET', path: '/txns', description: 'Transactions', params: [] },
]

describe('the endpoint list (old permissions)', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ permissions_v2: false })))
  afterEach(() => restore())

  function open(onChange = vi.fn()) {
    render(<RestEndpointsEditor endpoints={ENDPOINTS} onChange={onChange} />)
    return onChange
  }
  const paths = (group: string) =>
    within(screen.getByRole('list', { name: group }))
      .getAllByRole('listitem')
      .map((row) => row.querySelector('.rest-path')?.textContent)

  it('splits the endpoints into read-only and write & delete, in their own order', () => {
    open()
    expect(paths('Read-only tools')).toEqual(['/accounts', '/txns'])
    expect(paths('Write & delete tools')).toEqual(['/transfers', '/payees/{id}'])
  })

  it('shows each endpoint at the level its tool has, asking by default', () => {
    open()
    const pressed = (name: string) =>
      screen.getByRole('button', { name }).getAttribute('aria-pressed')
    expect(pressed('GET /accounts: Always allow')).toBe('true')
    expect(pressed('GET /txns: Needs approval')).toBe('true')
    expect(pressed('POST /transfers: Needs approval')).toBe('true')
  })

  it('picks a level for ONE endpoint, on the list the rows came from', () => {
    const onChange = open()
    fireEvent.click(screen.getByRole('button', { name: 'DELETE /payees/{id}: Blocked' }))
    const next = onChange.mock.calls[0]![0] as RestEndpoint[]
    expect(next.map((e) => e.permission)).toEqual([undefined, undefined, 'blocked', undefined])
  })

  it('stamps a level on every endpoint of a group, and only that group', () => {
    const onChange = open()
    const reads = screen.getByRole('list', { name: 'Read-only tools' }).parentElement!
    // The group shows Custom: one read is allowed, one asks.
    fireEvent.click(within(reads).getByRole('button', { name: /Custom/ }))
    fireEvent.click(within(reads).getByRole('button', { name: 'Always allow' }))
    const next = onChange.mock.calls[0]![0] as RestEndpoint[]
    expect(next.map((e) => e.permission)).toEqual([
      undefined,
      'always_allow',
      undefined,
      'always_allow',
    ])
  })
})

describe('removing an endpoint', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ permissions_v2: false })))
  afterEach(() => restore())

  it('asks first, naming the endpoint, and removes only on a yes', () => {
    const onChange = vi.fn()
    render(<RestEndpointsEditor endpoints={ENDPOINTS} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Remove POST /transfers' }))
    expect(screen.getByText('Remove this endpoint?')).toBeTruthy()
    expect(screen.getByText('POST /transfers')).toBeTruthy()
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText('Remove this endpoint?')).toBeNull()
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Remove POST /transfers' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    const next = onChange.mock.calls[0]![0] as RestEndpoint[]
    expect(next.map((e) => e.path)).toEqual(['/accounts', '/payees/{id}', '/txns'])
  })

  it('does not ask about a row that has no path yet', () => {
    const onChange = vi.fn()
    const blank: RestEndpoint = { method: 'GET', path: '', description: '', params: [] }
    render(<RestEndpointsEditor endpoints={[blank]} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /^Remove GET/ }))
    expect(screen.queryByText('Remove this endpoint?')).toBeNull()
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('folds a group away and back, like the permission list’s', () => {
    render(<RestEndpointsEditor endpoints={ENDPOINTS} onChange={vi.fn()} />)
    const head = screen.getByRole('button', { name: /Read-only tools/ })
    expect(head.getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(head)
    expect(head.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('list', { name: 'Read-only tools' })).toBeNull()
  })
})

describe('a row keeps Edit and Remove out of sight', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ permissions_v2: false })))
  afterEach(() => {
    restore()
    vi.unstubAllGlobals()
  })

  it('opens its editor when the endpoint itself is pressed, and the editor can remove it', () => {
    const onChange = vi.fn()
    render(<RestEndpointsEditor endpoints={ENDPOINTS} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open GET /accounts' }))
    expect(screen.getByLabelText('Path')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Remove endpoint' }))
    // The same question as the row's own Remove.
    expect(screen.getByText('Remove this endpoint?')).toBeTruthy()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('with a mouse, draws them in the row for hover to reveal', () => {
    const { container } = render(<RestEndpointsEditor endpoints={ENDPOINTS} onChange={vi.fn()} />)
    expect(container.querySelectorAll('.rest-ep-acts')).toHaveLength(ENDPOINTS.length)
    expect(container.querySelector('.ui-swipe')).toBeNull()
  })

  it('on a touch screen, swipes the row onto them instead', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(hover: none) and (pointer: coarse)',
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }))
    const onChange = vi.fn()
    const { container } = render(<RestEndpointsEditor endpoints={ENDPOINTS} onChange={onChange} />)
    // Five buttons on every row was the complaint: here a row is the endpoint and its switch.
    expect(container.querySelector('.rest-ep-acts')).toBeNull()
    expect(container.querySelectorAll('.ui-swipe')).toHaveLength(ENDPOINTS.length)

    fireEvent.click(screen.getByRole('button', { name: 'Remove GET /txns', hidden: true }))
    expect(screen.getByText('Remove this endpoint?')).toBeTruthy()
  })
})

describe('an endpoint’s level', () => {
  const read: RestEndpoint = { method: 'GET', path: '/txns', description: '', params: [] }
  const write: RestEndpoint = { ...read, method: 'POST' }

  it('defaults to asking; under the new permissions a read runs unasked', () => {
    expect(shownLevel(read, false)).toBe('needs_approval')
    expect(shownLevel(write, true)).toBe('needs_approval')
    expect(shownLevel(read, true)).toBe('always_allow')
  })

  it('is the owner’s unsaved pick before what the server reported', () => {
    expect(shownLevel({ ...read, level: 'always_allow', permission: 'blocked' }, false)).toBe(
      'blocked',
    )
  })

  it('goes back to asking when an allowed endpoint becomes a different request', () => {
    const allowed: RestEndpoint = { ...read, level: 'always_allow' }
    // It was allowed as a GET: the DELETE it is edited into must be picked again, in view.
    expect(editedEndpoint(allowed, { ...allowed, method: 'DELETE' }, false).permission).toBe(
      'needs_approval',
    )
    expect(editedEndpoint(allowed, { ...allowed, path: '/other' }, false).permission).toBe(
      'needs_approval',
    )
    // A description is not the request; a blocked endpoint is never loosened.
    expect(editedEndpoint(allowed, { ...allowed, description: 'x' }, false).permission).toBe(
      undefined,
    )
    const blocked: RestEndpoint = { ...read, level: 'blocked' }
    expect(editedEndpoint(blocked, { ...blocked, method: 'POST' }, false).permission).toBe(
      undefined,
    )
  })
})
