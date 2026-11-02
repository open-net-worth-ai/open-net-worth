// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../../../state/queryCache'
import { IntelSection } from './IntelSection'

vi.mock('../../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const CONFIG = {
  price_check_interval_seconds: 900,
  news_scan_interval_seconds: 43200,
  enabled: true,
  notify_routes: { price_alerts: [], watch_alerts: ['telegram'], news: [] },
  search_budget: 15,
  web_search_enabled: true,
  news_freshness: 'fast',
  outlet_feeds_enabled: true,
  crash_thresholds: { equity: 12, commodity: 15, crypto: 25 },
  spike_min_abs: 5,
  spike_factor: 3,
  explained_alerts_enabled: true,
  watch_reads_enabled: true,
  watch_reads_per_day: 20,
  ai_opinion_level: 'information',
  holdings_brief_verbosity: 3,
  watchlist_brief_verbosity: 3,
}

let patches: Record<string, unknown>[] = []
let failPatch = false
let deliveryOn = false

function respond(url: string, init?: { method?: string; body?: string }) {
  if (init?.method === 'PATCH') {
    const body = JSON.parse(init.body ?? '{}')
    patches.push(body)
    return Promise.resolve({
      ok: !failPatch,
      json: async () => ({ ...CONFIG, ...body }),
    })
  }
  if (url === '/api/feature-flags')
    return Promise.resolve({
      ok: true,
      json: async () => ({
        flags: [{ name: 'intel_delivery_decider', enabled: deliveryOn }],
        locked: false,
      }),
    })
  if (url === '/api/alert-channels')
    return Promise.resolve({
      ok: true,
      json: async () => ({
        channels: [
          { id: 'telegram', label: 'Telegram', available: true },
          { id: 'email', label: 'Email', available: false },
        ],
      }),
    })
  return Promise.resolve({
    ok: true,
    json: async () => ({ items: [], watchlists: [], config: CONFIG }),
  })
}

const toast = vi.fn()

beforeEach(() => {
  clearQueryCache()
  patches = []
  failPatch = false
  deliveryOn = false
  toast.mockReset()
  mockAuthFetch.mockImplementation(respond)
})
afterEach(cleanup)

async function mount() {
  render(<IntelSection toast={toast} />)
  await screen.findByText('Monitoring')
}

describe('Settings › Intelligence', () => {
  it('holds every monitoring setting that used to live on the Intel tab', async () => {
    await mount()
    for (const label of [
      'Monitoring active',
      'Price check',
      'News scan interval',
      'RSS freshness',
      'Outlet headlines',
      'Web search',
    ])
      expect(screen.getAllByLabelText(label).length).toBeGreaterThan(0)
    expect(await screen.findByLabelText('Watch alerts via Telegram')).toBeTruthy()
    expect((screen.getByLabelText('Watch alerts via Telegram') as HTMLInputElement).checked).toBe(
      true,
    )
    expect((screen.getByLabelText('News analyses via Email') as HTMLInputElement).disabled).toBe(
      true,
    )
    // The scheduled daily digest is gone — a Daily briefing Automation does that job.
    expect(screen.queryByLabelText('End-of-day digest')).toBeNull()
    expect(screen.queryByText('Daily digests')).toBeNull()
    expect((screen.getByLabelText('Price alerts in-app') as HTMLInputElement).checked).toBe(true)
  })

  it('saves a cadence change with the same key and value as before', async () => {
    await mount()
    fireEvent.change(screen.getByLabelText('Price check'), { target: { value: '300' } })
    await waitFor(() => expect(patches).toEqual([{ price_check_interval_seconds: 300 }]))
    expect(toast).toHaveBeenCalledWith('Price check updated')
  })

  it('clamps the search budget on blur and ignores junk', async () => {
    await mount()
    const input = document.getElementById('search-budget') as HTMLInputElement
    fireEvent.change(input, { target: { value: '99' } })
    fireEvent.blur(input)
    await waitFor(() => expect(patches).toEqual([{ search_budget: 50 }]))
    fireEvent.change(input, { target: { value: 'abc' } })
    fireEvent.blur(input)
    expect(patches).toHaveLength(1)
  })

  it('turns a channel on for one kind of update, leaving the others as they were', async () => {
    await mount()
    fireEvent.click(await screen.findByLabelText('News analyses via Telegram'))
    await waitFor(() =>
      expect(patches).toEqual([
        {
          notify_routes: {
            price_alerts: [],
            watch_alerts: ['telegram'],
            news: ['telegram'],
          },
        },
      ]),
    )
  })

  it('saves the AI analysis toggles, and disables reads per day while reads are off', async () => {
    await mount()
    fireEvent.click(screen.getByLabelText('Explained alerts'))
    fireEvent.click(screen.getByLabelText("Lucius's read on watch stories"))
    await waitFor(() =>
      expect(patches).toEqual([
        { explained_alerts_enabled: false },
        { watch_reads_enabled: false },
      ]),
    )
    await waitFor(() =>
      expect((document.getElementById('watch-reads-per-day') as HTMLInputElement).disabled).toBe(
        true,
      ),
    )
  })

  it('saves the opinion level and explains it, with the not-advice note', async () => {
    await mount()
    fireEvent.change(screen.getByLabelText("How far Lucius's opinion goes"), {
      target: { value: 'lean' },
    })
    await waitFor(() => expect(patches).toEqual([{ ai_opinion_level: 'lean' }]))
    expect(await screen.findByText(/overall lean — bullish, bearish or neutral/)).toBeTruthy()
    expect(screen.getByText(/not financial advice/)).toBeTruthy()
  })

  it('saves both brief verbosity levels', async () => {
    await mount()
    fireEvent.change(screen.getByLabelText('Holdings brief detail'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Watchlist brief detail'), { target: { value: '1' } })
    await waitFor(() =>
      expect(patches).toEqual([{ holdings_brief_verbosity: 5 }, { watchlist_brief_verbosity: 1 }]),
    )
  })

  it('rolls a failed save back and says so', async () => {
    failPatch = true
    await mount()
    const toggle = screen.getByLabelText('Monitoring active')
    fireEvent.click(toggle)
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Could not save — try again'))
    expect(toggle.getAttribute('aria-checked')).toBe('true')
  })

  it('keeps the thresholds folded away until asked', async () => {
    await mount()
    expect(document.getElementById('crash-equity')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    const equity = document.getElementById('crash-equity') as HTMLInputElement
    fireEvent.change(equity, { target: { value: '150' } })
    fireEvent.blur(equity)
    await waitFor(() =>
      expect(patches).toEqual([{ crash_thresholds: { equity: 99, commodity: 15, crypto: 25 } }]),
    )
  })

  it('hides what Intel should tell me while the delivery experiment is off', async () => {
    await mount()
    await screen.findByText('AI analysis on watched tickers')
    expect(screen.queryByLabelText('What Intel should tell me')).toBeNull()
  })

  it("saves what Intel should tell me, in the user's own words, when the experiment is on", async () => {
    deliveryOn = true
    await mount()
    const box = await screen.findByLabelText('What Intel should tell me')
    fireEvent.change(box, { target: { value: '  No analyst upgrades.  ' } })
    fireEvent.blur(box)
    await waitFor(() =>
      expect(patches).toEqual([{ delivery_instructions: 'No analyst upgrades.' }]),
    )
    fireEvent.blur(box)
    expect(patches).toHaveLength(1)
  })
})
