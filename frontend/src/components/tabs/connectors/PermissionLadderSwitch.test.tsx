// @vitest-environment jsdom
//
// The connector permission ladder's switch point for permissions_v2: the same ladder, its
// levels named Needs approval / Blocked with the flag off and Ask / Block with it on. What the
// buttons persist is the stored value either way.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../hooks/useFlag'
import { buildToolGroups } from './model'
import { PermissionLadderSwitch } from './PermissionLadderSwitch'

afterEach(cleanup)

describe.each([false, true])('the connector permission ladder (permissions_v2=%s)', (on) => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ permissions_v2: on })))
  afterEach(() => restore())

  it(`names the levels ${on ? 'Ask / Block' : 'Needs approval / Blocked'}`, () => {
    render(
      <PermissionLadderSwitch
        groups={buildToolGroups(['get_positions'])}
        perms={{}}
        onSetTool={vi.fn()}
        onSetGroup={vi.fn()}
      />,
    )
    const names = (labels: string[]) =>
      labels.map((l) => screen.queryByRole('button', { name: `get_positions: ${l}` }) !== null)
    expect(names(['Needs approval', 'Blocked'])).toEqual([!on, !on])
    expect(names(['Ask', 'Block'])).toEqual([on, on])
  })

  it('persists the stored level, whatever it is called', () => {
    const onSetTool = vi.fn()
    render(
      <PermissionLadderSwitch
        groups={buildToolGroups(['get_positions'])}
        perms={{}}
        onSetTool={onSetTool}
        onSetGroup={vi.fn()}
      />,
    )
    fireEvent.click(
      screen.getByRole('button', { name: `get_positions: ${on ? 'Block' : 'Blocked'}` }),
    )
    expect(onSetTool).toHaveBeenCalledWith('get_positions', 'blocked')
  })
})
