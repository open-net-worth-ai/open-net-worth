import { COPY } from './following.copy'

/** "Copy Trading / Berkshire Hathaway" over a portfolio's page, as an asset's page shows
 *  "Assets / its name" (owner, 2026-11-13): the first part goes back to the gallery. The page,
 *  its skeleton and its error state all show it, so the way back never moves. */
export function Crumb({
  name,
  onBack,
  root = COPY.backToAll,
}: {
  name: string
  onBack: () => void
  /** The gallery it goes back to: Copy Trading by default, Paper Trading for a paper page. */
  root?: string
}) {
  return (
    <nav className="fol-crumb" aria-label={COPY.breadcrumbLabel}>
      <button type="button" onClick={onBack}>
        {root}
      </button>
      <span aria-hidden="true">/</span>
      <span className="cur">{name}</span>
    </nav>
  )
}
