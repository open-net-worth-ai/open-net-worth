// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation } from '../../../../api/automations'
import { clearQueryCache } from '../../../../state/queryCache'
import { overrideFlagsForTests } from '../../../../hooks/useFlag'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

const api = vi.hoisted(() => ({
  listAutomations: vi.fn(),
  getAutomationOptions: vi.fn(),
  updateAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  reorderRules: vi.fn(),
  previewRule: vi.fn(),
  applyRule: vi.fn(),
  createRule: vi.fn(),
  updateRule: vi.fn(),
  undoRuleApply: vi.fn(),
}))
vi.mock('../../../../api/automations', async (load) => ({ ...(await load()), ...api }))

const route = vi.hoisted(() => ({ search: {} as Record<string, string> }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ search: route.search }),
}))

import { Automations } from '../../Automations'
import { RulesView } from './RulesView'

const OPTIONS = {
  accounts: [{ id: 'acc_card', label: 'Card', currency: 'USD' }],
  payees: [{ id: 'pay_acme', label: 'Acme' }],
  instruments: [],
  models: [],
  base_currency: 'USD',
}
const CATEGORIES = [
  { id: 'cat_a', label: 'Groceries' },
  { id: 'cat_b', label: 'Household' },
]

const rule = (id: string, position: number, over: Partial<Automation> = {}): Automation => ({
  id,
  name: `Rule ${id}`,
  description: '',
  instructions: `When payee is Acme ${id}: set category to Groceries.`,
  status: 'active',
  trigger: {
    kind: 'event',
    event: 'txn',
    when: { match: 'all', conditions: [{ field: 'payee', op: 'is', value: 'pay_acme' }] },
    limit: 'each',
  },
  execution: {
    kind: 'transaction_transform',
    position,
    stop: false,
    override_existing: false,
    set: { category: 'cat_a' },
  },
  model: '',
  effort: 'low',
  run_count: 0,
  next_run_at: null,
  last_run_at: null,
  last_status: null,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
  provenance: {},
  stats: { match_count: 3, last_matched_at: new Date().toISOString() },
  ...over,
})

const reply = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })

let restore: () => void

beforeEach(() => {
  clearQueryCache()
  Object.values(api).forEach((fn) => fn.mockReset())
  authFetch.mockImplementation((url: string) =>
    url.startsWith('/api/categories')
      ? reply(CATEGORIES)
      : Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) }),
  )
  api.getAutomationOptions.mockResolvedValue(OPTIONS)
  route.search = {}
  restore = overrideFlagsForTests({ transaction_rules: true, bank_data_sync: false })
})
afterEach(() => {
  cleanup()
  restore()
})

/** Choose `label` from one of the editor's own dropdowns (never a native <select>). */
function pick(id: string, label: string) {
  fireEvent.click(document.getElementById(id)!)
  fireEvent.mouseDown(screen.getByRole('option', { name: label }))
}

function Harness({ initial }: { initial: Automation[] }) {
  const [rows, setRows] = useState(initial)
  return (
    <RulesView automations={rows} setAutomations={setRows} options={OPTIONS} onError={vi.fn()} />
  )
}

describe('Rules list', () => {
  it('lists rules in order with their sentence and match count, and explains the order', () => {
    render(<Harness initial={[rule('r2', 1), rule('r1', 0)]} />)
    const items = screen.getAllByRole('listitem').filter((li) => li.className.includes('rl-item'))
    expect(items.map((li) => li.querySelector('.rl-name-t')!.textContent)).toEqual([
      'Rule r1',
      'Rule r2',
    ])
    expect(screen.getByText(/When payee is Acme r1/)).toBeTruthy()
    expect(screen.getAllByText(/3 matches/).length).toBe(2)
    expect(screen.getByText(/run top to bottom; every matching rule applies/i)).toBeTruthy()
  })

  it('explains Rules versus Lucius when there are none', () => {
    render(<Harness initial={[]} />)
    expect(screen.getByText(/no ai/i)).toBeTruthy()
  })

  it('reorders from the keyboard and saves the whole order', async () => {
    api.reorderRules.mockImplementation(async (ids: string[]) =>
      ids.map((id, position) => rule(id, position)),
    )
    render(<Harness initial={[rule('r1', 0), rule('r2', 1)]} />)
    fireEvent.keyDown(screen.getByRole('button', { name: /reorder rule r1/i }), {
      key: 'ArrowDown',
    })
    await waitFor(() => expect(api.reorderRules).toHaveBeenCalledWith(['r2', 'r1']))
    await waitFor(() =>
      expect([...document.querySelectorAll('.rl-name-t')].map((el) => el.textContent)).toEqual([
        'Rule r2',
        'Rule r1',
      ]),
    )
  })

  it('reorders by dragging a handle onto another row', async () => {
    api.reorderRules.mockResolvedValue([rule('r2', 0), rule('r1', 1)])
    render(<Harness initial={[rule('r1', 0), rule('r2', 1)]} />)
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    fireEvent.dragStart(screen.getByRole('button', { name: /reorder rule r1/i }), { dataTransfer })
    const target = screen.getByRole('button', { name: /reorder rule r2/i }).closest('li')!
    fireEvent.dragOver(target, { dataTransfer })
    fireEvent.drop(target, { dataTransfer })
    await waitFor(() => expect(api.reorderRules).toHaveBeenCalledWith(['r2', 'r1']))
  })

  it('pauses a rule with the switch', async () => {
    api.updateAutomation.mockResolvedValue(rule('r1', 0, { status: 'paused' }))
    render(<Harness initial={[rule('r1', 0)]} />)
    fireEvent.click(screen.getByRole('switch', { name: /rule r1 is on/i }))
    await waitFor(() =>
      expect(api.updateAutomation).toHaveBeenCalledWith('r1', { status: 'paused' }),
    )
    expect(await screen.findByText('Paused')).toBeTruthy()
  })
})

