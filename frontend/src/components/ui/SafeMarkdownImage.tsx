import type { ImgHTMLAttributes } from 'react'
import { remoteImageHost } from '../../utils/remoteImage'

// Markdown from a model, a skill file or an imported note can carry `![x](https://host/?d=…)`,
// and a browser fetches that URL the moment the image mounts — an exfiltration channel that
// needs no click. Only an image that cannot leave this origin is rendered: same-origin, blob:
// or data:. Anything else becomes a link showing the host; the page's CSP (img-src) blocks a
// remote image outright, so opening it is a deliberate navigation, never an embedded load.

export function SafeMarkdownImage({ src, alt }: ImgHTMLAttributes<HTMLImageElement>) {
  const host = remoteImageHost(src)
  if (host === null) return <img loading="lazy" src={src} alt={alt ?? ''} />
  const openable = typeof src === 'string' && /^https?:\/\//i.test(src)
  const label = `Remote image blocked (${host})`
  return openable ? (
    <a
      className="md-image-blocked"
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      title={alt || undefined}
    >
      {label} · open
    </a>
  ) : (
    <span className="md-image-blocked">{label}</span>
  )
}
