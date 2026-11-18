/**
 * Copy / download a visual as an image (the visual's ⋯ menu). Standard blocks are captured
 * from the page with `html-to-image`. A drawing lives in a sandboxed frame the chat cannot
 * read, so the frame exports itself: the chat posts it the same library (its browser build,
 * as text) and gets a data URL back. A drawing that is one SVG downloads as that SVG.
 */
import { toPng, toSvg } from 'html-to-image'
import libSource from 'html-to-image/dist/html-to-image.js?raw'
import { MSG_EXPORT, MSG_EXPORTED } from './drawingFrame'

export type ImageFormat = 'png' | 'svg'

/** The menu itself is not part of the picture. */
const notMenu = (node: HTMLElement) => !node.classList?.contains('ivis-menu')

function surfaceOf(el: HTMLElement): string {
  const own = getComputedStyle(el).backgroundColor
  if (own && own !== 'rgba(0, 0, 0, 0)' && own !== 'transparent') return own
  return getComputedStyle(document.body).backgroundColor || '#ffffff'
}

/** A standard visual (tiles, charts, timeline) as a PNG or SVG data URL. */
export function exportBlocks(el: HTMLElement, format: ImageFormat): Promise<string> {
  const opts = { filter: notMenu, backgroundColor: surfaceOf(el), pixelRatio: 2, cacheBust: true }
  return format === 'png' ? toPng(el, opts) : toSvg(el, opts)
}

let nextId = 0

/** A drawing, exported by its own frame. Rejects after 15s if the frame doesn't answer. */
export function exportDrawing(frame: HTMLIFrameElement, format: ImageFormat): Promise<string> {
  const win = frame.contentWindow
  if (!win) return Promise.reject(new Error('the drawing is not loaded yet'))
  const id = `x${++nextId}`
  return new Promise((resolve, reject) => {
    const done = (fn: () => void) => {
      window.removeEventListener('message', onMessage)
      clearTimeout(timer)
      fn()
    }
    const onMessage = (e: MessageEvent) => {
      const d = e.data as { type?: string; id?: string; dataUrl?: string; error?: string } | null
      if (e.source !== win || d?.type !== MSG_EXPORTED || d.id !== id) return
      if (d.dataUrl) done(() => resolve(d.dataUrl as string))
      else done(() => reject(new Error(d.error || 'export failed')))
    }
    const timer = setTimeout(() => done(() => reject(new Error('export timed out'))), 15000)
    window.addEventListener('message', onMessage)
    win.postMessage({ type: MSG_EXPORT, id, format, lib: libSource, bg: surfaceOf(frame) }, '*')
  })
}

/** Decoded in code, not `fetch(dataUrl)`: the app's CSP (`connect-src 'self'`) blocks fetching a
 *  data: URL, which made "Copy as image" fail on a served build. */
export function toBlob(dataUrl: string): Blob {
  const comma = dataUrl.indexOf(',')
  const head = dataUrl.slice(5, comma)
  const body = dataUrl.slice(comma + 1)
  const type = head.split(';')[0] || 'application/octet-stream'
  if (!head.includes(';base64')) return new Blob([decodeURIComponent(body)], { type })
  const bin = atob(body)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new Blob([bytes], { type })
}

/** Copy a PNG to the clipboard. The promise goes INTO the ClipboardItem so the browser keeps
 *  the click's permission while the image is still being made. */
export async function copyImage(png: () => Promise<string>): Promise<void> {
  const item = new ClipboardItem({ 'image/png': png().then(toBlob) })
  await navigator.clipboard.write([item])
}

export function downloadImage(dataUrl: string, name: string, format: ImageFormat): void {
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = `${name}.${format}`
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/** A file name from the visual's title: "Spending by category" → "spending-by-category". */
export function fileName(title: string | null | undefined): string {
  const slug = (title || 'chart')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return slug.slice(0, 60) || 'chart'
}
