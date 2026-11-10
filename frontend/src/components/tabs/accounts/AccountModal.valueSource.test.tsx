// @vitest-environment jsdom
//
// PR #93 user-acceptance scenarios for the account dialog (TYPED-18/19, SWITCH-17/18/19,
// UI-19, UI-20, FLAG-12): the "Total value" label by flag, the Edit account "Value this account
// by" section and its switch preview, and the Unlink confirmation. Synthetic figures only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

import AccountModal from './AccountModal'
import { fromApiAccount } from './data'
import type { Account, ApiAccount } from './types'
import { overrideFlagsForTests } from '../../../hooks/useFlag'
import { setBaseCurrency } from '../../../utils/format'

const FLAG = 'allow_brokerage_total_valuation'
let restoreFlag: () => void = () => {}

beforeEach(() => {
  setBaseCurrency('USD')
  mockAuthFetch.mockReset()
})
afterEach(() => {
  cleanup()
  restoreFlag()
})

const PLAID = {
  source: 'plaid',
  ref: 'pa-1',
  name: 'Brokerage',
  mask: '4321',
  linked_at: '2026-11-01T00:00:00Z',
  provider_label: 'Plaid',
  status: 'connected' as const,
}

function api(over: Partial<ApiAccount> = {}): ApiAccount {
  return {
    id: 'acc_1',
    name: 'Old 401(k)',
    kind: 'asset',
    type: 'retirement',
    institution: 'Acme Securities',
    currency: 'USD',
    initial_balance: 8000,
    initial_balance_date: '2026-01-01',
    external_ids: [],
    balance: 4980,
    balance_base: 4980,
    cleared_balance: 4980,
    cleared_balance_base: 4980,
    value_source: 'total',
    value_sync: null,
    value_siblings: [],
    value_breakdown: { tracked_holdings: 3020, untracked: 4980, total: 8000, currency: 'USD' },
    ...over,
  } as ApiAccount
}

const acct = (over: Partial<ApiAccount> = {}, holdingsBase = 3020): Account =>
  fromApiAccount(api(over), holdingsBase)

interface Preview {
  holdings: number
  untracked: number
  total: number
  adjustment: number
  value_after: number
}

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function serve(preview: Preview | 'fail' | 'reject' = 'fail') {
  mockAuthFetch.mockImplementation(async (url: string) => {
    if (url.startsWith('/api/accounts/acc_1/value-switch')) {
      if (preview === 'reject') throw new Error('network down')
      if (preview === 'fail') return { ok: false, status: 500, json: async () => ({}) } as Response
      return okJson(preview)
    }
    return okJson({})
  })
}

function previewCalls(): string[] {
  return (mockAuthFetch.mock.calls as Array<[string]>)
    .map(([u]) => u)
    .filter((u) => u.includes('value-switch'))
}

function renderEdit(account: Account, onSave = vi.fn().mockResolvedValue(undefined)) {
  render(
    <AccountModal
      open
      mode="edit"
      account={account}
      institutions={[]}
      onClose={() => {}}
      onSave={onSave}
    />,
  )
  return onSave
}

