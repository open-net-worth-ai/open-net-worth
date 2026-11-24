import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  buildDrawingDocument,
  FRAME_URL,
  MSG_DRAFT,
  MSG_HEIGHT,
  MSG_MEASURE,
  MSG_RENDER,
  MSG_THEME,
  readMode,
  readTheme,
} from './drawingFrame'
import { loadFrameFonts } from './drawingFonts'

// No `allow-same-origin`: the drawing's scripts get an opaque origin, never the app's session.
const SANDBOX = 'allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals'

/** A drawing Lucius wrote itself, run as written in a sandboxed frame. The frame is the loader
 *  at `FRAME_URL`, served with its own security policy — a `srcdoc` frame would inherit the
 *  app page's `script-src 'self'` and run no script at all, the drawing's or the height
 *  report's. Once the loader is up, the drawing's document is posted in; the frame then grows
 *  to the drawing's height and follows the app's light/dark theme.
 *
 *  `draft`: Lucius is still writing it. The frame gets an empty page, then the markup so far
 *  as it grows (no script runs); the finished drawing is written over it in the same frame,
 *  scripts and all, so it never jumps. */
export function VisualDrawing({
  code,
  title,
  draft = false,
}: {
  code: string
  title?: string | null
  draft?: boolean
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const renderedDoc = useRef<string | null>(null)
  const loaderReady = useRef(false)
  // Whether the height the frame reports may make it SHORTER. Not while the drawing is being
  // written or swapped in: writing the finished drawing over the draft first empties the page
  // (a 0 report), and its scripts and CDN libraries draw after that, so the frame used to drop
  // to its 160px default and grow back — the chart visibly collapsed and re-expanded (owner
  // report, 2026-11-24). Only the FINISHED drawing's own load lets it shrink to its true size.
  const settled = useRef(false)
  const [height, setHeight] = useState(0)
  // The app's font, packed for the frame; null until fetched (once per page).
  const [fontFaces, setFontFaces] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    void loadFrameFonts().then((css) => live && setFontFaces(css))
    return () => {
      live = false
    }
  }, [])
  // Built once per drawing: a theme change is posted in, never a re-render that re-runs scripts.
  // A draft's page is empty — its markup goes in separately as it grows.
  const body = draft ? '' : code
  const doc = useMemo(
    () =>
      fontFaces === null
        ? null
        : buildDrawingDocument(
            body,
            readTheme(),
            getComputedStyle(document.body).fontFamily,
            readMode(),
            fontFaces,
          ),
    [body, fontFaces],
  )

  // Layout effect, not effect: it must be listening before the frame's first report.
  useLayoutEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return
      const data = e.data as { type?: string; height?: unknown } | null
      if (data?.type === MSG_HEIGHT && typeof data.height === 'number' && data.height >= 0) {
        const next = data.height
        setHeight((prev) => (next === 0 || (!settled.current && next < prev) ? prev : next))
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  // Post the drawing in once both the loader and the fonts are ready, whichever comes last;
  // then, while it is a draft, the markup written so far.
  const render = () => {
    const win = frame.current?.contentWindow
    if (!win || !doc || !loaderReady.current) return
    if (renderedDoc.current !== doc) {
      renderedDoc.current = doc
      settled.current = false
      win.postMessage({ type: MSG_RENDER, html: doc }, '*')
    }
    if (draft) win.postMessage({ type: MSG_DRAFT, html: code }, '*')
  }
  useEffect(render, [doc, draft, code])

  useEffect(() => {
    const push = () =>
      frame.current?.contentWindow?.postMessage(
        { type: MSG_THEME, vars: readTheme(), mode: readMode() },
        '*',
      )
    const watch = new MutationObserver(push)
    const opts = { attributes: true, attributeFilter: ['class', 'data-theme', 'style'] }
    watch.observe(document.documentElement, opts)
    watch.observe(document.body, opts)
    return () => watch.disconnect()
  }, [])

  // The loader's load posts the drawing in; the drawing's own load (writing it fires `load`
  // again) only asks for its height.
  const onLoad = () => {
    if (!loaderReady.current) {
      loaderReady.current = true
      render()
    } else {
      // The finished drawing has loaded (a draft's empty page loads too, and must not count).
      if (!draft) settled.current = true
      frame.current?.contentWindow?.postMessage({ type: MSG_MEASURE }, '*')
    }
  }

  return (
    <iframe
      ref={frame}
      className="ivis-frame"
      title={title || 'Drawing'}
      sandbox={SANDBOX}
      src={FRAME_URL}
      onLoad={onLoad}
      // A draft starts at 0 and only grows; a finished drawing keeps the CSS default until it
      // reports, so a reopened transcript does not flash empty.
      style={height || draft ? { height } : undefined}
    />
  )
}
