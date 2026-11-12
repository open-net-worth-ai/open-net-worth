import { useState } from 'react'
import type { Follow, FollowModel } from '../../../api/followedPortfolios'
import type { Names } from './activityRows'
import { COPY } from './following.copy'
import { describePortfolio } from './portfolioDescription'

/** The Overview's opening: what this portfolio is, in a few sentences written from its data.
 *  Three lines show; More opens the rest. */
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
  return (
    <section className="fol-about" aria-label={COPY.aboutLabel(follow.name)}>
      <p className={open ? 'fol-about-text is-open' : 'fol-about-text'}>{text}</p>
      <button type="button" className="fol-about-more" onClick={() => setOpen((o) => !o)}>
        {open ? COPY.aboutLess : COPY.aboutMore}
      </button>
    </section>
  )
}
