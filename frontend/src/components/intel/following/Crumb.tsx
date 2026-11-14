import { COPY } from './following.copy'

/** "Copy Trading / Berkshire Hathaway" over a portfolio's page, as an asset's page shows
 *  "Assets / its name" (owner, 2026-11-13): the first part goes back to the gallery. The page,
 *  its skeleton and its error state all show it, so the way back never moves. */
export function Crumb({ name, onBack }: { name: string; onBack: () => void }) {
  return (
    <nav className="fol-crumb" aria-label={COPY.breadcrumbLabel}>
      <button type="button" onClick={onBack}>
        {COPY.backToAll}
      </button>
      <span aria-hidden="true">/</span>
      <span className="cur">{name}</span>
    </nav>
  )
}