describe('TYPED-18 / TYPED-19: the amount label by flag (add dialog)', () => {
  function renderAdd(type: 'investment' | 'retirement' | 'cash' | 'credit') {
    render(
      <AccountModal
        open
        mode="add"
        institutions={[]}
        prefill={{ name: 'New account', currency: 'USD', type }}
        onClose={() => {}}
        onSave={vi.fn()}
      />,
    )
  }

  it.each(['investment', 'retirement'] as const)(
    'flag on: a %s account asks for "Total value", with its help text',
    (type) => {
      restoreFlag = overrideFlagsForTests({ [FLAG]: true })
      renderAdd(type)
      expect(screen.getByText('Total value')).toBeTruthy()
      expect(screen.queryByText('Current balance')).toBeNull()
      expect(
        screen.getByText(
          'Holdings and cash together, as your brokerage shows it. You can add holdings later.',
        ),
      ).toBeTruthy()
    },
  )

  it('flag on: a cash account still says "Current balance" with the Starting Balance help', () => {
    restoreFlag = overrideFlagsForTests({ [FLAG]: true })
    renderAdd('cash')
    expect(screen.getByText('Current balance')).toBeTruthy()
    expect(screen.queryByText('Total value')).toBeNull()
    expect(
      screen.getByText(
        'We’ll record this as today’s Starting Balance transaction. You can edit it later.',
      ),
    ).toBeTruthy()
  })

  it('flag on: a credit card still says "Amount currently owed"', () => {
    restoreFlag = overrideFlagsForTests({ [FLAG]: true })
    renderAdd('credit')
    expect(screen.getByText('Amount currently owed')).toBeTruthy()
    expect(screen.queryByText('Total value')).toBeNull()
  })

  it.each(['investment', 'retirement'] as const)(
    'flag off: a %s account keeps "Current balance" and the old help',
    (type) => {
      restoreFlag = overrideFlagsForTests({ [FLAG]: false })
      renderAdd(type)
      expect(screen.getByText('Current balance')).toBeTruthy()
      expect(screen.queryByText('Total value')).toBeNull()
      expect(screen.queryByText(/Holdings and cash together/)).toBeNull()
    },
  )
})

describe('UI-19: "Value this account by"', () => {
  beforeEach(() => {
    restoreFlag = overrideFlagsForTests({ [FLAG]: true })
  })

  it('offers Brokerage total (selected) and Live holdings for an unlinked total account', () => {
    serve()
    renderEdit(acct())
    expect(screen.getByText('Value this account by')).toBeTruthy()
    const group = screen.getByRole('radiogroup', { name: 'Value this account by' })
    const radios = within(group).getAllByRole('radio') as HTMLInputElement[]
    expect(radios.map((r) => r.checked)).toEqual([true, false])
    expect(
      screen.getByText('The total on your brokerage statement. Holdings you add are part of it.'),
    ).toBeTruthy()
    expect(
      screen.getByText('Each holding at live market prices, plus cash. Needs every holding added.'),
    ).toBeTruthy()
    expect(screen.getByText('Brokerage total')).toBeTruthy()
    expect(screen.getByText('Live holdings')).toBeTruthy()
  })

  it('names the connector when the connector keeps the total', () => {
    serve()
    renderEdit(
      acct({
        external_ids: [PLAID],
        value_sync: { status: 'fresh', source: 'plaid', provider_label: 'Plaid', total: 8000 },
      }),
    )
    expect(
      screen.getByText(
        'The total your brokerage shows, synced via Plaid. Holdings you add are part of it.',
      ),
    ).toBeTruthy()
  })

  it('selects Live holdings for an account already on it', () => {
    serve()
    renderEdit(acct({ value_source: 'holdings', value_breakdown: undefined }))
    const radios = within(
      screen.getByRole('radiogroup', { name: 'Value this account by' }),
    ).getAllByRole('radio') as HTMLInputElement[]
    expect(radios.map((r) => r.checked)).toEqual([false, true])
  })

  it('is absent for a cash or credit account (no valuation from the server)', () => {
    serve()
    renderEdit(acct({ type: 'cash', value_source: undefined, value_breakdown: undefined }, 0))
    expect(screen.queryByText('Value this account by')).toBeNull()
  })

  it('FLAG-12: is absent with the flag off (the server sends no value_source) and the rest of the dialog is intact', () => {
    restoreFlag()
    restoreFlag = overrideFlagsForTests({ [FLAG]: false })
    serve()
    renderEdit(acct({ value_source: undefined, value_sync: undefined, value_breakdown: undefined }))
    expect(screen.queryByText('Value this account by')).toBeNull()
    expect(screen.queryByText('Brokerage total')).toBeNull()
    expect(screen.queryByText('Live holdings')).toBeNull()
    expect(screen.getByText('Starting balance')).toBeTruthy()
    expect(previewCalls()).toEqual([])
  })

  it('a waiting total cannot be switched until the first total arrives', () => {
    serve()
    renderEdit(
      acct({
        external_ids: [PLAID],
        balance: 0,
        balance_base: 0,
        value_sync: { status: 'waiting', source: 'plaid', provider_label: 'Plaid' },
      }),
    )
    const radios = within(
      screen.getByRole('radiogroup', { name: 'Value this account by' }),
    ).getAllByRole('radio') as HTMLInputElement[]
    expect(radios.every((r) => r.disabled)).toBe(true)
    expect(screen.getByText('You can choose once the first total arrives.')).toBeTruthy()
  })

  it('warns that a linked total at a shared institution would be counted twice (one sibling, then two)', () => {
    serve()
    const linked = {
      external_ids: [PLAID],
      value_sync: { status: 'fresh', source: 'plaid', provider_label: 'Plaid', total: 8000 },
    } as Partial<ApiAccount>
    renderEdit(acct({ ...linked, value_siblings: ['Brokerage Y'] }))
    expect(screen.getByText('Counted twice: this total already includes Brokerage Y.')).toBeTruthy()
    cleanup()
    renderEdit(acct({ ...linked, value_siblings: ['Brokerage Y', 'Brokerage Z'] }))
    expect(
      screen.getByText('Counted twice: this total already includes 2 of your other accounts.'),
    ).toBeTruthy()
  })

  it('does not warn about siblings for an unlinked total, or once Live holdings is picked', async () => {
    serve({ holdings: 3020, untracked: 4980, total: 8000, adjustment: -4980, value_after: 3020 })
    renderEdit(acct({ value_siblings: ['Brokerage Y'] }))
    expect(screen.queryByText(/Counted twice/)).toBeNull()
    cleanup()

    renderEdit(
      acct({
        external_ids: [PLAID],
        value_siblings: ['Brokerage Y'],
        value_sync: { status: 'fresh', source: 'plaid', provider_label: 'Plaid', total: 8000 },
      }),
    )
    expect(screen.getByText(/Counted twice/)).toBeTruthy()
    fireEvent.click(screen.getByLabelText(/Live holdings/))
    await waitFor(() => expect(screen.queryByText(/Counted twice/)).toBeNull())
  })
})

