import type { Citation } from '../../types/assetDetail'

// Relative time for a published_at ISO string — compact form for chip label.
function chipDate(iso: string | undefined): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    const s = Math.floor((Date.now() - d.getTime()) / 1000)
    if (s < 60) return 'just now'
    const m = Math.floor(s / 60)
    if (m < 60) return `${m}m`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h`
    const days = Math.floor(h / 24)
    if (days === 1) return 'yesterday'
    if (days < 7) return `${days}d`
    if (days < 31) return `${Math.floor(days / 7)}w`
    return `${Math.floor(days / 30)}mo`
  } catch {
    return ''
  }
}

// Display title: prefer the citation's title (truncated), else domain, else the
// bare hostname extracted from the url. Falls back to the raw url.
function chipTitle(source: Citation): string {
  const raw = source.title || source.domain || ''
  if (!raw && source.url) {
    try {
      return new URL(source.url).hostname.replace(/^www\./, '')
    } catch {
      return source.url
    }
  }
  return raw.length > 18 ? raw.slice(0, 17) + '…' : raw
}

function faviconDomain(source: Citation): string {
  if (source.domain) return source.domain
  try {
    return new URL(source.url).hostname
  } catch {
    return ''
  }
}

interface Props {
  source: Citation
}

export function CitationChip({ source }: Props) {
  const { url, published_at, quote } = source
  const title = chipTitle(source)
  const date = chipDate(published_at)
  const domain = faviconDomain(source)
  const faviconSrc = domain
    ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`
    : ''

  // Invalid / empty / non-http(s) url → render a non-link span so no href is set (a javascript:
  // or data: scheme must never become a live href — XSS).
  let isValidUrl = false
  try {
    if (url) {
      isValidUrl = ['http:', 'https:'].includes(new URL(url).protocol)
    }
  } catch {
    /* invalid */
  }

  const inner = (
    <>
      {faviconSrc && (
        <img
          loading="lazy"
          className="citation-chip__favicon"
          src={faviconSrc}
          alt=""
          aria-hidden="true"
          onError={(e) => {
            ;(e.currentTarget as HTMLImageElement).style.display = 'none'
          }}
        />
      )}
      <span className="citation-chip__title">{title}</span>
      {date && <span className="citation-chip__date">{date}</span>}
      {quote && (
        <span className="citation-chip__popover">
          {quote.length > 180 ? quote.slice(0, 179) + '…' : quote}
        </span>
      )}
    </>
  )

  if (isValidUrl) {
    return (
      <a href={url} target="_blank" rel="noopener noreferrer" className="citation-chip">
        {inner}
      </a>
    )
  }

  return <span className="citation-chip citation-chip--no-link">{inner}</span>
}
