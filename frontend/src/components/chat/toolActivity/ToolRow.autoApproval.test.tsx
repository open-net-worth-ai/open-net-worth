// @vitest-environment jsdom
//
// A call the Auto judge let through wears a small "i" whose bubble gives the judge's reason
// (permissions_v2). The reason is model-written from the call's arguments, so it must render as
// plain text — never markup, never a link.

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToolRow } from './ToolRow'
import type { Cluster, ToolItem } from './cluster'
import type { AutoApproval } from '../../../types/chat'

vi.mock('../../../utils/chatStore', () => ({ fetchToolDetail: vi.fn() }))

afterEach(cleanup)

function row(autoApproval?: AutoApproval): Cluster {
  const item: ToolItem = {
    type: 'tool',
    name: 'add_transactions',
    id: 'tu_1',
    status: 'done',
    input: { transactions: [] },
    result: '{"ok":true}',
    ...(autoApproval ? { autoApproval } : {}),
  }
  return {
    key: 'tu_1',
    items: [item],
    label: 'Add transactions',
    verb: 'write',
    noun: 'transactions',
    ownerId: 'core',
    ownerName: '',
    detail: '',
    failed: false,
    errorText: '',
    busy: false,
  }
}

const hint = () => screen.queryByRole('button', { name: 'Why this ran without asking' })

describe('the auto-approval hint', () => {
  it('is absent on a call the judge did not decide', () => {
    render(<ToolRow cluster={row()} look={{}} />)
    expect(hint()).toBeNull()
  })

  it('shows the judge and its reason, and sits beside the row button rather than inside it', () => {
    const { container } = render(
      <ToolRow
        cluster={row({ reason: 'Adds the coffee you asked for.', model: 'sonnet' })}
        look={{}}
      />,
    )
    const button = hint()
    expect(button).not.toBeNull()
    expect(container.querySelector('.msg-toolact__head')?.contains(button)).toBe(false)

    fireEvent.click(button!)

    expect(screen.getByRole('tooltip').textContent).toContain('Auto-approved by the judge (sonnet)')
    expect(screen.getByRole('tooltip').textContent).toContain('Adds the coffee you asked for.')
  })

  it('does not open the row when the hint is tapped', () => {
    const { container } = render(<ToolRow cluster={row({ reason: 'ok' })} look={{}} />)

    fireEvent.click(hint()!)

    expect(container.querySelector('.msg-toolact__row--open')).toBeNull()
  })

  it('renders a reason carrying markup as plain text', () => {
    render(
      <ToolRow
        cluster={row({ reason: '<img src=x onerror=alert(1)> [click](https://example.com)' })}
        look={{}}
      />,
    )

    fireEvent.click(hint()!)

    const tip = screen.getByRole('tooltip')
    expect(tip.textContent).toContain('<img src=x onerror=alert(1)> [click](https://example.com)')
    expect(tip.querySelector('img')).toBeNull()
    expect(tip.querySelector('a')).toBeNull()
  })
})