describe('Rule editor', () => {
  it('never offers the ledger’s split marker as a category to set', async () => {
    authFetch.mockImplementation((url: string) =>
      url.startsWith('/api/categories')
        ? reply([...CATEGORIES, { id: 'split', label: 'Split' }])
        : Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) }),
    )
    render(<Harness initial={[]} />)
    fireEvent.click(screen.getAllByRole('button', { name: /new rule/i })[0]!)
    const field = await screen.findByLabelText('Set category')
    expect(field.tagName).not.toBe('SELECT')
    await waitFor(() => {
      fireEvent.click(field)
      expect(screen.getByRole('option', { name: 'Groceries' })).toBeTruthy()
    })
    expect(screen.queryByRole('option', { name: 'Split' })).toBeNull()
  })

  it('opens with Set category ready and offers only the actions not yet added', async () => {
    render(<Harness initial={[]} />)
    fireEvent.click(screen.getAllByRole('button', { name: /new rule/i })[0]!)
    await screen.findByLabelText('Name')
    expect(screen.getByLabelText('Set category')).toBeTruthy()
    expect(screen.queryByLabelText('Set payee')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add action' }))
    const offered = screen.getAllByRole('menuitem').map((item) => item.textContent)
    // A rule sets one category or splits — never both.
    expect(offered).not.toContain('Set category')
    expect(offered).not.toContain('Split into lines')
    expect(offered).toContain('Add tags')
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add tags' }))
    expect(screen.getByLabelText('Add tags').getAttribute('placeholder')).toMatch(
      /search or create/i,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Remove set category' }))
    expect(screen.queryByLabelText('Set category')).toBeNull()
  })

  it('shows what is missing only after Create is pressed', async () => {
    render(<Harness initial={[]} />)
    fireEvent.click(screen.getAllByRole('button', { name: /new rule/i })[0]!)
    await screen.findByLabelText('Name')
    expect(screen.queryByText('Finish every condition.')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }))
    expect(await screen.findByText('Finish every condition.')).toBeTruthy()
    expect(screen.getByText('Add at least one action.')).toBeTruthy()
    expect(api.createRule).not.toHaveBeenCalled()
  })

  it('offers Create with Lucius from New rule', async () => {
    const ask = vi.fn()
    render(
      <RulesView
        automations={[]}
        setAutomations={vi.fn()}
        options={OPTIONS}
        onError={vi.fn()}
        onAskLucius={ask}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'New rule' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /create with lucius/i }))
    expect(ask).toHaveBeenCalledWith(expect.stringMatching(/transaction Rule/))
  })

  it('builds the contract payload (match any, a split) and shows the preview', async () => {
    api.previewRule.mockResolvedValue({
      total: 2,
      matches: [
        {
          transaction_id: 't1',
          date: '2026-11-02',
          description: 'ACME',
          memo: '',
          payee_id: 'pay_acme',
          payee_name: 'Acme',
          account_id: 'acc_card',
          account_name: 'Card',
          amount: 100,
          currency: 'USD',
          kind: 'expense',
          category: null,
          tags: [],
          splits: [],
          after: {
            category: 'cat_a',
            tags: [],
            description: 'ACME',
            memo: '',
            payee_id: 'pay_acme',
            splits: [],
          },
          skipped_reason: null,
        },
        {
          transaction_id: 't2',
          date: '2026-11-01',
          description: 'ACME',
          memo: '',
          payee_id: 'pay_acme',
          payee_name: 'Acme',
          account_id: 'acc_card',
          account_name: 'Card',
          amount: 50,
          currency: 'USD',
          kind: 'expense',
          category: 'cat_b',
          tags: [],
          splits: [],
          after: null,
          skipped_reason: 'category picked by you',
        },
      ],
    })
    api.applyRule.mockResolvedValue({
      applied: 1,
      unchanged: 0,
      skipped: [],
      checkpoint_id: 'cp_1',
    })
    api.createRule.mockImplementation(async (draft) => rule('new', 0, { name: draft.name }))
    render(<Harness initial={[]} />)
    fireEvent.click(screen.getAllByRole('button', { name: /new rule/i })[0]!)
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Acme split' } })
    pick('rule-match', 'any')
    pick('automation-condition-0-value', 'Acme')
    fireEvent.click(screen.getByRole('button', { name: 'Remove set category' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add action' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Split into lines' }))
    fireEvent.change(document.getElementById('rule-split-0-value')!, { target: { value: '30' } })
    await waitFor(() => pick('rule-split-0-category', 'Groceries'))
    pick('rule-split-1-category', 'Household')

    expect(await screen.findByText('Matches 2 transactions', {}, { timeout: 3000 })).toBeTruthy()
    expect(screen.getByText('Skipped: category picked by you')).toBeTruthy()
    expect(screen.getAllByText('Category: Uncategorized → Groceries').length).toBe(1)
    expect(api.previewRule).toHaveBeenLastCalledWith(
      expect.objectContaining({ execution: expect.objectContaining({ position: 0 }) }),
      50,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Apply to 1' }))
    await waitFor(() => expect(api.applyRule).toHaveBeenCalledWith(expect.anything(), ['t1']))
    expect(await screen.findByText(/Applied to 1 transaction/)).toBeTruthy()
    api.undoRuleApply.mockResolvedValue(undefined)
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(api.undoRuleApply).toHaveBeenCalledWith('cp_1'))
    expect(await screen.findByText(/Undone/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Create rule' }))
    await waitFor(() => expect(api.createRule).toHaveBeenCalledTimes(1))
    expect(api.createRule).toHaveBeenCalledWith({
      name: 'Acme split',
      instructions: expect.stringContaining('payee is Acme'),
      trigger: {
        kind: 'event',
        event: 'txn',
        when: {
          match: 'any',
          conditions: [{ field: 'payee', op: 'is', value: 'pay_acme' }],
        },
      },
      execution: {
        kind: 'transaction_transform',
        position: 0,
        stop: false,
        override_existing: false,
        set: {},
        splits: [
          { amount: { type: 'fixed', value: 30 }, category: 'cat_a' },
          { amount: { type: 'remainder' }, category: 'cat_b' },
        ],
      },
      status: 'active',
    })
  })
})

