// @vitest-environment jsdom
//
// REGRESSION (mobile incident, 2026-09-06): a phone user tapped "Allow once" on a staged
// `update_accounts` card, the server could not redeem it (a later message had replaced the
// approval), and it streamed back an `error` carrying the SAME `tool_use_id`. The reducer's
// `error` branch (chatReducer.ts) un-paints the optimistic tick: `status: undefined,
// refused: true`. Before this fix, clearing `status` alone re-armed the card — a fresh blue
// "Allow once ▾ / Deny" split button sat directly above the
// red error, and the user's second tap produced the identical refusal. A refused approval can
// never be redeemed (the backend consumes/replaces the pending tool_use once), so re-arming it
// is a lie the UI must not tell.
//
// `refused` is the flag the card must treat as TERMINAL regardless of the button layout a
// given change type would otherwise use — the permission split-button (grant_tool, which is
// what run_code's card is too) and the external_mcp_call card (same split-button layout, three
// effective choices). Each must render a neutral
// status line instead, reusing the SAME resolved-state row the card already shows for
// "✓ Approved" / "✗ Dismissed" (`.change-confirmed`) — no new className, per CLAUDE.md's CSS
// rule.
import { describe, it, expect } from 'vitest'
import { createElement } from 'react'
import { render, screen, cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { ChangePreview } from './ChangePreview'
import type { ProposedChange } from '../../types/chat'

afterEach(cleanup)

const REFUSED_TEXT = "Not run — can't be reused."

const noop = () => {}

const permissionCard: ProposedChange = {
  type: 'txn_create',
  grant_tool: 'add_transactions',
  id: 'toolu-1',
  session_id: 'sess-1',
  description: 'Coffee',
  from: { account_id: 'acc_chk', account_name: 'Checking', amount: 12.4, currency: 'SGD' },
}

const externalMcpCard: ProposedChange = {
  type: 'external_mcp_call',
  id: 'toolu-2',
  server_id: 'drive',
  tool: 'transfer',
  args: { to: 'savings', amount: 100 },
  display: { name: 'transfer', server: 'Drive', args: { to: 'savings', amount: 100 } },
  reason: "Run the 'transfer' tool on the external 'Drive' MCP server.",
}

const codeRunCard: ProposedChange = {
  type: 'tool_permission',
  grant_tool: 'run_code',
  id: 'toolu-3',
  display: { tool: 'run_code', arguments: { source: 'print(1)', purpose: 'a script' } },
}

function mount(change: ProposedChange) {
  return render(
    createElement(ChangePreview, {
      change,
      onRespond: noop,
      onResolvePendingToolUse: noop,
    }),
  )
}

const BANNED_BUTTON_NAMES = [
  'Allow once',
  'Deny',
  'Reject',
  'Always allow',
  'Confirm',
  'Dismiss',
  'Approve',
]

function assertNoActionableButtons() {
  for (const name of BANNED_BUTTON_NAMES) {
    expect(screen.queryByRole('button', { name })).toBeNull()
  }
  expect(screen.queryByRole('button', { name: 'Standing permission' })).toBeNull()
  expect(screen.queryByRole('menu')).toBeNull()
}

describe('ChangePreview — a refused approval is terminal, not re-armed', () => {
  it('permission split-button card (grant_tool): refused renders the status row, no buttons', () => {
    const { container } = mount({ ...permissionCard, status: undefined, refused: true })
    assertNoActionableButtons()
    expect(screen.getByText(REFUSED_TEXT)).toBeTruthy()
    // The preview body (what was proposed) must still be visible.
    expect(container.textContent).toContain('Coffee')
  })

  it('external_mcp_call card: refused renders the status row, no buttons, no dropdown', () => {
    const { container } = mount({ ...externalMcpCard, status: undefined, refused: true })
    assertNoActionableButtons()
    expect(screen.getByText(REFUSED_TEXT)).toBeTruthy()
    // The args/preview body stays visible.
    expect(container.textContent).toContain('savings')
  })

  it('run_code card: refused renders the status row and keeps the script visible', () => {
    const { container } = mount({ ...codeRunCard, status: undefined, refused: true })
    assertNoActionableButtons()
    expect(screen.getByText(REFUSED_TEXT)).toBeTruthy()
    expect(container.textContent).toContain('print(1)')
  })

  it('reload semantics: refused: true with no status renders the same terminal state', () => {
    // `refused` persists with the message (chatReducer persists via saveMessages) — a fresh
    // mount with no prior interaction (no localResponse) must render identically to the
    // just-tapped case, since a reload never calls onRespond first.
    mount({ ...permissionCard, refused: true })
    assertNoActionableButtons()
    expect(screen.getByText(REFUSED_TEXT)).toBeTruthy()
  })

  it('guard: a non-refused permission card still shows its live buttons', () => {
    mount(permissionCard)
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeTruthy()
    expect(screen.queryByText(REFUSED_TEXT)).toBeNull()
  })
})
