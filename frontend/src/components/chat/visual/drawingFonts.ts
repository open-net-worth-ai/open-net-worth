/**
 * The app's own font (Plus Jakarta Sans, the four weights `main.tsx` loads) for the drawing
 * frame. The frame is a separate, sandboxed document: naming the font was not enough — it had
 * no files, so every drawing fell back to whatever the system had, and two drawings could show
 * two different fonts and boldness. The files are packed in as data URLs (the frame's opaque
 * origin cannot fetch the app's own assets), fetched once and shared by every drawing.
 */
import w400 from '@fontsource/plus-jakarta-sans/files/plus-jakarta-sans-latin-400-normal.woff2?url'
import w500 from '@fontsource/plus-jakarta-sans/files/plus-jakarta-sans-latin-500-normal.woff2?url'
import w600 from '@fontsource/plus-jakarta-sans/files/plus-jakarta-sans-latin-600-normal.woff2?url'
import w700 from '@fontsource/plus-jakarta-sans/files/plus-jakarta-sans-latin-700-normal.woff2?url'

export const FRAME_FONT = 'Plus Jakarta Sans'

const FILES: [number, string][] = [
  [400, w400],
  [500, w500],
  [600, w600],
  [700, w700],
]

const asDataUrl = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })

async function face([weight, url]: [number, string]): Promise<string> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`font ${weight}: ${res.status}`)
  const data = await asDataUrl(await res.blob())
  return `@font-face{font-family:'${FRAME_FONT}';font-style:normal;font-weight:${weight};font-display:block;src:url(${data}) format('woff2')}`
}

let pending: Promise<string> | null = null

/** The `@font-face` rules for the frame; `''` if a file can't be fetched (the drawing still
 *  renders, in the fallback font). Fetched once per page. */
export function loadFrameFonts(): Promise<string> {
  pending ??= Promise.all(FILES.map(face))
    .then((rules) => rules.join(''))
    .catch(() => '')
  return pending
}
