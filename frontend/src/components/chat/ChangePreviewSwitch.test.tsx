// @vitest-environment jsdom
//
// The approval card's switch point for permissions_v2: the old card with the flag off, the
// three-answer card with it on. The v2 card puts Deny first and groups the allow scopes in
// `.change-grant-allow`; the old card has neither.
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { overrideFlagsForTests } from '../../hooks/useFlag'
import type { ProposedChange } from '../../types/chat'
import { ChangePreviewSwitch } from './ChangePreviewSwitch'

const card: ProposedChange = {
  type: 'tool_permission',
  grant_tool: 'create_asset_category',
  id: 'toolu_1',
  session_id: 'sess-1',
  display: { tool: 'create_asset_category', arguments: { label: 'Vinyl' } },
}

afterEach(cleanup)

describe.each([false, true])('the approval card (permissions_v2=%s)', (on) => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ permissions_v2: on })))
  afterEach(() => restore())

  it(`renders the ${on ? 'three-answer' : 'old'} card`, () => {
    const { container } = render(<ChangePreviewSwitch change={card} onRespond={() => {}} />)
    expect(screen.getByRole('button', { name: 'Deny' })).toBeTruthy()
    expect(container.querySelector('.change-grant-allow') !== null).toBe(on)
    expect(container.querySelector('.change-grant-btn--deny-v2') !== null).toBe(on)
  })

  it('a resolved card reads the same label either way for a plain approval', () => {
    render(<ChangePreviewSwitch change={{ ...card, status: 'confirmed' }} />)
    expect(screen.getByText('✓ Approved')).toBeTruthy()
  })

  it(`${on ? 'names' : 'does not name'} an Always allow on a reloaded card`, () => {
    render(
      <ChangePreviewSwitch
        change={{ ...card, status: 'confirmed', resolution: 'always_allowed' }}
      />,
    )
    expect(screen.queryByText('✓ Always allowed') !== null).toBe(on)
    expect(screen.queryByText('✓ Approved') !== null).toBe(!on)
  })
})
