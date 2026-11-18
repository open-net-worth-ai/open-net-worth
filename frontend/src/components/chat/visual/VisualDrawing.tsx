import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { buildDrawingDocument, MSG_HEIGHT, MSG_MEASURE, MSG_THEME, readTheme } from './drawingFrame'

// No `allow-same-origin`: the drawing's scripts get an opaque origin, never the app's session.
const SANDBOX = 'allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals'

/** A drawing Lucius wrote itself, run as written in a sandboxed frame (see `drawingFrame.ts`).
 *  The frame grows to the drawing's height and follows the app's light/dark theme. */
export function VisualDrawing({ code, title }: { code: string; title?: string | null }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(0)
  // Built once per drawing: a theme change is posted in, never a reload that re-runs scripts.
  const doc = useMemo(
    () => buildDrawingDocument(code, readTheme(), getComputedStyle(document.body).fontFamily),
    [code],
  )

  // Layout effect, not effect: it must be listening before the frame's first report, which a
  // long transcript's deferred effects used to miss. `onLoad` below asks again regardless.
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
    const push = () =>
      frame.current?.contentWindow?.postMessage({ type: MSG_THEME, vars: readTheme() }, '*')
    const watch = new MutationObserver(push)
    const opts = { attributes: true, attributeFilter: ['class', 'data-theme', 'style'] }
    watch.observe(document.documentElement, opts)
    watch.observe(document.body, opts)
    return () => watch.disconnect()
  }, [])

  return (
    <iframe
      ref={frame}
      className="ivis-frame"
      title={title || 'Drawing'}
      sandbox={SANDBOX}
      srcDoc={doc}
      onLoad={() => frame.current?.contentWindow?.postMessage({ type: MSG_MEASURE }, '*')}
      style={height ? { height } : undefined}
    />
  )
}
