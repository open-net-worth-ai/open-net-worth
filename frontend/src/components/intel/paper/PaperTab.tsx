import '../following/following.css'

import { useState } from 'react'
import { Button, EmptyState, ErrorState, TabSkeleton } from '../../ui'
import { NewPortfolioDialog } from './NewPortfolioDialog'
import { PaperCard } from './PaperCard'
import { PaperPortfolioPage } from './PaperPortfolioPage'
import { usePaperList } from './usePaper'

/** Intelligence › Paper Trading: your paper portfolios as cards; open one to trade on paper. */
export function PaperTab({ onAskLucius }: { onAskLucius?: (text: string) => void }) {
  const list = usePaperList()
  // `?paper=<id>` opens that portfolio (Copy Trading's Paper-copy lands here with it).
  const [openId, setOpenId] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get('paper'),
  )
  const [creating, setCreating] = useState(false)
  const [showArchived, setShowArchived] = useState(false)

  if (openId)
    return (
      <PaperPortfolioPage
        id={openId}
        onBack={() => setOpenId(null)}
        onOpen={setOpenId}
        onAskLucius={onAskLucius}
      />
    )
  if (list.initialLoading)
    return <TabSkeleton variant="holdings" label="Loading paper portfolios" header={false} />
  if (!list.data)
    return (
      <ErrorState
        title="Paper portfolios didn't load"
        message="Check that the app is running, then try again."
        onRetry={() => void list.refresh()}
      />
    )

  const { portfolios, archived } = list.data
  const dialog = (
    <NewPortfolioDialog
      open={creating}
      onClose={() => setCreating(false)}
      onCreated={(id) => {
        setCreating(false)
        setOpenId(id)
      }}
    />
  )

  if (portfolios.length === 0 && archived.length === 0)
    return (
      <div className="fol-page">
        <EmptyState
          fill
          title="Try an idea before you put money in"
          body="Trade with pretend money at real prices. Never in your net worth."
          action={
            <Button variant="paper" onClick={() => setCreating(true)}>
              New paper portfolio
            </Button>
          }
        />
        {dialog}
      </div>
    )

  const shown = showArchived ? archived : portfolios
  return (
    <div className="fol-page">
      <section className="fol-sec" aria-label="Paper portfolios">
        <div className="paper-toolbar">
          <span className="fol-by">Never in your net worth</span>
          <Button variant="paper" size="sm" onClick={() => setCreating(true)}>
            New paper portfolio
          </Button>
        </div>
        {shown.length > 0 ? (
          <div className="fol-cards">
            {shown.map((p) => (
              <PaperCard key={p.id} p={p} onOpen={() => setOpenId(p.id)} />
            ))}
          </div>
        ) : (
          <p className="fol-by">No active paper portfolios</p>
        )}
        {archived.length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setShowArchived((v) => !v)}>
            {showArchived ? 'Back to active' : `Archived runs (${archived.length})`}
          </Button>
        )}
      </section>
      {dialog}
    </div>
  )
}
