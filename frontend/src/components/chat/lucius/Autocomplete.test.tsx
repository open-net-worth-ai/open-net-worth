// @vitest-environment jsdom
//
// Autocomplete.test.tsx — render smoke tests for the inline grouped picker (W2A).

import { createRef, type Ref } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, cleanup, fireEvent, act } from '@testing-library/react'

afterEach(cleanup)
import { Autocomplete } from './Autocomplete'
import type { AutocompleteHandle } from './Autocomplete'
import type { MentionRow, UseMentionablesResult } from '../../../hooks/useMentionables'

// jsdom doesn't implement scrollIntoView; jumpToSection calls it on the section tabs.
Element.prototype.scrollIntoView = vi.fn()

const NOOP_RETRY = vi.fn()

const BASE_ITEM: MentionRow = {
  type: 'account',
  id: 'acc-1',
  label: 'Meridian Bank Multiplier',
  sublabel: 'Savings',
  value: 'acc-1',
  icon: '⬡',
  surface: 'entity',
  onScreen: false,
}

// Default mock: one local item, no error, no remote items.
const mockUseMentionables = vi.fn<
  (query?: string, opts?: { surfaces?: string[]; types?: string[] }) => UseMentionablesResult
>(() => ({
  items: [BASE_ITEM],
  loading: false,
  error: false,
  retry: NOOP_RETRY,
  remoteItems: [],
}))

vi.mock('../../../hooks/useMentionables', () => ({
  useMentionables: (query: string, opts: { surfaces?: string[]; types?: string[] }) =>
    mockUseMentionables(query, opts),
}))

vi.mock('../../../lucius/screenContract', () => ({
  allHandles: () => [],
}))

vi.mock('../../../lucius/identity', () => ({
  Spark: () => null,
  Identity: () => null,
}))

const NOOP = vi.fn()
const EMPTY_SET = new Set<string>()

