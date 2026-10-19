// @vitest-environment jsdom
//
// The permission ladder's four load-bearing behaviours (spec §4). Each is a contract a
// later "simplification" would plausibly break:
//   - the group stamp OVERWRITES per-tool overrides (lossy on purpose);
//   - `custom` is DERIVED the moment one tool diverges, never stored;
//   - `custom` is SELECTABLE BUT INERT;
//   - grouping is the fixed two-way read/write taxonomy.

import { useState, type JSX } from 'react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react'
import { PermissionLadder } from './PermissionLadder'
import { buildToolGroups, stampGroup, type PermissionLevel, type PermissionMap } from './model'

afterEach(cleanup)

const TOOLS = ['get_positions', 'list_accounts', 'place_order', 'cancel_order']

interface Persisted {
  tool: { tool: string; level: PermissionLevel }[]
  group: { tools: string[]; level: PermissionLevel }[]
}

function Harness({ tools, persisted }: { tools: string[]; persisted: Persisted }): JSX.Element {
  const [perms, setPerms] = useState<PermissionMap>({})
  return (
    <PermissionLadder
      groups={buildToolGroups(tools)}
      perms={perms}
      onSetTool={(tool, level) => {
        persisted.tool.push({ tool, level })
        setPerms((p) => ({ ...p, [tool]: level }))
      }}
      onSetGroup={(names, level) => {
        persisted.group.push({ tools: names, level })
        setPerms((p) => stampGroup(p, names, level))
      }}
    />
  )
}

function setup(tools = TOOLS) {
  const persisted: Persisted = { tool: [], group: [] }
  const { container } = render(<Harness tools={tools} persisted={persisted} />)
  return { container, persisted }
}

/** The group-level control — the only `.rd-btn` inside a group header's `.rd-kebab`. */
function groupControl(container: HTMLElement, index: number): HTMLElement {
  const controls = container.querySelectorAll('.rd-kebab > .rd-btn')
  const el = controls[index]
  if (!el) throw new Error(`no group control at index ${index}`)
  return el as HTMLElement
}

/** The open group menu. Only one is ever open, so it is always the sole `.rd-kebab-pop`. */
function groupMenu(container: HTMLElement): HTMLElement {
  const pops = container.querySelectorAll('.rd-kebab-pop')
  if (pops.length !== 1) throw new Error(`expected exactly one open group menu, got ${pops.length}`)
  return pops[0] as HTMLElement
}

function toolBtn(tool: string, label: string): HTMLElement {
  return screen.getByRole('button', { name: `${tool}: ${label}` })
}

describe('the fixed two-way grouping', () => {
  it('renders a read group and a write group with count badges', () => {
    const { container } = setup()
    expect(screen.getByText('Read-only tools')).toBeTruthy()
    expect(screen.getByText('Write & delete tools')).toBeTruthy()
    const counts = Array.from(container.querySelectorAll('.rd-badge')).map((b) => b.textContent)
    expect(counts).toEqual(['2', '2'])
  })

  it('shows only the read group when a connector has no write tools', () => {
    setup(['get_positions', 'list_accounts'])
    expect(screen.getByText('Read-only tools')).toBeTruthy()
    expect(screen.queryByText('Write & delete tools')).toBeNull()
  })

  it('files each tool under exactly one group', () => {
    const { container } = setup()
    const lists = container.querySelectorAll('.cn-permlist')
    expect(
      Array.from(lists[0]!.querySelectorAll('.cn-permtool')).map((n) => n.textContent),
    ).toEqual(['get_positions', 'list_accounts'])
    expect(
      Array.from(lists[1]!.querySelectorAll('.cn-permtool')).map((n) => n.textContent),
    ).toEqual(['place_order', 'cancel_order'])
  })
})

describe('defaults', () => {
  it('starts every tool at Ask, trade tools included', () => {
    setup()
    for (const t of TOOLS) {
      expect(toolBtn(t, 'Ask').getAttribute('aria-pressed')).toBe('true')
      expect(toolBtn(t, 'Always allow').getAttribute('aria-pressed')).toBe('false')
    }
  })

  it('reads both group controls as Ask', () => {
    const { container } = setup()
    expect(groupControl(container, 0).textContent).toContain('Ask')
    expect(groupControl(container, 1).textContent).toContain('Ask')
  })
})

describe('the derived Custom aggregate', () => {
  it('flips the group control to Custom when ONE tool diverges', () => {
    const { container } = setup()
    expect(groupControl(container, 0).textContent).toContain('Ask')

    fireEvent.click(toolBtn('list_accounts', 'Always allow'))

    expect(groupControl(container, 0).textContent).toContain('Custom')
    // the sibling group is unaffected
    expect(groupControl(container, 1).textContent).toContain('Ask')
  })

  it('never persists `custom` — only the diverging tool is written', () => {
    const { persisted } = setup()
    fireEvent.click(toolBtn('list_accounts', 'Block'))
    expect(persisted.tool).toEqual([{ tool: 'list_accounts', level: 'blocked' }])
    expect(persisted.group).toEqual([])
  })

  it('returns to a real level once the group is stamped uniform again', () => {
    const { container } = setup()
    fireEvent.click(toolBtn('list_accounts', 'Always allow'))
    expect(groupControl(container, 0).textContent).toContain('Custom')

    fireEvent.click(groupControl(container, 0))
    fireEvent.click(within(groupMenu(container)).getByText('Ask'))

    expect(groupControl(container, 0).textContent).toContain('Ask')
  })
})

