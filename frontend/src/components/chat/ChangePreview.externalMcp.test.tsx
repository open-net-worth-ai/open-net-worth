// @vitest-environment jsdom
//
// external_mcp_call confirm card — reuses this app's OWN existing "Tool permission" split-
// button layout (owner, 2026-08-22: "we already have this style and layout for the
// permission, why don't we follow this? the buttons should align left like this") instead of
// a bespoke centered card. Same visual shape as the native grant_tool card
// ([Allow once ▾][Deny], Always allow behind the caret) — but external_mcp_call deliberately
// never carries grant_tool (backend mcp/client_manager.py's _build_proposed_change docstring),
// so "Always allow" here writes through the connector-scoped tool-permissions route instead
// of the generic registry grant route.
//
// Mirrors ChangePreview.grant.test.tsx's own style: exercise the buttons and assert what
// happens on the click, not markup. Two properties under test:
//   1. Deny/Allow once resolve the SAME server-owned tool_use_id via onResolvePendingToolUse
//      exactly as the old Dismiss/Confirm did — never construct a model-facing chat message.
//   2. Always allow writes the CONNECTOR-SCOPED tool-permissions route
//      (`PUT /api/connectors/{mcp:<server>}/permissions/{tool}`) BEFORE firing the call — the
//      external tool carries no `grant_tool`, so it must NOT hit the generic
//      `/api/tool-permissions/{tool}` route the grant_tool card uses.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createElement } from 'react'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { ChangePreview } from './ChangePreview'
import type { ProposedChange } from '../../types/chat'

const TOOL_USE_ID = 'toolu-ext-mcp-1'

const card: ProposedChange = {
  type: 'external_mcp_call',
  id: TOOL_USE_ID,
  server_id: 'drive',
  tool: 'transfer',
  args: { to: 'savings', amount: 100 },
  display: { name: 'transfer', server: 'Drive', args: { to: 'savings', amount: 100 } },
  reason: "Run the 'transfer' tool on the external 'Drive' MCP server.",
}

let resolved: Array<[string, 'approve' | 'deny']>
let fetchMock: ReturnType<typeof vi.fn>

function mount(change: ProposedChange = card) {
  const utils = render(
    createElement(ChangePreview, {
      change,
      onResolvePendingToolUse: (toolUseId: string, decision: 'approve' | 'deny') =>
        resolved.push([toolUseId, decision]),
      onRespond: () => {},
    }),
  )
  return utils
}

/** Opens the "Standing permission" caret menu and clicks its one item — the only way to
 *  reach Always allow in the split-button layout. */
function clickAlwaysAllow() {
  fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
  fireEvent.click(screen.getByRole('menuitem', { name: /Always allow/ }))
}

beforeEach(() => {
  resolved = []
  fetchMock = vi.fn(async () => new Response(null, { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** The parsed body of the single PUT /api/connectors/{id}/permissions/{tool} call, or null
 *  if none was made. */
function permissionCall(): { url: string; body: Record<string, unknown> } | null {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/permissions/'))
  if (!call) return null
  return {
    url: String(call[0]),
    body: JSON.parse((call[1] as RequestInit).body as string),
  }
}

describe('external_mcp_call card — the Tool permission split-button layout', () => {
  it('renders the SAME layout as the native Tool permission card: [Allow once ▾][Deny], never the old bare Confirm/Dismiss', () => {
    mount()
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Standing permission' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Always allow' })).toBeNull() // behind the caret, not a top-level button
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()
  })

  it('is titled by the tool name, with the server beside it', () => {
    const { container } = mount()
    expect(container.querySelector('.change-preview-tool')?.textContent).toBe('transfer')
    expect(container.querySelector('.change-preview-label')?.textContent).toBe('Drive')
  })

  it('shows the args as fact rows so the user sees exactly what will run', () => {
    const { container } = mount()
    const rows = [...container.querySelectorAll('.ac-fact')].map((r) => r.textContent)
    expect(rows).toEqual(['Tosavings', 'Amount100'])
  })

  it('names the tool in the title, not in a note under the buttons', () => {
    // The "Approving runs `x`." note is gone from every card (owner, 2026-08-25) — it
    // restated what the card already showed, and on a native card it printed our own
    // snake_case tool name, the one thing no chat surface is supposed to show. An external
    // server's tool name is different: it IS the tool's identity, so it is the card's title
    // (owner, 2026-10-31 — the old "Lucius wants to use … from …" lead was too many words).
    const { container } = mount()
    expect(container.querySelector('.change-grant-note')).toBeNull()
    expect(container.querySelector('.change-preview-header')?.textContent).toContain('transfer')
  })
})

describe('external_mcp_call card — Deny/Allow once match the old Dismiss/Confirm', () => {
  it('Allow once resolves the SAME tool_use_id server-side — never sends a chat message', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(resolved).toEqual([[TOOL_USE_ID, 'approve']])
    expect(permissionCall()).toBeNull() // no standing permission written
  })

  it('Deny resolves the SAME tool_use_id server-side — never sends a chat message', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(resolved).toEqual([[TOOL_USE_ID, 'deny']])
    expect(permissionCall()).toBeNull()
  })

  it('resolves the card as Approved / Denied afterward', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    await waitFor(() => expect(screen.getByText('✗ Denied')).toBeTruthy())
  })
})

describe('external_mcp_call card — Always allow (behind the caret)', () => {
  it('sets the connector-scoped permission BEFORE firing the call', async () => {
    const order: string[] = []
    fetchMock.mockImplementation(async () => {
      order.push('permission')
      return new Response(null, { status: 200 })
    })
    render(
      createElement(ChangePreview, {
        change: card,
        onResolvePendingToolUse: () => order.push('approve'),
        onRespond: () => {},
      }),
    )
    clickAlwaysAllow()
    await waitFor(() => expect(order).toEqual(['permission', 'approve']))
  })

  it('writes the CONNECTOR-SCOPED route, keyed on mcp:<server_id> + the tool name — never the generic registry route', async () => {
    mount()
    clickAlwaysAllow()
    await waitFor(() => expect(resolved.length).toBe(1))
    const call = permissionCall()
    expect(call).not.toBeNull()
    expect(call!.url).toBe('/api/connectors/mcp%3Adrive/permissions/transfer')
    expect(call!.body).toEqual({ level: 'always_allow' })
  })

  it('also fires the call after recording the grant — no second click needed', async () => {
    mount()
    clickAlwaysAllow()
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(resolved).toEqual([[TOOL_USE_ID, 'approve']])
  })

  it('a failed permission write still approves the call but reports the failure, never silently', async () => {
    fetchMock.mockImplementation(async () => new Response(null, { status: 500 }))
    const { container } = mount()
    clickAlwaysAllow()
    await waitFor(() => expect(resolved.length).toBe(1)) // approved anyway
    expect(resolved).toEqual([[TOOL_USE_ID, 'approve']])
    const err = container.querySelector('.change-grant-error')?.textContent ?? ''
    expect(err).toMatch(/couldn't save/i)
    expect(err).toMatch(/asked again/i)
  })

  it('disables the row while the permission write is in flight', async () => {
    const releaseBox: { current: (() => void) | null } = { current: null }
    fetchMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseBox.current = () => resolve(new Response(null, { status: 200 }))
        }),
    )
    mount()
    clickAlwaysAllow()
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Deny' }) as HTMLButtonElement).disabled).toBe(
        true,
      ),
    )
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeTruthy()
    releaseBox.current?.()
    await waitFor(() => expect(resolved.length).toBe(1))
  })
})
