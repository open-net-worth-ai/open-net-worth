import { Link } from '@tanstack/react-router'
import type { PaperSource as Source } from '../../../api/paperTrading'
import { portfolioParam } from '../following/useOpenPortfolio'

/** Where a paper portfolio came from, linked to it while it still exists: the followed portfolio
 *  it copied, or the Automation that runs it. A plain label otherwise. */
export function PaperSource({ source }: { source: Source }) {
  if (source.kind === 'followed' && source.followed)
    return (
      <Link
        className="paper-source-link"
        to="/intel"
        search={{ sub: 'copy-trading', portfolio: portfolioParam(source.followed) } as never}
      >
        {source.label}
      </Link>
    )
  if (source.kind === 'automation' && source.automation_id)
    return (
      <Link
        className="paper-source-link"
        to="/automations"
        search={{ automation: source.automation_id } as never}
      >
        {source.label}
      </Link>
    )
  return <>{source.label}</>
}
