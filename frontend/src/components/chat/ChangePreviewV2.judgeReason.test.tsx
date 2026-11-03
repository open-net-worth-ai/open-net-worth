// @vitest-environment jsdom
// Why the Auto judge stopped to ask (permissions_v2): the card says so, on every kind of card —
// an Interactive Brokers order's card said nothing, as if Auto had never judged it (owner,
// 2026-11-04). The reason is model-written, so it is plain text, never markup.
import { afterEach, describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

afterEach(cleanup)

const order = (extra: Partial<ProposedChange>): ProposedChange =>
  ({
    type: 'external_mcp_call',
    id: 'toolu_order',
    server_id: 'broker',
    tool: 'create_order',
    args: { symbol: 'ACME', side: 'BUY', quantity: 20 },
    display: { name: 'create_order', server: 'ACME Broker', args: { symbol: 'ACME' } },
    reason: "Run the 'create_order' tool on the external 'ACME Broker' MCP server.",
    grant_tool: 'mcp__broker__create_order',
    tool_use_id: 'toolu_order',
    ...extra,
  }) as ProposedChange

describe('the card says why Auto asked', () => {
  it('shows the judge reason on a connector card, as plain text', () => {
    render(
      createElement(ChangePreviewV2, {
        change: order({ judge_reason: 'A live order is not cheap to undo <b>once placed</b>.' }),
        onRespond: () => {},
      }),
    )
    const note = screen.getByText(/Auto asked because:/)
    expect(note.textContent).toContain('A live order is not cheap to undo <b>once placed</b>.')
    expect(note.querySelector('b')).toBeNull()
  })

  it('says nothing about Auto when it did not judge the call', () => {
    render(createElement(ChangePreviewV2, { change: order({}), onRespond: () => {} }))
    expect(screen.queryByText(/Auto asked because/)).toBeNull()
  })
})
