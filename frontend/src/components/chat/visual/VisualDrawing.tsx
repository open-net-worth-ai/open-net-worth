import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  buildDrawingDocument,
  FRAME_URL,
  MSG_HEIGHT,
  MSG_MEASURE,
  MSG_RENDER,
  MSG_THEME,
  readMode,
  readTheme,
} from './drawingFrame'

// No `allow-same-origin`: the drawing's scripts get an opaque origin, never the app's session.
const SANDBOX = 'allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals'

/** A drawing Lucius wrote itself, run as written in a sandboxed frame. The frame is the loader
 *  at `FRAME_URL`, served with its own security policy — a `srcdoc` frame would inherit the
 *  app page's `script-src 'self'` and run no script at all, the drawing's or the height
 *  report's. Once the loader is up, the drawing's document is posted in; the frame then grows
 *  to the drawing's height and follows the app's light/dark theme. */
export function VisualDrawing({ code, title }: { code: string; title?: string | null }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const rendered = useRef(false)
  const [height, setHeight] = useState(0)
  // Built once per drawing: a theme change is posted in, never a re-render that re-runs scripts.
  const doc = useMemo(
    () =>
      buildDrawingDocument(
        code,
        readTheme(),
        getComputedStyle(document.body).fontFamily,
        readMode(),
      ),
    [code],
  )

  // Layout effect, not effect: it must be listening before the frame's first report.
  useLayoutEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return
      const data = e.data as { type?: string; height?: unknown } | null
      if (data?.type === MSG_HEIGHT && typeof data.height === 'number' && data.height >= 0) {
        setHeight(data.height)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  useEffect(() => {
    rendered.current = false
  }, [doc])

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
    const win = frame.current?.contentWindow
    if (!win) return
    if (!rendered.current) {
      rendered.current = true
      win.postMessage({ type: MSG_RENDER, html: doc }, '*')
    } else {
      win.postMessage({ type: MSG_MEASURE }, '*')
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
      style={height ? { height } : undefined}
    />
  )
}
