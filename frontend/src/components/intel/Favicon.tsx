import { useState } from 'react'
import { faviconUrl } from '../../utils/format'

interface Props {
  url: string
  domain?: string
  // Sizing class (e.g. "asd-news-favicon" / "asd-dev-favicon"). The fallback box shares it, so a
  // missing favicon occupies the exact same footprint — rows/cards never shift.
  className?: string
}

// Publisher favicon with a consistent-size fallback: when the favicon is absent or fails to load,
// render a same-size blue box instead of collapsing, so every row/card keeps an aligned leading icon.
export function Favicon({ url, domain, className = '' }: Props) {
  const src = faviconUrl(url, domain)
  // Track the src that errored (not a bare boolean): when the item — and thus src — changes, the
  // failure no longer matches, so a reused component instance retries the new favicon (list-safe,
  // no key/effect needed).
  const [failedSrc, setFailedSrc] = useState<string | null>(null)

  if (!src || failedSrc === src) {
    return <span className={`${className} favicon-fallback`} aria-hidden="true" />
  }
  return (
    <img
      loading="lazy"
      src={src}
      alt=""
      className={className}
      aria-hidden="true"
      onError={() => setFailedSrc(src)}
    />
  )
}