describe('Autocomplete', () => {
  it('renders without crashing', () => {
    render(<Autocomplete mode="@" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)
    expect(document.querySelector('.ac')).not.toBeNull()
    expect(document.querySelector('.ac-scroll')).not.toBeNull()
    expect(document.querySelector('.ac-foot')).not.toBeNull()
  })

  it('renders an item from the mock backend', () => {
    render(<Autocomplete mode="@" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)
    expect(document.querySelector('.ac-item')).not.toBeNull()
    expect(document.body.textContent).toContain('Meridian Bank Multiplier')
  })

  it('sorts "on screen" so non-transaction types come before transactions (owner report 2026-08-11)', () => {
    // Mirrors the reported shape: several matching transactions interleaved with one
    // matching account, all onScreen. Without the fix, DOM/harvest order (transactions
    // first, account last) buries the account under a wall of individual line items
    // that all happen to match the same query text.
    const items: MentionRow[] = [
      {
        type: 'transaction',
        id: 't1',
        label: 'Vantage Brokerage',
        sublabel: 'txn-1',
        value: 't1',
        icon: '⇅',
        onScreen: true,
      },
      {
        type: 'transaction',
        id: 't2',
        label: 'Vantage Brokerage',
        sublabel: 'txn-2',
        value: 't2',
        icon: '⇅',
        onScreen: true,
      },
      {
        type: 'account',
        id: 'acc-ibkr',
        label: 'Vantage Brokerage',
        sublabel: 'Brokerage',
        value: 'acc-ibkr',
        icon: '⬡',
        onScreen: true,
      },
      {
        type: 'transaction',
        id: 't3',
        label: 'Vantage Brokerage',
        sublabel: 'txn-3',
        value: 't3',
        icon: '⇅',
        onScreen: true,
      },
    ]
    mockUseMentionables.mockReturnValueOnce({
      items,
      loading: false,
      error: false,
      retry: NOOP_RETRY,
      remoteItems: [],
    })

    render(
      <Autocomplete mode="@" query="vantage" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
    )

    const subs = Array.from(document.querySelectorAll('.ac-item-sub')).map((el) => el.textContent)
    expect(subs).toEqual(['Brokerage', 'txn-1', 'txn-2', 'txn-3'])
  })

  it('renders the footer hint for @ mode', () => {
    render(<Autocomplete mode="@" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)
    const foot = document.querySelector('.ac-foot-hint')
    expect(foot?.textContent).toContain('accounts')
  })

  it('renders the footer hint for / mode', () => {
    render(<Autocomplete mode="/" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)
    const foot = document.querySelector('.ac-foot-hint')
    expect(foot?.textContent).toContain('skill')
  })

  it('exposes imperative handle with move / pickHighlighted / count', () => {
    const ref = createRef<AutocompleteHandle>()
    render(
      <Autocomplete
        ref={ref}
        mode="@"
        query=""
        visibleIds={EMPTY_SET}
        onPick={NOOP}
        onClose={NOOP}
      />,
    )
    expect(typeof ref.current!.move).toBe('function')
    expect(typeof ref.current!.pickHighlighted).toBe('function')
    expect(typeof ref.current!.count).toBe('number')
  })

  it('count equals number of items', () => {
    const ref = createRef<AutocompleteHandle>()
    render(
      <Autocomplete
        ref={ref}
        mode="@"
        query=""
        visibleIds={EMPTY_SET}
        onPick={NOOP}
        onClose={NOOP}
      />,
    )
    // One backend item in the mock.
    expect(ref.current!.count).toBe(1)
  })

  describe('Everywhere rows, grouped by type (owner request 2026-08-11)', () => {
    // Owner request 2026-08-11: "i want to group by type as well! payee, transaction..."
    // — replaced the single combined "Everywhere" bucket with one header PER TYPE
    // (labelFor(type), e.g. "Transaction"), still 'remote'-marked so they're visually
    // distinguished from local results, and each now its own section tab.
    const remoteRow: MentionRow = {
      type: 'transaction',
      id: 'txn-99',
      label: 'Starbucks coffee',
      sublabel: '-$6.50',
      value: 'txn-99',
      icon: '↕',
      surface: 'entity',
      onScreen: false,
    }

    it('renders a type header (not a combined "Everywhere" header) when remoteItems are present', () => {
      mockUseMentionables.mockReturnValueOnce({
        items: [remoteRow],
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [remoteRow],
      })

      render(
        <Autocomplete mode="@" query="star" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
      )

      const zones = document.querySelectorAll('.ac-zone')
      const typeZone = Array.from(zones).find((z) => z.textContent?.includes('Transaction'))
      expect(typeZone).toBeDefined()
      expect(typeZone!.classList.contains('remote')).toBe(true)
      expect(Array.from(zones).some((z) => z.textContent?.includes('Everywhere'))).toBe(false)
    })

    it('renders backend-only rows under their own type header', () => {
      mockUseMentionables.mockReturnValueOnce({
        items: [remoteRow],
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [remoteRow],
      })

      render(
        <Autocomplete mode="@" query="star" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
      )

      expect(document.body.textContent).toContain('Starbucks coffee')
      expect(document.body.textContent).toContain('Transaction')
    })

    it('splits remoteItems of different types into separate headers/tabs', () => {
      const payeeRow: MentionRow = {
        type: 'payee',
        id: 'pay-1',
        label: 'Hotel Example',
        sublabel: '17 txns',
        value: 'pay-1',
        icon: '■',
        surface: 'entity',
        onScreen: false,
      }
      mockUseMentionables.mockReturnValueOnce({
        items: [remoteRow, payeeRow],
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [remoteRow, payeeRow],
      })

      render(
        <Autocomplete mode="@" query="park" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
      )
      const zoneLabels = Array.from(document.querySelectorAll('.ac-zone')).map((z) => z.textContent)
      expect(zoneLabels).toContain('Transaction')
      expect(zoneLabels).toContain('Payee')
    })

    it('renders no type headers from the remote pool when remoteItems is empty', () => {
      // Default mock has remoteItems: []
      render(
        <Autocomplete mode="@" query="dbs" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
      )
      const remoteZones = Array.from(document.querySelectorAll('.ac-zone.remote'))
      expect(remoteZones).toHaveLength(0)
    })
  })

  describe('error state', () => {
    it('renders the .ac-error row with retry button when backend fails', () => {
      mockUseMentionables.mockReturnValueOnce({
        items: [],
        loading: false,
        error: true,
        retry: NOOP_RETRY,
        remoteItems: [],
      })

      render(
        <Autocomplete mode="@" query="foo" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
      )

      const errorEl = document.querySelector('.ac-error')
      expect(errorEl).not.toBeNull()
      expect(errorEl!.textContent).toContain("Couldn't reach search")
      expect(document.querySelector('.ac-retry')).not.toBeNull()
    })

    it('calls retry() when the Retry button is clicked', () => {
      const retrySpy = vi.fn()
      mockUseMentionables.mockReturnValueOnce({
        items: [],
        loading: false,
        error: true,
        retry: retrySpy,
        remoteItems: [],
      })

      render(
        <Autocomplete mode="@" query="foo" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
      )

      const retryBtn = document.querySelector<HTMLButtonElement>('.ac-retry')
      expect(retryBtn).not.toBeNull()
      fireEvent.click(retryBtn!)
      expect(retrySpy).toHaveBeenCalledOnce()
    })

    it('does NOT render .ac-error when error=false', () => {
      // Default mock has error: false
      render(
        <Autocomplete mode="@" query="dbs" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
      )
      expect(document.querySelector('.ac-error')).toBeNull()
    })

    it('does NOT render .ac-error when there is no query (even if error=true)', () => {
      mockUseMentionables.mockReturnValueOnce({
        items: [],
        loading: false,
        error: true,
        retry: NOOP_RETRY,
        remoteItems: [],
      })

      render(<Autocomplete mode="@" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)
      // No query → error row suppressed; empty state shows instead.
      expect(document.querySelector('.ac-error')).toBeNull()
    })
  })

  describe('slash / verb mode — skill-only filter', () => {
    it('shows skill rows in / mode', () => {
      const skillRow: MentionRow = {
        type: 'skill',
        id: 'sk-1',
        label: 'portfolio-summary',
        sublabel: 'Summarize your portfolio',
        value: 'sk-1',
        icon: '/',
        surface: 'verb',
        onScreen: false,
      }
      mockUseMentionables.mockReturnValueOnce({
        items: [skillRow],
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [],
      })

      render(<Autocomplete mode="/" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)
      expect(document.body.textContent).toContain('portfolio-summary')
    })

    it('excludes on-screen entity rows (accounts etc.) from / mode', () => {
      // backendItems contains both an on-screen account (entity surface) and a skill (verb surface).
      // Only the skill should appear in / mode.
      const accountRow: MentionRow = {
        type: 'account',
        id: 'acc-s',
        label: 'Song Han Bank',
        sublabel: 'Savings',
        value: 'acc-s',
        icon: '⬡',
        surface: 'entity',
        onScreen: true,
      }
      const skillRow: MentionRow = {
        type: 'skill',
        id: 'sk-2',
        label: 'tax-summary',
        sublabel: 'Tax estimate',
        value: 'sk-2',
        icon: '/',
        surface: 'verb',
        onScreen: false,
      }
      mockUseMentionables.mockReturnValueOnce({
        items: [accountRow, skillRow],
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [],
      })

      render(<Autocomplete mode="/" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)
      expect(document.body.textContent).toContain('tax-summary')
      expect(document.body.textContent).not.toContain('Song Han Bank')
    })
  })

  describe('searching spinner', () => {
    it('renders .ac-searching when loading=true and query is present', () => {
      mockUseMentionables.mockReturnValueOnce({
        items: [],
        loading: true,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [],
      })

      render(
        <Autocomplete mode="@" query="apple" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />,
      )
      expect(document.querySelector('.ac-searching')).not.toBeNull()
    })

    it('renders .ac-searching when loading=true and query is EMPTY (right after typing a bare "@")', () => {
      // Regression (owner report 2026-08-11, "the mention view can be almost 0 height"):
      // this is the very first render after typing "@" — before any query text and before
      // the debounced fetch resolves. It used to render nothing at all (this spinner
      // required a query; the empty-state below required !loading), so the popover
      // visibly collapsed to just its footer strip for that window.
      mockUseMentionables.mockReturnValueOnce({
        items: [],
        loading: true,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [],
      })

      render(<Autocomplete mode="@" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)
      expect(document.querySelector('.ac-searching')).not.toBeNull()
    })
  })

  describe('section tabs (owner request 2026-08-11)', () => {
    const ON_SCREEN_ITEM: MentionRow = {
      type: 'holding',
      id: 'h1',
      label: 'Vantage Brokerage',
      sublabel: 'SGD 554',
      value: 'h1',
      icon: '◆',
      onScreen: true,
    }
    const REMOTE_ITEM: MentionRow = {
      type: 'account',
      id: 'a1',
      label: 'Vantage Brokerage',
      sublabel: '0 SGD',
      value: 'a1',
      icon: '⬡',
      onScreen: false,
    }

    const renderVantage = (ref?: Ref<AutocompleteHandle>) => {
      mockUseMentionables.mockReturnValue({
        items: [ON_SCREEN_ITEM],
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [REMOTE_ITEM],
      })
      render(
        <Autocomplete
          ref={ref}
          mode="@"
          query="vantage"
          visibleIds={EMPTY_SET}
          onPick={NOOP}
          onClose={NOOP}
        />,
      )
    }
    const tabEls = () => Array.from(document.querySelectorAll('.ac-tab'))
    const activeTab = () => document.querySelector('.ac-tab.active')?.textContent
    const zones = () => Array.from(document.querySelectorAll('.ac-zone')).map((z) => z.textContent)

    it('renders an All tab then one tab per section present, with All active', () => {
      renderVantage()
      // Everywhere rows are grouped by type (2026-08-11) — REMOTE_ITEM's type is 'account'.
      expect(tabEls().map((t) => t.textContent)).toEqual(['All', 'On this screen', 'Account'])
      expect(activeTab()).toBe('All')
    })

    it('clicking a tab filters the list to that section, and All brings everything back', () => {
      renderVantage()
      fireEvent.click(tabEls()[1]!)
      expect(activeTab()).toBe('On this screen')
      expect(zones()).toEqual(['◉On this screen'])
      expect(document.querySelectorAll('.ac-item')).toHaveLength(1)
      // The strip keeps every tab while one narrows the list.
      expect(tabEls().map((t) => t.textContent)).toEqual(['All', 'On this screen', 'Account'])

      fireEvent.click(tabEls()[0]!)
      expect(activeTab()).toBe('All')
      expect(document.querySelectorAll('.ac-item')).toHaveLength(2)
    })

    it('a type tab asks the backend for that type alone', () => {
      renderVantage()
      expect(mockUseMentionables).toHaveBeenLastCalledWith(
        'vantage',
        expect.objectContaining({ types: undefined }),
      )
      fireEvent.click(tabEls()[2]!)
      expect(activeTab()).toBe('Account')
      expect(mockUseMentionables).toHaveBeenLastCalledWith(
        'vantage',
        expect.objectContaining({ types: ['account'] }),
      )
      const names = Array.from(document.querySelectorAll('.ac-item-name')).map((n) => n.textContent)
      expect(names).toEqual(['Vantage Brokerage'])
      expect(zones()).toEqual(['Account'])
    })

    it('moveSection (the imperative handle Composer.tsx drives) steps through tabs and clamps at the ends', () => {
      const ref = createRef<AutocompleteHandle>()
      renderVantage(ref)
      act(() => ref.current!.moveSection(1))
      expect(activeTab()).toBe('On this screen')
      act(() => ref.current!.moveSection(1))
      expect(activeTab()).toBe('Account')
      // Last tab — a further +1 must clamp, not go out of bounds.
      act(() => ref.current!.moveSection(1))
      expect(activeTab()).toBe('Account')
      act(() => ref.current!.moveSection(-1))
      act(() => ref.current!.moveSection(-1))
      act(() => ref.current!.moveSection(-1))
      expect(activeTab()).toBe('All')
    })

    it('renders no tab strip when only one section is present', () => {
      mockUseMentionables.mockReturnValue({
        items: [ON_SCREEN_ITEM],
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [],
      })
      render(
        <Autocomplete
          mode="@"
          query="vantage"
          visibleIds={EMPTY_SET}
          onPick={NOOP}
          onClose={NOOP}
        />,
      )
      expect(document.querySelector('.ac-tabs')).toBeNull()
    })
  })

  describe('Privacy Mode (.money masking in .ac-item-sub and .ac-item-v)', () => {
    it('wraps amount in .ac-item-sub with .money for accounts, holdings, and transactions', () => {
      const items: MentionRow[] = [
        {
          type: 'account',
          id: 'acc-1',
          label: 'OCBC 360',
          sublabel: '12,345 SGD',
          value: 'acc-1',
          icon: '🏦',
          onScreen: true,
        },
        {
          type: 'holding',
          id: 'h-1',
          label: 'BTC',
          sublabel: 'Coinbase • 0.0273087',
          value: 'h-1',
          icon: '₿',
          onScreen: true,
        },
        {
          type: 'transaction',
          id: 't-1',
          label: 'Grab',
          sublabel: 'Grab • OCBC 360 • 2026-08-30 • -18.50 SGD',
          value: 't-1',
          icon: '⇅',
          onScreen: true,
        },
      ]
      mockUseMentionables.mockReturnValue({
        items,
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [],
      })

      render(<Autocomplete mode="@" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)

      const subElements = document.querySelectorAll('.ac-item-sub')
      expect(subElements.length).toBe(3)

      // Account sublabel: entire 12,345 SGD is inside .money
      const accMoney = subElements[0]!.querySelector('.money')
      expect(accMoney).toBeTruthy()
      expect(accMoney?.textContent).toBe('12,345 SGD')

      // Holding sublabel: only 0.0273087 is inside .money
      const holdingMoney = subElements[1]!.querySelector('.money')
      expect(holdingMoney).toBeTruthy()
      expect(holdingMoney?.textContent).toBe('0.0273087')
      expect(subElements[1]!.textContent).toBe('Coinbase • 0.0273087')

      // Transaction sublabel: only -18.50 SGD is inside .money
      const txnMoney = subElements[2]!.querySelector('.money')
      expect(txnMoney).toBeTruthy()
      expect(txnMoney?.textContent).toBe('-18.50 SGD')
      expect(subElements[2]!.textContent).toBe('Grab • OCBC 360 • 2026-08-30 • -18.50 SGD')
    })

    it('wraps maskable .ac-item-v in .money while leaving non-amount selectors plain', () => {
      const items: MentionRow[] = [
        {
          type: 'account',
          id: 'acc-1',
          label: 'DBS',
          sublabel: 'Savings',
          value: 'SGD 14,710',
          icon: '🏦',
          onScreen: true,
        },
        {
          type: 'filter',
          id: 'f-1',
          label: 'Timespan',
          sublabel: 'Filter',
          value: '1Y',
          icon: '🔍',
          onScreen: true,
        },
      ]
      mockUseMentionables.mockReturnValue({
        items,
        loading: false,
        error: false,
        retry: NOOP_RETRY,
        remoteItems: [],
      })

      render(<Autocomplete mode="@" query="" visibleIds={EMPTY_SET} onPick={NOOP} onClose={NOOP} />)

      const values = document.querySelectorAll('.ac-item-v')
      expect(values.length).toBe(2)
      expect(values[0]!.classList.contains('money')).toBe(true)
      expect(values[1]!.classList.contains('money')).toBe(false)
    })
  })
})
