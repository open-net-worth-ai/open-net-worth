import '../following/following.css'

import { useState } from 'react'
import { Button, EmptyState } from '../../ui'
import { NewPortfolioDialog } from './NewPortfolioDialog'
import { PaperCard } from './PaperCard'
import { PaperPortfolioPage } from './PaperPortfolioPage'
import { resetPrototype, usePaperPortfolios } from './paperStore'

/** Intelligence › Paper Trading: your paper portfolios as cards, open one, trade on paper.
 *  PROTOTYPE: reads an in-browser store (paperStore.ts), not the API yet. */
export function PaperTab() {
  const all = usePaperPortfolios()
  const [openId, setOpenId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const live = all.filter((p) => !p.archived)
  const archived = all.filter((p) => p.archived)
  const open = all.find((p) => p.id === openId)

  if (open) return <PaperPortfolioPage p={open} onBack={() => setOpenId(null)} onOpen={setOpenId} />

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

  if (live.length === 0)
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

  return (
    <div className="fol-page">
      <section className="fol-sec" aria-label="Paper portfolios">
        <div className="paper-toolbar">
          <span className="fol-by">Never in your net worth</span>
          <Button variant="paper" size="sm" onClick={() => setCreating(true)}>
            New paper portfolio
          </Button>
        </div>
        <div className="fol-cards">
          {(showArchived ? archived : live).map((p) => (
            <PaperCard key={p.id} p={p} onOpen={() => setOpenId(p.id)} />
          ))}
        </div>
        {archived.length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setShowArchived((v) => !v)}>
            {showArchived ? 'Back to active' : `Archived runs (${archived.length})`}
          </Button>
        )}
        <div className="paper-proto">
          Prototype data, kept in this browser.
          <Button variant="ghost" size="sm" onClick={resetPrototype}>
            Reset prototype
          </Button>
        </div>
      </section>
      {dialog}
    </div>
  )
}
