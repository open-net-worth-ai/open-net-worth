import './following.css'

import { ErrorState, TabSkeleton } from '../../ui'
import { COPY } from './following.copy'
import { Discover } from './Discover'
import { PortfolioPage } from './PortfolioPage'
import { useFollowList } from './useFollowing'
import { useOpenPortfolio } from './useOpenPortfolio'

interface Props {
  onAskLucius?: (text: string) => void
}

/** Intel › Famous portfolios: browse the cards, open one, copy it. */
export function FollowingTab({ onAskLucius }: Props) {
  const list = useFollowList()
  const { open, setOpen } = useOpenPortfolio()

  if (list.initialLoading)
    return <TabSkeleton variant="holdings" label={COPY.loadingList} header={false} />
  if (!list.data)
    return (
      <ErrorState
        title={COPY.listErrorTitle}
        message={COPY.listErrorBody}
        onRetry={() => void list.refresh()}
      />
    )
  if (open)
    return (
      <PortfolioPage
        portfolio={open}
        onBack={() => setOpen(null)}
        onChanged={list.refresh}
        onAskLucius={onAskLucius}
      />
    )
  const watched = list.data.follows.map((f) => ({
    kind: f.kind,
    source_ref: f.source_ref,
    name: f.name,
    subtitle: f.subtitle,
  }))
  return (
    <div className="fol-page">
      <Discover watched={watched} onOpen={setOpen} />
    </div>
  )
}
