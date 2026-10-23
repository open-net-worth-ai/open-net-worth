// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { TopicDetailModal } from './TopicDetailModal'
import type { WatchlistItem } from './WatchlistsSection'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

function baseItem(over: Partial<WatchlistItem> = {}): WatchlistItem {
  return {
    id: 'wli-1',
    type: 'topic',
    value: 'Middle East tensions',
    label: 'Middle East tensions',
    asset_class: 'equity',
    monitor_news: true,
    added_by: 'user',
    note: '',
    watchlist_ids: ['default'],
    linked_tickers: [],
    custom_feed_urls: [],
    ...over,
  }
}

function props(over: Partial<WatchlistItem> = {}) {
  return {
    item: baseItem(over),
    onClose: vi.fn(),
    onSaved: vi.fn(),
    onDelete: vi.fn(),
  }
}

afterEach(cleanup)
beforeEach(() => mockAuthFetch.mockReset())

describe('TopicDetailModal — layout', () => {
  it('renders at the lg modal size', () => {
    render(<TopicDetailModal {...props()} />)
    expect(document.body.querySelector('.ui-modal-card--lg')).toBeTruthy()
  })

  it('the Advanced: news sources section is collapsed by default with no count badge', () => {
    const { getByText, queryByText, queryByPlaceholderText } = render(
      <TopicDetailModal {...props()} />,
    )
    expect(getByText('Advanced: news sources')).toBeTruthy()
    expect(queryByText('Linked tickers')).toBeFalsy()
    expect(queryByPlaceholderText('https://example.com/feed.xml')).toBeFalsy()
    expect(queryByText(/linked/, { selector: '.intel-td-adv-count' })).toBeFalsy()
  })

  it('shows a live count badge once tickers/feeds are populated', () => {
    const { getByText } = render(
      <TopicDetailModal
        {...props({
          linked_tickers: ['AAPL', 'MSFT'],
          custom_feed_urls: ['https://a.com/feed.xml'],
        })}
      />,
    )
    fireEvent.click(getByText('Advanced: news sources'))
    expect(getByText('2 linked • 1 feed')).toBeTruthy()
  })
})

describe('TopicDetailModal — Linked tickers', () => {
  it('adds a chip and caps at 10, swapping help to the muted max message', () => {
    const nine = Array.from({ length: 9 }, (_, i) => `T${i}`)
    const { getByText, getByPlaceholderText, getByLabelText } = render(
      <TopicDetailModal {...props({ linked_tickers: nine })} />,
    )
    fireEvent.click(getByText('Advanced: news sources'))
    expect(getByText(/Up to 10\./)).toBeTruthy()
    const input = getByLabelText('Linked tickers')
    fireEvent.change(input, { target: { value: 'T9' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(getByText('Maximum 10 tickers.')).toBeTruthy()
    // at cap — a further add is blocked, input keeps the typed text
    fireEvent.change(input, { target: { value: 'T10' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect((input as HTMLInputElement).value).toBe('T10')
    expect(() => getByPlaceholderText('nonexistent')).toThrow() // sanity: query helpers still work
  })
})

describe('TopicDetailModal — Custom news feeds', () => {
  it('shows the empty state, then adds/removes rows, capping at 5', () => {
    const { getByText, getAllByPlaceholderText, queryByText } = render(
      <TopicDetailModal {...props()} />,
    )
    fireEvent.click(getByText('Advanced: news sources'))
    expect(getByText('No custom feeds yet.')).toBeTruthy()
    for (let i = 0; i < 5; i++) fireEvent.click(getByText('Add feed URL'))
    expect(getAllByPlaceholderText('https://example.com/feed.xml')).toHaveLength(5)
    expect(queryByText('Add feed URL')).toBeFalsy()
    expect(getByText('Maximum 5 feeds.')).toBeTruthy()
  })
})

describe('TopicDetailModal — Save', () => {
  it('extends the single PATCH body with both arrays (blanks filtered from feeds)', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    const p = props({ linked_tickers: ['AAPL'], custom_feed_urls: ['https://a.com/feed.xml'] })
    const { getByText } = render(<TopicDetailModal {...p} />)
    fireEvent.click(getByText('Advanced: news sources'))
    fireEvent.click(getByText('Add feed URL')) // an extra blank row must be filtered out
    fireEvent.click(getByText('Save'))
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalledTimes(1))
    const [url, opts] = mockAuthFetch.mock.calls[0]!
    expect(url).toBe('/api/watchlist/items/wli-1')
    expect(opts.method).toBe('PATCH')
    const body = JSON.parse(opts.body)
    expect(body.linked_tickers).toEqual(['AAPL'])
    expect(body.custom_feed_urls).toEqual(['https://a.com/feed.xml'])
    await waitFor(() => expect(p.onSaved).toHaveBeenCalled())
  })

  it('a 422 on linked_tickers force-opens the section, shows the field error inline, and keeps the modal open', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: 'linked_tickers: at most 10 per item (got 11)' }),
    })
    const p = props()
    const { getByText, queryByText } = render(<TopicDetailModal {...p} />)
    fireEvent.click(getByText('Save'))
    await waitFor(() =>
      expect(getByText('linked_tickers: at most 10 per item (got 11)')).toBeTruthy(),
    )
    expect(getByText('Linked tickers')).toBeTruthy() // section force-opened
    expect(p.onClose).not.toHaveBeenCalled()
    expect(queryByText('Could not save — please try again.')).toBeFalsy()
  })

  it('a 422 on custom_feed_urls shows the verbatim message and highlights the offending row', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({
        detail: "custom_feed_urls: 'ftp://bad.example.com' is not a safe http(s) URL (scheme)",
      }),
    })
    const p = props({ custom_feed_urls: ['ftp://bad.example.com'] })
    const { getByText, getByDisplayValue } = render(<TopicDetailModal {...p} />)
    fireEvent.click(getByText('Save'))
    await waitFor(() =>
      expect(
        getByText("custom_feed_urls: 'ftp://bad.example.com' is not a safe http(s) URL (scheme)"),
      ).toBeTruthy(),
    )
    const row = getByDisplayValue('ftp://bad.example.com') as HTMLInputElement
    expect(row.getAttribute('aria-invalid')).toBe('true')
  })

  it('a non-field failure (500) shows the generic save-failed banner', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    const p = props()
    const { getByText } = render(<TopicDetailModal {...p} />)
    fireEvent.click(getByText('Save'))
    await waitFor(() => expect(getByText('Could not save — please try again.')).toBeTruthy())
    expect(p.onClose).not.toHaveBeenCalled()
  })
})
