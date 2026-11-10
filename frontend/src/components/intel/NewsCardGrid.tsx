import { useState } from 'react'
import { newsTime, safeHref } from '../../utils/format'
import type { NewsItem } from '../../types/assetDetail'
import { Favicon } from './Favicon'

interface Props {
  items: NewsItem[]
  // Max cards to render. Must match the LatestDevelopments `offset` at the call site
  // so an item never appears as both a card and a development row.
  limit?: number
}

// Perplexity-style cards. Desktop: raw article title + source·time on the left, a compact
// thumbnail on the right ONLY when the article has a real image (no empty placeholder box).
// Phone: a horizontal snap carousel — picture (or a favicon tile) on top, title, time, source
// (owner ask 2026-09-22) — the same markup, reflowed by the CSS below 640px.
export function NewsCardGrid({ items, limit = 6 }: Props) {
  const grid = items.slice(0, limit)
  if (!grid.length) return null
  return (
    <div className="asd-news-grid">
      {grid.map((item, i) => {
        const href = safeHref(item.url)
        const when = newsTime(item.published_at)
        const cardProps = href
          ? { href, target: '_blank' as const, rel: 'noopener noreferrer' }
          : {}
        const Tag = href ? 'a' : 'div'
        return (
          <Tag key={i} {...cardProps} className="asd-news-card">
            <div className="asd-news-card-body">
              <p className="asd-news-card-title">{item.title}</p>
              <div className="asd-news-card-meta">
                <Favicon url={item.url} domain={item.domain} className="asd-news-favicon" />
                <span className="asd-news-source">{item.source}</span>
                {when && (
                  <>
                    <span className="asd-news-dot" aria-hidden="true">
                      •
                    </span>
                    <span className="asd-news-time">{when}</span>
                  </>
                )}
              </div>
            </div>
            <NewsThumb key={item.image ?? ''} item={item} />
          </Tag>
        )
      })}
    </div>
  )
}

// A broken og:image falls back to the same favicon tile as no image at all — hiding the slot
// instead left that phone card text-only, its title jumping above its neighbours'.
function NewsThumb({ item }: { item: NewsItem }) {
  const [failed, setFailed] = useState(false)
  if (item.image && !failed) {
    return (
      <div className="asd-news-thumb">
        <img src={item.image} alt="" loading="lazy" onError={() => setFailed(true)} />
      </div>
    )
  }
  /* Phone carousel only (hidden on desktop, see the CSS): a favicon tile keeps the card's
     picture slot while the article has no og:image yet — the brief's full-text pass harvests
     one later (owner ask 2026-09-22, Perplexity-style). */
  return (
    <div className="asd-news-thumb asd-news-thumb--placeholder" aria-hidden="true">
      <Favicon url={item.url} domain={item.domain} className="asd-news-favicon" />
    </div>
  )
}