describe('the switch preview, both directions (SWITCH-01/03/04/08/17)', () => {
  beforeEach(() => {
    restoreFlag = overrideFlagsForTests({ [FLAG]: true })
  })

  async function pick(label: RegExp) {
    fireEvent.click(screen.getByLabelText(label))
  }

  it('to Live holdings with a large untracked part: red zeroing text, to the cent', async () => {
    serve({ holdings: 3020, untracked: 4980, total: 8000, adjustment: -4980, value_after: 3020 })
    renderEdit(acct())
    expect(screen.queryByText(/This account will be worth/)).toBeNull()
    await pick(/Live holdings/)
    const text =
      'This account will be worth $3,020.00, because that’s all the holdings you’ve added. Add your holdings first, or keep Brokerage total.'
    const node = await screen.findByText(text)
    expect(node.className).toContain('bad')
    expect(previewCalls()).toEqual(['/api/accounts/acc_1/value-switch?to=holdings'])
  })

  it('to Live holdings with ≤2% untracked: neutral text, the 200.00 stays as cash', async () => {
    serve({ holdings: 9800, untracked: 200, total: 10000, adjustment: 0, value_after: 10000 })
    renderEdit(acct({ balance: 200, balance_base: 200 }, 9800))
    await pick(/Live holdings/)
    const node = await screen.findByText(
      'The $200.00 that’s untracked will count as cash in this account.',
    )
    expect(node.className).not.toContain('bad')
    expect(screen.queryByText(/This account will be worth/)).toBeNull()
  })

  it('to Live holdings with nothing untracked: "The value stays $8,000.00."', async () => {
    serve({ holdings: 8000, untracked: 0, total: 8000, adjustment: 0, value_after: 8000 })
    renderEdit(acct({ balance: 0, balance_base: 0 }, 8000))
    await pick(/Live holdings/)
    expect(await screen.findByText('The value stays $8,000.00.')).toBeTruthy()
  })

  it('SWITCH-08: with no holdings, clearing the whole typed 8,000.00 is shown as worth $0.00, in red', async () => {
    serve({ holdings: 0, untracked: 8000, total: 8000, adjustment: -8000, value_after: 0 })
    renderEdit(acct({ balance: 8000, balance_base: 8000 }, 0))
    await pick(/Live holdings/)
    const node = await screen.findByText(/This account will be worth \$0\.00, because/)
    expect(node.className).toContain('bad')
  })

  it('the preview money is in the account’s own currency (EUR)', async () => {
    serve({
      holdings: 2745.45,
      untracked: 7254.55,
      total: 10000,
      adjustment: -7254.55,
      value_after: 2745.45,
    })
    renderEdit(acct({ currency: 'EUR', balance: 7254.55, balance_base: 7980.01 }))
    await pick(/Live holdings/)
    expect(await screen.findByText(/This account will be worth €2,745\.45, because/)).toBeTruthy()
  })

  it('back to Brokerage total from Live holdings: the value stays, holdings become a breakdown', async () => {
    serve({ holdings: 3020, untracked: 4980, total: 8000, adjustment: 0, value_after: 8000 })
    renderEdit(acct({ value_source: 'holdings', value_breakdown: undefined }))
    await pick(/Brokerage total/)
    expect(
      await screen.findByText(
        'The value stays $8,000.00. Your holdings become a breakdown of that total.',
      ),
    ).toBeTruthy()
    expect(previewCalls()).toEqual(['/api/accounts/acc_1/value-switch?to=total'])
  })

  it('back to Brokerage total on a linked account adds that the connector’s next total replaces it', async () => {
    serve({ holdings: 3020, untracked: 4980, total: 8000, adjustment: 0, value_after: 8000 })
    renderEdit(
      acct({
        value_source: 'holdings',
        external_ids: [PLAID],
        value_breakdown: undefined,
        value_sync: {
          status: 'compare',
          source: 'plaid',
          provider_label: 'Plaid',
          reported_total: 8000,
        },
      }),
    )
    await pick(/Brokerage total/)
    expect(
      await screen.findByText(
        'The value stays $8,000.00. Your holdings become a breakdown of that total, and Plaid’s next total replaces it.',
      ),
    ).toBeTruthy()
  })

  it('picking the original option again clears the preview', async () => {
    serve({ holdings: 3020, untracked: 4980, total: 8000, adjustment: -4980, value_after: 3020 })
    renderEdit(acct())
    await pick(/Live holdings/)
    await screen.findByText(/This account will be worth/)
    await pick(/Brokerage total/)
    await waitFor(() => expect(screen.queryByText(/This account will be worth/)).toBeNull())
    expect(previewCalls()).toHaveLength(1)
  })

  it('SWITCH-18: a failed or unreachable preview shows no figures and no crash', async () => {
    for (const mode of ['fail', 'reject'] as const) {
      serve(mode)
      renderEdit(acct())
      await pick(/Live holdings/)
      await waitFor(() => expect(previewCalls().length).toBeGreaterThan(0))
      expect(screen.queryByText(/This account will be worth/)).toBeNull()
      expect(screen.queryByText(/that’s untracked will count as cash/)).toBeNull()
      cleanup()
      mockAuthFetch.mockReset()
    }
  })
})

