// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../../state/queryCache'
import { PermissionsSectionV2 } from './PermissionsSectionV2'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function row(over: Record<string, unknown>) {
  return {
    connector_id: 'core',
    level: 'needs_approval',
    default_level: 'needs_approval',
    description: '',
    updated_at: '',
    tool_exists: true,
    ...over,
  }
}

// Every tool has a row. `update_transactions` is at its default (Ask); `get_net_worth` has been
// moved off its default (a read that runs, set to Ask); `add_transactions` was set to Always allow.
const PERMISSIONS = [
  row({ tool: 'add_transactions', level: 'always_allow', description: 'Record new transactions.' }),
  row({
    tool: 'get_net_worth',
    level: 'needs_approval',
    default_level: 'always_allow',
    description: 'Read the current net worth.',
  }),
  row({ tool: 'update_transactions', description: 'Edit existing transactions.' }),
  row({
    tool: 'plaid_fetch_transactions',
    connector_id: 'plaid',
    level: 'always_allow',
    default_level: 'always_allow',
  }),
  row({ tool: 'browser_click', connector_id: 'mcp:playwright' }),
  row({ tool: 'old_removed_tool', level: 'blocked', default_level: '', tool_exists: false }),
]

describe('PermissionsSectionV2', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ permissions: PERMISSIONS, total: PERMISSIONS.length }),
    } as Response)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('lists only built-in tools, with no owner header — connector and MCP tools live in Connectors', async () => {
    const { container } = render(<PermissionsSectionV2 toast={vi.fn()} />)

    await screen.findByText('Add transactions')
    expect(screen.queryByText('Built-in')).toBeNull()
    expect(screen.queryByText('Plaid fetch transactions')).toBeNull()
    expect(screen.queryByText('Browser click')).toBeNull()
    const builtIn = PERMISSIONS.filter((p) => p.connector_id === 'core').length
    await waitFor(() => {
      expect(container.querySelector('.rd-panel-title .rd-badge')?.textContent).toBe(
        String(builtIn),
      )
    })
  })

  it('names a tool in words and keeps the raw identifier out of the visible label', async () => {
    render(<PermissionsSectionV2 toast={vi.fn()} />)

    const label = await screen.findByText('Add transactions')
    expect(label.closest('[title]')?.getAttribute('title')).toBe('add_transactions') // tooltip only
    expect(screen.queryByText('add_transactions')).toBeNull()
  })

  it('searches by name and description', async () => {
    render(<PermissionsSectionV2 toast={vi.fn()} />)
    await screen.findByText('Add transactions')
    const search = screen.getByLabelText('Search tools')

    fireEvent.change(search, { target: { value: 'net worth' } })
    expect(screen.getByText('Get net worth')).toBeTruthy()
    expect(screen.queryByText('Add transactions')).toBeNull()

    fireEvent.change(search, { target: { value: 'existing transactions' } })
    expect(screen.getByText('Update transactions')).toBeTruthy()
    expect(screen.queryByText('Get net worth')).toBeNull()

    fireEvent.change(search, { target: { value: 'zzz' } })
    expect(screen.getByText('No tools match')).toBeTruthy()
  })

  it('labels the three values Always allow / Ask / Block', async () => {
    render(<PermissionsSectionV2 toast={vi.fn()} />)

    const select = (await screen.findByLabelText(
      'Permission for Add transactions',
    )) as HTMLSelectElement
    expect(select.value).toBe('always_allow')
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      'Always allow',
      'Ask',
      'Block',
    ])
  })

  it('lets the owner change a tool’s value right here', async () => {
    const toast = vi.fn()
    render(<PermissionsSectionV2 toast={toast} />)

    const select = (await screen.findByLabelText(
      'Permission for Update transactions',
    )) as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'always_allow' } })

    await waitFor(() => {
      const put = mockAuthFetch.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === 'PUT',
      )
      expect(put).toBeTruthy()
      expect(put![0]).toBe('/api/tool-permissions/update_transactions')
      expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({
        level: 'always_allow',
        connector_id: 'core',
      })
    })
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        'Update transactions: Always allow — takes effect on the next call',
      ),
    )
  })

  it('offers Reset only where the value differs from the default, and resets to it', async () => {
    const toast = vi.fn()
    render(<PermissionsSectionV2 toast={toast} />)
    await screen.findByText('Add transactions')

    // At default: no Reset. Off default: Reset.
    expect(
      screen.queryByRole('button', { name: 'Reset Update transactions to the default' }),
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'Reset Plaid fetch transactions to the default' }),
    ).toBeNull()
    const reset = screen.getByRole('button', { name: 'Reset Get net worth to the default' })

    mockAuthFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ reset: 'get_net_worth', level: 'always_allow' }),
    } as Response)
    fireEvent.click(reset)

    await waitFor(() => {
      const del = mockAuthFetch.mock.calls.find(
        (c) => (c[1] as RequestInit | undefined)?.method === 'DELETE',
      )
      expect(del).toBeTruthy()
      expect(del![0]).toBe('/api/tool-permissions/get_net_worth?connector_id=core')
    })
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        'Get net worth: back to Always allow — takes effect on the next call',
      ),
    )
  })

  it('can show only what has been changed from its default', async () => {
    render(<PermissionsSectionV2 toast={vi.fn()} />)
    await screen.findByText('Add transactions')

    fireEvent.click(screen.getByRole('button', { name: /^Changed \(/ }))
    const shown = Array.from(document.querySelectorAll('.rd-permrow-tool-v2')).map(
      (el) => el.textContent,
    )
    expect(shown.some((text) => text?.startsWith('Get net worth'))).toBe(true)
    expect(shown.some((text) => text?.startsWith('Update transactions'))).toBe(false)
  })

  it('renders a retired tool’s decision as inert and lets Reset clear it', async () => {
    render(<PermissionsSectionV2 toast={vi.fn()} />)

    await screen.findByText('Old removed tool')
    expect(screen.getByText('No longer available — renamed or removed')).toBeTruthy()
    expect(screen.queryByLabelText('Permission for Old removed tool')).toBeNull()
    const reset = screen.getByRole('button', { name: 'Reset Old removed tool to the default' })
    expect(within(reset.closest('.rd-permrow-v2') as HTMLElement).getByText('Block')).toBeTruthy()
  })

  it('says so when a change could not be saved', async () => {
    const toast = vi.fn()
    render(<PermissionsSectionV2 toast={toast} />)

    const select = (await screen.findByLabelText(
      'Permission for Update transactions',
    )) as HTMLSelectElement
    mockAuthFetch.mockResolvedValueOnce({ ok: false, status: 500 } as Response)
    fireEvent.change(select, { target: { value: 'blocked' } })

    await waitFor(() => expect(toast).toHaveBeenCalledWith('Could not change that — try again'))
  })
})
