import { useLayoutEffect, useRef, useState } from 'react'
import type { Follow, FollowModel } from '../../../api/followedPortfolios'
import type { Names } from './activityRows'
import { COPY } from './following.copy'
import { describePortfolio } from './portfolioDescription'
import { LOGOS, PHOTOS } from './portfolioPhotos'

/** The Overview's opening: the person behind it (or the firm's logo) beside a few sentences on
 *  what this portfolio is, written from its data. Three lines show; More opens the rest. */
export function AboutPortfolio({
  follow,
  model,
  names,
  sectors,
}: {
  follow: Follow
  model: FollowModel
  names: Names
  sectors: Record<string, string>
}) {
  const [open, setOpen] = useState(false)
  const text = describePortfolio(follow, model, names, sectors).join(' ')
  // More only when the three lines cut the text off: measured, and again whenever the width
  // changes (a phone wraps more lines than a wide screen). Measured BEFORE the first paint: as
  // an effect after it, the page drew without More, then added it and pushed everything below
  // down a line (owner, 2026-11-13).
  const ref = useRef<HTMLParagraphElement>(null)
  const [cut, setCut] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || open) return
    const measure = () => setCut(el.scrollHeight > el.clientHeight + 1)
    measure()
    const watch = new ResizeObserver(measure)
    watch.observe(el)
    return () => watch.disconnect()
  }, [text, open])
  const key = `${follow.kind}:${follow.source_ref}`
  const photo = PHOTOS[key]
  const logo = photo ? undefined : LOGOS[key]
  return (
    <section
      className={photo || logo ? 'fol-about has-avatar' : 'fol-about'}
      aria-label={COPY.aboutLabel(follow.name)}
    >
      {photo && (
        <span className="fol-about-avatar" aria-hidden="true">
          <img src={photo.src} alt="" />
        </span>
      )}
      {logo && (
        <span className="fol-about-avatar is-logo" aria-hidden="true">
          <img src={logo.src} alt="" />
        </span>
      )}
      <div className="fol-about-body">
        <p ref={ref} className={open ? 'fol-about-text is-open' : 'fol-about-text'}>
          {text}
        </p>
        {(cut || open) && (
          <button type="button" className="fol-about-more" onClick={() => setOpen((o) => !o)}>
            {open ? COPY.aboutLess : COPY.aboutMore}
          </button>
        )}
      </div>
    </section>
  )
}
