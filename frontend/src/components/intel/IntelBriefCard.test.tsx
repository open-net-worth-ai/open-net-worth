// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { IntelDigest } from '../../types/intelDigest'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

import { IntelBriefCard } from './IntelBriefCard'
import { _clearDigestCache } from '../../hooks/useIntelDigest'
import { clearQueryCache } from '../../state/queryCache'

// ── Fixtures ──────────────────────────────────────────────────────────────────

const DIGEST: IntelDigest = {
  generated_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
  base_currency: 'SGD',
  period: '1M',
  market_backdrop: {
    benchmark: {
      symbol: 'SPY',
      instrument_id: 'inst-spy',
      twr: { period_return: 0.024, annualized: 0.3 },
      data_quality: { market_since: '2023-01-01', value_points: 30, sufficient: true },
    },
    crypto_benchmark: null,
  },
  return_drivers: [],
  top_movers: [
    {
      instrument_id: 'inst-nvda',
      label: 'Nvidia',
      ticker: 'NVDA',
      day_change_base: 850,
      // day_change_pct is a PERCENTAGE (positions.py computes (price/prev-1)*100),
      // so +5.0 means +5.00% — it must NOT be re-scaled by fraction:true.
      day_change_pct: 5.0,
    },
  ],
  narration: null,
  data_quality: {
    has_price_history: true,
    rows_count: 10,
    rows_with_day_change: 8,
    benchmark_sufficient: true,
    period: '1M',
  },
  today: { pl_base: 2410, pl_pct: 0.0076, has_data: true },
  portfolio_return: { period_return: 0.042 },
}

function makeFetch(data: IntelDigest) {
  return () => Promise.resolve({ ok: true, json: () => Promise.resolve(data) })
}

function noop() {}

function renderCard(onChatSuggest: (t: string) => void = noop) {
  return render(
    <IntelBriefCard onChatSuggest={onChatSuggest}>
      <div>desk</div>
    </IntelBriefCard>,
  )
}