describe('Automations Rules tab', () => {
  const lucius = {
    ...rule('lu', 0),
    id: 'lu',
    name: 'Morning brief',
    execution: { kind: 'lucius' },
    trigger: { kind: 'manual' },
  } as Automation

  beforeEach(() => {
    api.listAutomations.mockResolvedValue({
      automations: [rule('r1', 0), lucius],
      showing: 2,
      total: 2,
    })
  })

  it('shows the tab only with the flag on', async () => {
    const { unmount } = render(<Automations />)
    expect(await screen.findByRole('tab', { name: 'Rules' })).toBeTruthy()
    unmount()
    restore()
    restore = overrideFlagsForTests({ transaction_rules: false, bank_data_sync: false })
    render(<Automations />)
    await screen.findByText('Morning brief')
    expect(screen.queryByRole('tab', { name: 'Rules' })).toBeNull()
  })

  it('keeps Rules out of the Lucius list and shows them under ?view=rules', async () => {
    const { unmount } = render(<Automations />)
    await screen.findByText('Morning brief')
    expect(screen.queryByText('Rule r1')).toBeNull()
    unmount()
    route.search = { view: 'rules' }
    render(<Automations />)
    expect(await screen.findByText('Rule r1')).toBeTruthy()
    expect(screen.queryByText('Morning brief')).toBeNull()
  })
})

describe('Rule templates', () => {
  it('opens the editor pre-filled from a template, leaving the owner to pick their own values', async () => {
    render(<Harness initial={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Clean up a payee template' }))
    expect(await screen.findByLabelText('Set payee')).toBeTruthy()
    expect(screen.queryByLabelText('Set category')).toBeNull()
    expect(document.getElementById('automation-condition-0-field')!.textContent).toBe('Description')
  })

  it('a split template starts with half to one line and the rest to another', async () => {
    render(<Harness initial={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Browse all templates' }))
    fireEvent.click(screen.getByRole('button', { name: 'Split a shared purchase template' }))
    expect(((await screen.findByLabelText('Percent')) as HTMLInputElement).value).toBe('50')
    expect(document.getElementById('rule-split-1-kind')!.textContent).toBe('The remainder')
  })
})