describe('saving a switch', () => {
  beforeEach(() => {
    restoreFlag = overrideFlagsForTests({ [FLAG]: true })
  })

  function saveButton() {
    return screen.getByRole('button', { name: /^Save/ })
  }

  it('sends value_source only when it changed', async () => {
    serve({ holdings: 3020, untracked: 4980, total: 8000, adjustment: -4980, value_after: 3020 })
    const unchanged = renderEdit(acct())
    fireEvent.click(saveButton())
    await waitFor(() => expect(unchanged).toHaveBeenCalledTimes(1))
    expect(unchanged.mock.calls[0]![0]).not.toHaveProperty('value_source')
    expect(unchanged.mock.calls[0]![1]).toBe('acc_1')
    cleanup()

    const changed = renderEdit(acct())
    fireEvent.click(screen.getByLabelText(/Live holdings/))
    await screen.findByText(/This account will be worth/)
    fireEvent.click(saveButton())
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1))
    expect(changed.mock.calls[0]![0]).toMatchObject({ value_source: 'holdings' })
    expect(changed.mock.calls[0]![1]).toBe('acc_1')
  })

  it('switching away and back sends no value_source', async () => {
    serve({ holdings: 3020, untracked: 4980, total: 8000, adjustment: -4980, value_after: 3020 })
    const onSave = renderEdit(acct())
    fireEvent.click(screen.getByLabelText(/Live holdings/))
    fireEvent.click(screen.getByLabelText(/Brokerage total/))
    fireEvent.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    expect(onSave.mock.calls[0]![0]).not.toHaveProperty('value_source')
  })

  // SWITCH-18 asks that the dialog never save a switch blind. Today a failed preview still lets
  // Save through with value_source set (the server then clears the untracked part). Pinned as
  // the CURRENT behaviour; flagged to the owner in the report, not asserted as a defect.
  it('SWITCH-18 (current behaviour): Save still sends the switch when the preview failed', async () => {
    serve('fail')
    const onSave = renderEdit(acct())
    fireEvent.click(screen.getByLabelText(/Live holdings/))
    await waitFor(() => expect(previewCalls()).toHaveLength(1))
    expect((saveButton() as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    expect(onSave.mock.calls[0]![0]).toMatchObject({ value_source: 'holdings' })
  })
})

