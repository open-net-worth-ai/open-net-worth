// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { NewsCardGrid } from './NewsCardGrid'
import type { NewsItem } from '../../types/assetDetail'

afterEach(cleanup)

function items(n: number): NewsItem[] {
  return Array.from({ length: n }, (_, i) => ({
    title: `Story ${i + 1}`,
    source: 'Src',
    url: `https://e.com/${i + 1}`,
    published_at: '2026-06-19',
  })) as NewsItem[]
}

describe('NewsCardGrid', () => {
  it('caps at 6 cards by default', () => {
    const { container } = render(<NewsCardGrid items={items(12)} />)
    expect(container.querySelectorAll('.asd-news-card')).toHaveLength(6)
    expect(screen.getByText('Story 6')).toBeTruthy()
    expect(screen.queryByText('Story 7')).toBeNull()
  })

  it('honours an explicit limit', () => {
    const { container } = render(<NewsCardGrid items={items(12)} limit={4} />)
    expect(container.querySelectorAll('.asd-news-card')).toHaveLength(4)
  })

  it('shows all when fewer than the limit', () => {
    const { container } = render(<NewsCardGrid items={items(3)} />)
    expect(container.querySelectorAll('.asd-news-card')).toHaveLength(3)
  })

  it('renders nothing when there are no items', () => {
    const { container } = render(<NewsCardGrid items={[]} />)
    expect(container.querySelector('.asd-news-grid')).toBeNull()
  })

  it('uses the publisher domain for the favicon, not the Google News wrapper host', () => {
    const item = {
      title: 'AMD news - Benzinga',
      source: 'Benzinga',
      url: 'https://news.google.com/rss/articles/CBMiABC',
      domain: 'benzinga.com',
      published_at: '2026-06-19',
      image: null,
    } as NewsItem
    const { container } = render(<NewsCardGrid items={[item]} />)
    const favicon = container.querySelector('.asd-news-favicon') as HTMLImageElement
    expect(favicon).toBeTruthy()
    // Routes through our own proxy (ADR 0025), keyed by the publisher domain (not news.google.com).
    expect(favicon.getAttribute('src')).toBe('/api/favicon?domain=benzinga.com')
  })

  it('falls back to the article host for the favicon when no domain is given', () => {
    const item = {
      title: 'Direct article',
      source: 'Reuters',
      url: 'https://www.reuters.com/x',
      published_at: '2026-06-19',
      image: null,
    } as NewsItem
    const { container } = render(<NewsCardGrid items={[item]} />)
    const favicon = container.querySelector('.asd-news-favicon') as HTMLImageElement
    expect(favicon.getAttribute('src')).toBe('/api/favicon?domain=www.reuters.com')
  })

  // Owner ask 2026-08-31: title only, never a subtitle/snippet — superseded the earlier
  // 2026-08-30 "show the raw source snippet" decision this test used to cover.
  it('never renders a summary paragraph, even when snippet is present', () => {
    const item = {
      title: 'Kestrel Dash raises guidance',
      source: 'Reuters',
      url: 'https://e.com/1',
      published_at: '2026-06-19',
      snippet: 'The company reported record revenue, beating consensus by 12%...',
    } as NewsItem
    const { container } = render(<NewsCardGrid items={[item]} />)
    expect(
      screen.queryByText('The company reported record revenue, beating consensus by 12%...'),
    ).toBeNull()
    expect(container.querySelector('.asd-news-card-summary')).toBeNull()
  })

  it('renders no summary paragraph when snippet is absent', () => {
    const { container } = render(<NewsCardGrid items={items(1)} />)
    expect(container.querySelector('.asd-news-card-summary')).toBeNull()
  })

  it('an article without an image gets a favicon tile in place of the picture (phone carousel)', () => {
    // Owner ask 2026-09-22: Perplexity-style story cards on a phone — picture on top, title,
    // time, source. Most RSS rows arrive without a picture (og:image is harvested later by the
    // brief's full-text pass); until then the card shows a tile with the publisher's favicon so
    // the carousel keeps its shape instead of collapsing to text-only cards.
    const withImage = {
      title: 'A',
      source: 'Benzinga',
      url: 'https://benzinga.com/a',
      published_at: '2026-09-21',
      image: 'https://cdn.benzinga.com/a.jpg',
    } as NewsItem
    const without = {
      title: 'B',
      source: 'TradingKey',
      url: 'https://tradingkey.com/b',
      published_at: '2026-09-20',
      image: null,
    } as NewsItem
    const { container } = render(<NewsCardGrid items={[withImage, without]} />)
    const cards = container.querySelectorAll('.asd-news-card')
    const [imageCard, plainCard] = cards
    if (!imageCard || !plainCard) throw new Error(`expected 2 news cards, got ${cards.length}`)
    expect(imageCard.querySelector('.asd-news-thumb img')?.getAttribute('src')).toBe(
      'https://cdn.benzinga.com/a.jpg',
    )
    expect(imageCard.querySelector('.asd-news-thumb--placeholder')).toBeNull()
    const tile = plainCard.querySelector('.asd-news-thumb--placeholder')
    expect(tile).not.toBeNull()
    expect(tile?.querySelector('.asd-news-favicon')).not.toBeNull()
  })

  it('an image that fails to load falls back to the favicon tile, not an empty slot', () => {
    // Owner-reproduced 2026-10-28: a broken og:image hid the whole picture slot, so on a phone
    // that card's title jumped to the top while its neighbours kept a picture on top.
    const broken = {
      title: 'Broken picture',
      source: 'Yahoo',
      url: 'https://finance.yahoo.com/x',
      published_at: '2026-10-28',
      image: 'https://cdn.example.com/gone.jpg',
    } as NewsItem
    const { container } = render(<NewsCardGrid items={[broken]} />)
    const img = container.querySelector('.asd-news-thumb img') as HTMLImageElement
    fireEvent.error(img)
    expect(
      container.querySelector('.asd-news-thumb img[src="https://cdn.example.com/gone.jpg"]'),
    ).toBeNull()
    const tile = container.querySelector('.asd-news-thumb--placeholder')
    expect(tile).not.toBeNull()
    expect(tile?.querySelector('.asd-news-favicon')).not.toBeNull()
  })
})
