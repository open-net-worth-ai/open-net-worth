// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { PriceAlertsModal } from './PriceAlertsModal'
import type { AlertModalItem } from './PriceAlertsModal'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

// PriceAlertsModal self-builds its Lucius context from usePortfolio when no luciusCtx is
// passed — stub it so the modal mounts deterministically, mirroring AssetAlertsCard.test.tsx.
vi.mock('../../hooks/useLuciusContext', () => ({
  useLuciusContext: () => ({ holdingsBySymbol: {}, targets: {}, classLabels: {} }),
}))

const ok = (body: unknown = {}) => ({ ok: true, status: 200, json: async () => body })

function item(over: Partial<AlertModalItem> = {}): AlertModalItem {
  return {
    id: 'wli-1',
    value: 'ORCL',
    label: 'Oracle Corporation',
    asset_class: 'equity',
    price_alerts: [],
    monitor_news: true,
    custom_feed_urls: [],
    ...over,
  }
}

function props(over: Partial<AlertModalItem> = {}, extra: Record<string, unknown> = {}) {
  return {
    item: item(over),
    ccy: 'USD',
    livePrice: 190.24,
    canRemove: false,
    backLabel: 'Back to watchlist',
    onClose: vi.fn(),
    onSaved: vi.fn(),
    ...extra,
  }
}

const TWO = [
  {
    id: 'o2',
    op: '<=' as const,
    value: 182,
    name: 'Good entry zone',
    note: 'Check OCI growth.',
    channels: ['telegram'],
  },
  {
    id: 'o3',
    op: '>=' as const,
    value: 285,
    note: '',
    on_trigger: 'cooldown' as const,
    cooldown_minutes: 1440,
  },
]

const calls = (method: string) => mockAuthFetch.mock.calls.filter(([, o]) => o?.method === method)
const body = (call: unknown[]) => JSON.parse((call[1] as { body: string }).body)

afterEach(cleanup)
beforeEach(() => {
  mockAuthFetch.mockReset()
  // Default: every call succeeds; a GET returns an empty alert list / stats.
  mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method) return ok({})
    if (url === '/api/alert-channels')
      return ok({
        channels: [
          { id: 'telegram', label: 'Telegram', available: true },
          { id: 'email', label: 'Email', available: true },
        ],
      })
    if (url === '/api/watchlist')
      return ok({ config: { notify_routes: { price_alerts: ['telegram'] } } })
    return ok(url.includes('/api/alerts') ? { alerts: [] } : { stats: {} })
  })
})

