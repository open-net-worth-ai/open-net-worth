import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { DRAG_IGNORE_PROPS } from '../../hooks/useSheetDragToDismiss'
import { usePinchZoom } from '../../hooks/usePinchZoom'
import { ChevronLeft, ChevronRight } from '../../ui/icons'
import { usePageImage } from './usePageImage'

// The full-size reader: every page of a PDF stacked and scrolled (or swiped) through like any
// document viewer, each page loading as it nears the view. A floating "‹ 2 / 4 ›" pill stays in
// sight — it follows the page you're on and jumps between pages. It replaced one page at a time
// with its pager UNDER a page taller than the window: opened cut off, pager out of sight, no
// way to scroll on (owner report). Pinch, double-tap or a trackpad pinch zooms the pages
// (usePinchZoom — the app has the browser's own zoom off); the reader pans by scrolling, and
// the pages own their touches (DRAG_IGNORE_PROPS), so a pinch never slides a phone sheet.

const A4 = 1 / Math.SQRT2 // width / height until page 1 tells us its own
const LOAD_AHEAD = '800px 0px'

function PageImage({
  itemId,
  page,
  count,
  title,
  onRatio,
}: {
  itemId: string
  page: number
  count: number
  title: string
  onRatio?: (ratio: number) => void
}) {
  const shown = usePageImage(itemId, page)
  if (shown.error)
    return (
      <p className="vault-pages-error">
        Can't show page {page}: {shown.error}
      </p>
    )
  if (!shown.url) return null
  return (
    <img
      className="vault-reader-img"
      src={shown.url}
      alt={`${title}, page ${page} of ${count}`}
      onLoad={(e) => onRatio?.(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)}
    />
  )
}

/** One page's slot: sized before it loads (so the scroll doesn't jump), loaded when near. */
function ReaderPage({
  scroller,
  ...props
}: {
  itemId: string
  page: number
  count: number
  title: string
  ratio: number
  scroller: string
  onRatio?: (ratio: number) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [near, setNear] = useState(props.page === 1 || typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    const el = ref.current
    if (near || !el) return
    // Measured against the reader's own scroller: against the page, the modal body clips
    // everything outside its view and the look-ahead margin never applied.
    const root = el.closest<HTMLElement>(scroller)
    const observer = new IntersectionObserver(
      (entries) => entries.some((e) => e.isIntersecting) && setNear(true),
      { root, rootMargin: LOAD_AHEAD },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [near, scroller])
  return (
    <div
      ref={ref}
      className="vault-reader-page"
      data-page={props.page}
      style={{ aspectRatio: String(props.ratio) }}
    >
      {near && <PageImage {...props} />}
    </div>
  )
}

export function DocumentReader({
  itemId,
  title,
  scroller = '.ui-modal__body',
}: {
  itemId: string
  title: string
  /** The element that scrolls the pages — the modal body, or the import's file pane. */
  scroller?: string
}) {
  const first = usePageImage(itemId, 1) // tells us how many pages there are
  const count = first.count ?? 1
  const [ratio, setRatio] = useState(A4)
  const [current, setCurrent] = useState(1)
  const rootRef = useRef<HTMLDivElement>(null)
  const pagesRef = useRef<HTMLDivElement>(null)
  const zoom = usePinchZoom(pagesRef, scroller)

  // The page you're on: the last whose top has passed the upper third of the view.
  useEffect(() => {
    const view = rootRef.current?.closest<HTMLElement>(scroller)
    if (!view) return
    const onScroll = () => {
      const edge = view.getBoundingClientRect().top + view.clientHeight / 3
      let on = 1
      view.querySelectorAll<HTMLElement>('.vault-reader-page').forEach((el) => {
        if (el.getBoundingClientRect().top <= edge) on = Number(el.dataset.page) || on
      })
      setCurrent(on)
    }
    view.addEventListener('scroll', onScroll, { passive: true })
    return () => view.removeEventListener('scroll', onScroll)
  }, [count, scroller])

  const jump = (page: number) =>
    rootRef.current
      ?.querySelector(`.vault-reader-page[data-page="${page}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  if (first.error)
    return <p className="vault-pages-error">Can't show this document here: {first.error}</p>
  return (
    <div className="vault-reader" ref={rootRef}>
      <div
        ref={pagesRef}
        className="vault-reader-pages"
        style={{ width: `${zoom * 100}%`, '--reader-zoom': zoom } as CSSProperties}
        {...DRAG_IGNORE_PROPS}
      >
        {Array.from({ length: count }, (_, i) => (
          <ReaderPage
            key={i + 1}
            itemId={itemId}
            page={i + 1}
            count={count}
            title={title}
            ratio={ratio}
            scroller={scroller}
            onRatio={i === 0 ? setRatio : undefined}
          />
        ))}
      </div>
      {/* Zoomed in, the pages pan sideways past it; a double-tap goes back to the whole page. */}
      {count > 1 && zoom === 1 && (
        <div className="vault-reader-bar" role="group" aria-label="Pages">
          <button
            type="button"
            className="vault-icon-btn"
            aria-label="Previous page"
            disabled={current <= 1}
            onClick={() => jump(current - 1)}
          >
            <ChevronLeft size={16} strokeWidth={1.8} />
          </button>
          <span className="vault-pager-label">
            {current} / {count}
          </span>
          <button
            type="button"
            className="vault-icon-btn"
            aria-label="Next page"
            disabled={current >= count}
            onClick={() => jump(current + 1)}
          >
            <ChevronRight size={16} strokeWidth={1.8} />
          </button>
        </div>
      )}
    </div>
  )
}