describe('the group stamp', () => {
  it('overwrites a per-tool override — deliberately lossy', () => {
    const { container } = setup()
    fireEvent.click(toolBtn('get_positions', 'Block'))
    expect(toolBtn('get_positions', 'Block').getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(groupControl(container, 0))
    fireEvent.click(within(groupMenu(container)).getByText('Always allow'))

    expect(toolBtn('get_positions', 'Always allow').getAttribute('aria-pressed')).toBe('true')
    expect(toolBtn('get_positions', 'Block').getAttribute('aria-pressed')).toBe('false')
    expect(toolBtn('list_accounts', 'Always allow').getAttribute('aria-pressed')).toBe('true')
  })

  it('persists the stamp as one group write naming every tool in the group', () => {
    const { container, persisted } = setup()
    fireEvent.click(groupControl(container, 1))
    fireEvent.click(within(groupMenu(container)).getByText('Always allow'))

    expect(persisted.group).toEqual([
      { tools: ['place_order', 'cancel_order'], level: 'always_allow' },
    ])
  })

  it('leaves the other group untouched', () => {
    const { container } = setup()
    fireEvent.click(groupControl(container, 0))
    fireEvent.click(within(groupMenu(container)).getByText('Block'))

    expect(toolBtn('place_order', 'Ask').getAttribute('aria-pressed')).toBe('true')
  })

  it('lets a trade tool be raised to Always allow like any other write tool', () => {
    setup()
    fireEvent.click(toolBtn('place_order', 'Always allow'))
    expect(toolBtn('place_order', 'Always allow').getAttribute('aria-pressed')).toBe('true')
  })
})

describe('Custom is selectable but inert', () => {
  it('offers Custom in the group menu', () => {
    const { container } = setup()
    fireEvent.click(groupControl(container, 0))
    expect(within(groupMenu(container)).getByText('Custom')).toBeTruthy()
  })

  it('changes no value and writes nothing when chosen', () => {
    const { container, persisted } = setup()
    fireEvent.click(toolBtn('list_accounts', 'Always allow'))
    persisted.tool.length = 0

    fireEvent.click(groupControl(container, 0))
    fireEvent.click(within(groupMenu(container)).getByText('Custom'))

    expect(persisted.group).toEqual([])
    expect(persisted.tool).toEqual([])
    expect(toolBtn('list_accounts', 'Always allow').getAttribute('aria-pressed')).toBe('true')
    expect(toolBtn('get_positions', 'Ask').getAttribute('aria-pressed')).toBe('true')
    expect(groupControl(container, 0).textContent).toContain('Custom')
  })

  it('closes the menu', () => {
    const { container } = setup()
    fireEvent.click(groupControl(container, 0))
    expect(container.querySelectorAll('.rd-kebab-pop').length).toBe(1)

    fireEvent.click(within(groupMenu(container)).getByText('Custom'))
    expect(container.querySelectorAll('.rd-kebab-pop').length).toBe(0)
  })

  it('has no Custom option at TOOL level', () => {
    setup()
    expect(screen.queryByRole('button', { name: 'get_positions: Custom' })).toBeNull()
    // three real options, no more
    expect(screen.getAllByRole('button', { name: /^get_positions: / }).length).toBe(3)
  })
})

describe('group disclosure', () => {
  it('collapses and re-expands a group', () => {
    const { container } = setup()
    expect(container.querySelectorAll('.cn-permlist').length).toBe(2)

    fireEvent.click(screen.getByText('Read-only tools'))
    expect(container.querySelectorAll('.cn-permlist').length).toBe(1)

    fireEvent.click(screen.getByText('Read-only tools'))
    expect(container.querySelectorAll('.cn-permlist').length).toBe(2)
  })

  it('starts a long tool list expanded too, not just a short one (2026-08-18)', () => {
    const many = Array.from({ length: 9 }, (_, i) => `get_thing_${i}`)
    const { container } = setup(many)
    expect(container.querySelectorAll('.cn-permlist').length).toBe(1)

    fireEvent.click(screen.getByText('Read-only tools'))
    expect(container.querySelectorAll('.cn-permlist').length).toBe(0)
  })
})

describe('the group menu closes on an outside click', () => {
  it('dismisses without changing anything', () => {
    const { container, persisted } = setup()
    fireEvent.click(groupControl(container, 0))
    expect(container.querySelectorAll('.rd-kebab-pop').length).toBe(1)

    fireEvent.mouseDown(document.body)
    expect(container.querySelectorAll('.rd-kebab-pop').length).toBe(0)
    expect(persisted.group).toEqual([])
  })
})

describe('the ladder holds no permission state of its own', () => {
  it('renders exactly what the caller passes', () => {
    const noop = vi.fn()
    const { rerender } = render(
      <PermissionLadder
        groups={buildToolGroups(TOOLS)}
        perms={{ get_positions: 'blocked' }}
        onSetTool={noop}
        onSetGroup={noop}
      />,
    )
    expect(toolBtn('get_positions', 'Block').getAttribute('aria-pressed')).toBe('true')

    rerender(
      <PermissionLadder
        groups={buildToolGroups(TOOLS)}
        perms={{ get_positions: 'always_allow' }}
        onSetTool={noop}
        onSetGroup={noop}
      />,
    )
    expect(toolBtn('get_positions', 'Always allow').getAttribute('aria-pressed')).toBe('true')
  })
})