afterEach(() => {
  cleanup()
  _clearDigestCache()
  vi.clearAllMocks()
})
beforeEach(() => {
  _clearDigestCache()
})

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('IntelBriefCard — header + brief', () => {
  it('does not call an empty portfolio steady', async () => {
    mockAuthFetch.mockImplementation(
      makeFetch({
        ...DIGEST,
        today: { pl_base: 0, pl_pct: 0, has_data: false },
        data_quality: { ...DIGEST.data_quality, rows_count: 0, rows_with_day_change: 0 },
        portfolio_return: null,
        top_movers: [],
        narration: null,
      }),
    )
    const { findByText, queryByText, getByText } = renderCard()
    expect(await findByText(/^No portfolio activity yet/)).toBeTruthy()
    expect(queryByText(/steady today/)).toBeNull()
    expect(getByText('desk')).toBeTruthy()
  })

  it('renders the Holdings brief header and a text-only brief with today’s P&L', async () => {
    mockAuthFetch.mockImplementation(makeFetch(DIGEST))
    const { findByText, container } = renderCard()
    expect(await findByText('Holdings brief')).toBeTruthy()
    await findByText(/Your book is/)
    const text = container.querySelector('.intel-brief-narrative')?.textContent ?? ''
    expect(text).toMatch(/\+S\$2,410/)
    expect(text).toMatch(/NVDA led/)
    expect(text).toMatch(/ahead of SPY 1M/)
  })

  it('leaves the decision count to the desk below', async () => {
    mockAuthFetch.mockImplementation(makeFetch(DIGEST))
    const { findByText, container } = renderCard()
    await findByText(/Your book is/)
    expect(container.querySelector('.intel-brief-narrative')?.textContent).not.toMatch(
      /needs? a look|Nothing needs a decision/,
    )
  })

  it('below level 5 the brief is the template, never a narration', async () => {
    // Lucius's narrative has its own request at level 5 (see "verbosity" below); a stray
    // narration on the plain digest must not replace the chosen level's text.
    mockAuthFetch.mockImplementation(makeFetch({ ...DIGEST, narration: 'A calm day for you.' }))
    const { container, queryByText } = renderCard()
    await waitFor(() => expect(container.textContent).toMatch(/You're .* pts ahead of SPY/))
    expect(queryByText('A calm day for you.')).toBeNull()
  })

  it('asks Lucius about the brief', async () => {
    mockAuthFetch.mockImplementation(makeFetch(DIGEST))
    const onChatSuggest = vi.fn()
    const { findByRole } = renderCard(onChatSuggest)
    fireEvent.click(await findByRole('button', { name: 'Ask Lucius about this brief' }))
    expect(onChatSuggest).toHaveBeenCalledWith(expect.stringMatching(/portfolio brief/))
  })
})

describe('IntelBriefCard — the holdings never wait on the brief', () => {
  it('shows the desk while the digest is still loading', () => {
    mockAuthFetch.mockImplementation(() => new Promise(() => {}))
    const { getByText, container } = renderCard()
    expect(getByText('desk')).toBeTruthy()
    expect(container.querySelector('.intel-brief-narrative')).toBeNull()
  })

  it('keeps the desk and offers a retry when the digest fails', async () => {
    mockAuthFetch.mockImplementation(() => Promise.resolve({ ok: false }))
    const { findByText, getByText, getByRole } = renderCard()
    expect(await findByText(/brief unavailable/i)).toBeTruthy()
    expect(getByText('desk')).toBeTruthy()
    expect(getByText('Holdings brief')).toBeTruthy()
    mockAuthFetch.mockImplementation(makeFetch(DIGEST))
    fireEvent.click(getByRole('button', { name: 'Retry' }))
    expect(await findByText(/Your book is/)).toBeTruthy()
  })

  it('does not crash when the benchmark payload has a null twr (short history)', async () => {
    const noTwr: IntelDigest = {
      ...DIGEST,
      market_backdrop: {
        benchmark: { ...DIGEST.market_backdrop.benchmark!, twr: null },
        crypto_benchmark: null,
      },
    }
    mockAuthFetch.mockImplementation(makeFetch(noTwr))
    const { findByText, queryByText } = renderCard()
    expect(await findByText(/Your book is/)).toBeTruthy()
    expect(queryByText(/ahead of SPY|behind SPY/)).toBeNull()
  })
})

describe('IntelBriefCard — verbosity', () => {
  afterEach(() => clearQueryCache())

  function byUrl(narrated: Promise<unknown>) {
    mockAuthFetch.mockImplementation((url: string) =>
      url.includes('style=brief')
        ? narrated
        : Promise.resolve({ ok: true, json: () => Promise.resolve(DIGEST) }),
    )
  }

  it('level 1 shows the headline only and never asks for a narrative', async () => {
    byUrl(new Promise(() => {}))
    const { findByText } = render(
      <IntelBriefCard onChatSuggest={noop} verbosity={1}>
        <div>desk</div>
      </IntelBriefCard>,
    )
    expect(await findByText(/^Your book is .* today\.$/)).toBeTruthy()
    expect(mockAuthFetch.mock.calls.some(([u]) => String(u).includes('style=brief'))).toBe(false)
  })

  it('level 5 shows the level-4 template while Lucius writes, then the narrative and its lean', async () => {
    let resolve: (v: unknown) => void = () => {}
    byUrl(new Promise((r) => (resolve = r)))
    const { findByText, queryByText } = render(
      <IntelBriefCard onChatSuggest={noop} verbosity={5}>
        <div>desk</div>
      </IntelBriefCard>,
    )
    expect(await findByText(/Top movers: NVDA/)).toBeTruthy()
    resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          ...DIGEST,
          narration: 'NVDA carried the book today.',
          narration_opinion: { lean: 'bullish', lean_reason: 'Momentum.' },
        }),
    })
    expect(await findByText('NVDA carried the book today.')).toBeTruthy()
    expect(await findByText('Leans bullish')).toBeTruthy()
    expect(queryByText(/Top movers: NVDA/)).toBeNull()
  })

  it('level 5 keeps the template when the narrative can’t be written', async () => {
    byUrl(
      Promise.resolve({ ok: true, json: () => Promise.resolve({ ...DIGEST, narration: null }) }),
    )
    const { findByText } = render(
      <IntelBriefCard onChatSuggest={noop} verbosity={5}>
        <div>desk</div>
      </IntelBriefCard>,
    )
    expect(await findByText(/Top movers: NVDA/)).toBeTruthy()
  })
})