describe('PriceAlertsModal — manage view', () => {
  it('lists every alert on the ticker with its name, cooldown, channel and distance', async () => {
    const { getByText, getAllByText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    expect(getByText('ORCL · Oracle Corporation')).toBeTruthy()
    expect(getByText('Good entry zone')).toBeTruthy()
    // Unnamed alerts fall back to their condition, never to a note fragment.
    expect(getByText('Rises above $285.00')).toBeTruthy()
    expect(getByText('4.3% away')).toBeTruthy()
    expect(getByText('49.8% away')).toBeTruthy()
    expect(getByText('2 of 2 on')).toBeTruthy()
    await waitFor(() =>
      expect(getByText(/24 hour cooldown · In-app \+ Telegram · Has instructions/)).toBeTruthy(),
    )
    expect(getAllByText('New alert').length).toBeGreaterThan(0)
  })

  it('shows the empty state with a New alert action when the ticker has no alerts', () => {
    const { getByText } = render(<PriceAlertsModal {...props()} />)
    expect(getByText('No alerts on ORCL yet')).toBeTruthy()
  })

  it('a row toggle PATCHes enabled on that alert and reports the save to the caller', async () => {
    const p = props({ price_alerts: TWO })
    const { getByLabelText } = render(<PriceAlertsModal {...p} />)
    fireEvent.click(getByLabelText('Turn off the ≤ $182.00 alert'))
    await waitFor(() => expect(calls('PATCH')).toHaveLength(1))
    expect(calls('PATCH')[0]![0]).toBe('/api/alerts/o2')
    expect(body(calls('PATCH')[0]!)).toEqual({ enabled: false })
    await waitFor(() => expect(p.onSaved).toHaveBeenCalled())
  })

  it('a row toggle whose PATCH fails flips back and shows the error', async () => {
    mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) =>
      init?.method === 'PATCH'
        ? { ok: false, status: 500, json: async () => ({}) }
        : ok(url.includes('/api/alerts') ? { alerts: [] } : { stats: {} }),
    )
    const { getByLabelText, getByText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    const sw = getByLabelText('Turn off the ≤ $182.00 alert') as HTMLInputElement
    expect(sw.checked).toBe(true)
    fireEvent.click(sw)
    await waitFor(() => expect(getByText('Could not save — please try again.')).toBeTruthy())
    expect((getByLabelText('Turn off the ≤ $182.00 alert') as HTMLInputElement).checked).toBe(true)
  })

  it('the mute switch PATCHes the watchlist item immediately (no Save button)', async () => {
    const { getByLabelText, queryByText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    expect(queryByText('Save')).toBeNull()
    fireEvent.click(getByLabelText('Mute ORCL'))
    await waitFor(() => expect(calls('PATCH')).toHaveLength(1))
    expect(calls('PATCH')[0]![0]).toBe('/api/watchlist/items/wli-1')
    expect(body(calls('PATCH')[0]!)).toEqual({ monitor_news: false })
  })

  it('hides the ticker-level settings for a standalone alert with no watchlist item', () => {
    const { queryByText } = render(<PriceAlertsModal {...props({ id: '', price_alerts: TWO })} />)
    expect(queryByText('Mute ORCL')).toBeNull()
    expect(queryByText('Additional news sources')).toBeNull()
  })

  it('the Additional news sources copy states the exact fetch cadence and says when the lane is paused', async () => {
    mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method) return ok({})
      if (url === '/api/watchlist') return ok({ config: { enabled: true, news_freshness: 'off' } })
      return ok(url.includes('/api/alerts') ? { alerts: [] } : { stats: {} })
    })
    const { getByText } = render(<PriceAlertsModal {...props({ price_alerts: TWO })} />)
    fireEvent.click(getByText('Additional news sources'))
    expect(getByText(/checked every 90 seconds/)).toBeTruthy()
    await waitFor(() => expect(getByText(/Paused right now: RSS freshness is off/)).toBeTruthy())
  })

  it('a feed row commits on blur through the item PATCH and drops an empty row', async () => {
    const { getByText, getByPlaceholderText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    fireEvent.click(getByText('Additional news sources'))
    fireEvent.click(getByText('Add feed'))
    const input = getByPlaceholderText('https://example.com/feed.xml')
    fireEvent.change(input, { target: { value: 'https://example.com/feed.xml' } })
    fireEvent.blur(input)
    await waitFor(() => expect(calls('PATCH')).toHaveLength(1))
    expect(body(calls('PATCH')[0]!)).toEqual({ custom_feed_urls: ['https://example.com/feed.xml'] })
  })

  it('a 422 on custom_feed_urls surfaces its detail instead of the generic message', async () => {
    mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH')
        return {
          ok: false,
          status: 422,
          json: async () => ({ detail: "custom_feed_urls: 'x' is not a safe URL" }),
        }
      return ok(url.includes('/api/alerts') ? { alerts: [] } : { stats: {} })
    })
    const { getByText, getByPlaceholderText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    fireEvent.click(getByText('Additional news sources'))
    fireEvent.click(getByText('Add feed'))
    const input = getByPlaceholderText('https://example.com/feed.xml')
    fireEvent.change(input, { target: { value: 'x' } })
    fireEvent.blur(input)
    await waitFor(() => expect(getByText("custom_feed_urls: 'x' is not a safe URL")).toBeTruthy())
  })

  it('shows exact alert prices on an alert-only scale, with the 52-week range separate', async () => {
    mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method) return ok({})
      if (url.includes('/stats'))
        return ok({ stats: { week52_low: 99.26, week52_high: 198.31, prev_close: 187.58 } })
      return ok({ alerts: [] })
    })
    const { getByText } = render(
      <PriceAlertsModal
        {...props(
          {
            price_alerts: [
              { id: 'low', op: '<=', value: 182.25 },
              { id: 'high', op: '>=', value: 285.75 },
            ],
            instrument_id: 'inst-1',
          },
          { livePrice: 190.24 },
        )}
      />,
    )
    await waitFor(() => expect(getByText('52w low', { exact: false })).toBeTruthy())
    expect(getByText('Alert levels')).toBeTruthy()
    expect([...document.querySelectorAll('.pa-tklab')].map((el) => el.textContent)).toEqual([
      '$182.25',
      '$285.75',
    ])
    const ticks = [...document.querySelectorAll<HTMLElement>('.pa-tk')]
    expect(parseFloat(ticks[0]!.style.left)).toBeLessThan(10)
    expect(parseFloat(ticks[1]!.style.left)).toBeGreaterThan(90)
    expect(getByText('+1.42% today')).toBeTruthy()
  })

  it('Lucius suggestions are created directly as alerts, one per Add', async () => {
    const { getByText, getAllByText } = render(
      <PriceAlertsModal
        {...props(
          { price_alerts: TWO },
          { luciusCtx: { holdingsBySymbol: {}, targets: {}, classLabels: {} } },
        )}
      />,
    )
    fireEvent.click(getByText('Suggest'))
    expect(getByText(/Lucius suggests \d alert/)).toBeTruthy()
    fireEvent.click(getAllByText('Add')[0]!)
    await waitFor(() => expect(calls('POST')).toHaveLength(1))
    const b = body(calls('POST')[0]!)
    expect(b.ticker).toBe('ORCL')
    expect(b.kind).toBe('price')
    expect(b.name).toBe('Entry zone')
    expect(b.on_trigger).toBe('cooldown')
    expect(b.cooldown_minutes).toBe(1440)
    expect(b.channels).toBeUndefined() // the server starts it from the price-alert default
  })
})