describe('UI-20: Unlink on a total account asks first', () => {
  beforeEach(() => {
    restoreFlag = overrideFlagsForTests({ [FLAG]: true })
  })

  const linked = (over: Partial<ApiAccount> = {}) =>
    acct({
      external_ids: [PLAID],
      value_sync: { status: 'fresh', source: 'plaid', provider_label: 'Plaid', total: 8000 },
      ...over,
    })

  it('a Brokerage total account is warned the balance stops updating, nothing is written yet', () => {
    serve()
    renderEdit(linked())
    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
    expect(
      screen.getByText(
        'Unlink from Plaid? The balance stops updating and keeps its current value.',
      ),
    ).toBeTruthy()
    const writes = (mockAuthFetch.mock.calls as Array<[string, RequestInit | undefined]>).filter(
      ([, init]) => init?.method === 'PUT',
    )
    expect(writes).toHaveLength(0)
  })

  it('a Live holdings account gets the old, gentler wording', () => {
    serve()
    renderEdit(linked({ value_source: 'holdings', value_breakdown: undefined }))
    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
    expect(screen.getByText('Unlink from Plaid? Nothing booked so far is removed.')).toBeTruthy()
  })

  it('Cancel puts the "Through Plaid" line back', () => {
    serve()
    renderEdit(linked())
    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText(/^Unlink from Plaid\?/)).toBeNull()
    expect(screen.getByText(/^Through Plaid/)).toBeTruthy()
  })

  it('confirming PUTs the link removed and the modal stays open', async () => {
    serve()
    const onSave = renderEdit(linked())
    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
    const row = screen.getByText(/^Unlink from Plaid\?/).closest('.acct-link-row') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Unlink' }))
    await waitFor(() => {
      const put = (mockAuthFetch.mock.calls as Array<[string, RequestInit | undefined]>).find(
        ([, init]) => init?.method === 'PUT',
      )
      expect(put).toBeTruthy()
      expect(put![0]).toBe('/api/accounts/acc_1')
      expect(JSON.parse(put![1]!.body as string)).toEqual({ external_ids: [] })
    })
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('with the flag off the unlink prompt is the old wording (valuation is absent)', () => {
    restoreFlag()
    restoreFlag = overrideFlagsForTests({ [FLAG]: false })
    serve()
    renderEdit(
      linked({ value_source: undefined, value_sync: undefined, value_breakdown: undefined }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
    expect(screen.getByText('Unlink from Plaid? Nothing booked so far is removed.')).toBeTruthy()
  })
})