describe('PriceAlertsModal — create / edit form', () => {
  it('New alert opens the form pre-filled from the first suggestion and Create POSTs one alert', async () => {
    const { getAllByText, getByText, getByLabelText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    fireEvent.click(getAllByText('New alert')[0]!)
    expect(getByText('Alert criteria')).toBeTruthy()
    fireEvent.change(getByLabelText('Name (optional)'), { target: { value: 'Stop-loss zone' } })
    fireEvent.change(getByLabelText('Cooldown'), { target: { value: 'off' } })
    await waitFor(() =>
      expect(within(getByLabelText('Notifications')).getByText('In-app + Email')).toBeTruthy(),
    )
    expect((getByLabelText('Notifications') as HTMLSelectElement).value).toBe('telegram')
    fireEvent.change(getByLabelText('Notifications'), { target: { value: 'email' } })
    fireEvent.click(getByText('Create alert'))
    await waitFor(() => expect(calls('POST')).toHaveLength(1))
    const b = body(calls('POST')[0]!)
    expect(b).toMatchObject({
      ticker: 'ORCL',
      name: 'Stop-loss zone',
      kind: 'price',
      op: '<=',
      on_trigger: 'disable',
      channels: ['email'],
    })
    expect(b.value).toBeGreaterThan(0)
    // Back on the manage view once saved.
    await waitFor(() => expect(getByText('ORCL · Oracle Corporation')).toBeTruthy())
  })

  it('a target already past the live price is flagged before it is created', () => {
    const { getAllByText, getByLabelText, getByText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    fireEvent.click(getAllByText('New alert')[0]!)
    fireEvent.click(getByLabelText('Rises above'))
    fireEvent.change(getByLabelText(/Target price/), { target: { value: '150' } })
    expect(getByText('ORCL is already above this level — it would alert right away')).toBeTruthy()
  })

  it('% move criteria posts kind=move with the percent as the value', async () => {
    const { getAllByText, getByText, getByLabelText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    fireEvent.click(getAllByText('New alert')[0]!)
    fireEvent.click(getByText('% move'))
    fireEvent.click(getByLabelText('Falls by'))
    fireEvent.change(getByLabelText('Movement amount (%)'), { target: { value: '7.5' } })
    expect(getByText(/a 7.50% fall in ORCL/)).toBeTruthy()
    fireEvent.click(getByText('Create alert'))
    await waitFor(() => expect(calls('POST')).toHaveLength(1))
    expect(body(calls('POST')[0]!)).toMatchObject({ kind: 'move', op: '<=', value: 7.5 })
  })

  it('editing a row PATCHes that alert only, with the Active switch and the ticker locked', async () => {
    const { getByText, getByLabelText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    fireEvent.click(getByText('Good entry zone'))
    expect(getByText('Edit alert · ORCL')).toBeTruthy()
    expect((getByLabelText('Ticker') as HTMLSelectElement).disabled).toBe(true)
    expect(getByText('Active')).toBeTruthy()
    fireEvent.click(getByLabelText('Alert on'))
    expect(getByText('Paused')).toBeTruthy()
    fireEvent.change(getByLabelText(/Target price/), { target: { value: '180' } })
    fireEvent.click(getByText('Save alert'))
    await waitFor(() => expect(calls('PATCH')).toHaveLength(1))
    expect(calls('PATCH')[0]![0]).toBe('/api/alerts/o2')
    expect(body(calls('PATCH')[0]!)).toMatchObject({
      value: 180,
      enabled: false,
      name: 'Good entry zone',
      op: '<=',
    })
    expect(calls('POST')).toHaveLength(0)
    expect(calls('DELETE')).toHaveLength(0)
  })

  it('Delete alert DELETEs that alert and returns to the manage view', async () => {
    const { getByText } = render(<PriceAlertsModal {...props({ price_alerts: TWO })} />)
    fireEvent.click(getByText('Good entry zone'))
    fireEvent.click(getByText('Delete alert'))
    await waitFor(() => expect(calls('DELETE')).toHaveLength(1))
    expect(calls('DELETE')[0]![0]).toBe('/api/alerts/o2')
    await waitFor(() => expect(getByText('ORCL · Oracle Corporation')).toBeTruthy())
  })

  it('a failed save keeps the form open and shows the error', async () => {
    mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) =>
      init?.method === 'PATCH'
        ? { ok: false, status: 500, json: async () => ({}) }
        : ok(url.includes('/api/alerts') ? { alerts: [] } : { stats: {} }),
    )
    const { getByText } = render(<PriceAlertsModal {...props({ price_alerts: TWO })} />)
    fireEvent.click(getByText('Good entry zone'))
    fireEvent.click(getByText('Save alert'))
    await waitFor(() => expect(getByText('Could not save — please try again.')).toBeTruthy())
    expect(getByText('Edit alert · ORCL')).toBeTruthy()
  })

  it('Cancel returns to the manage view without a write', () => {
    const { getByText, getAllByText } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO })} />,
    )
    fireEvent.click(getAllByText('New alert')[0]!)
    fireEvent.click(getByText('Cancel'))
    expect(getByText('ORCL · Oracle Corporation')).toBeTruthy()
    expect(calls('POST')).toHaveLength(0)
  })
})

describe('PriceAlertsModal — footer', () => {
  it('the back action closes the modal with the caller-supplied label', () => {
    const p = props({ price_alerts: TWO })
    const { getByText } = render(<PriceAlertsModal {...p} />)
    fireEvent.click(getByText('Back to watchlist'))
    expect(p.onClose).toHaveBeenCalled()
  })

  it('Remove from watchlist is offered only when the caller allows it', () => {
    const onRemove = vi.fn()
    const { getByText, queryByText, rerender } = render(
      <PriceAlertsModal {...props({ price_alerts: TWO }, { canRemove: true, onRemove })} />,
    )
    fireEvent.click(getByText('Remove from watchlist'))
    expect(onRemove).toHaveBeenCalled()
    rerender(<PriceAlertsModal {...props({ price_alerts: TWO })} />)
    expect(queryByText('Remove from watchlist')).toBeNull()
    within(document.body)
  })
})
