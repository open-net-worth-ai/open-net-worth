import '../following/following.css'

import { useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { ChevronDown, Plus, Search, Wrench } from 'lucide-react'
import '../../tabs/automations.css'
import { LuciusMark } from '../../../ui/icons'
import { AutomationMenu } from '../../tabs/automations/AutomationMenu'
import type { PaperCard as Card } from '../../../api/paperTrading'
import { Button, EmptyState, ErrorState, TabSkeleton } from '../../ui'
import { Spark } from '../../../lucius/identity'
import { NewPortfolioDialog } from './NewPortfolioDialog'
import { PaperCard } from './PaperCard'
import { PaperPortfolioPage } from './PaperPortfolioPage'
import { usePaperList } from './usePaper'

const LUCIUS_PROMPT =
  'Set up a paper portfolio with me. Ask what idea I want to test, suggest the ' +
  'starting cash and the first trades, then create it.'

/** A portfolio matches when its name, idea, source, benchmark or a holding's symbol does. */
function matches(p: Card, needle: string): boolean {
  if (!needle) return true
  return [p.name, p.mandate?.goal, p.source_label, p.benchmark, ...p.weights.map((w) => w.symbol)]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
    .includes(needle)
}

/** Intelligence › Paper Trading: your paper portfolios as cards; open one to trade on paper. */
export function PaperTab({ onAskLucius }: { onAskLucius?: (text: string) => void }) {
  const list = usePaperList()
  // The open portfolio lives in the address (`?paper=<id>`), as Copy Trading's does: a reload
  // or a shared link opens it again, and Back returns to the list. Copy Trading's Paper-copy
  // lands here with it.
  const navigate = useNavigate()
  const search = useSearch({ strict: false }) as { paper?: string }
  const openId = search.paper || null
  const setOpenId = (id: string | null) =>
    void navigate({
      to: '/intel',
      search: ((prev: Record<string, unknown>) => ({ ...prev, paper: id ?? undefined })) as never,
    } as never)
  const [creating, setCreating] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [query, setQuery] = useState('')

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
  // Lucius has its own create tool, so it can set one up from an idea in plain words.
  const withLucius = onAskLucius && (
    <Button
      size="sm"
      variant="primary"
      className="fi-lucius"
      iconLeft={<Spark size={15} ariaHidden />}
      onClick={() => onAskLucius(LUCIUS_PROMPT)}
    >
      Create with Lucius
    </Button>
  )
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
            <div className="paper-actions">
              {withLucius}
              <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
                Create manually
              </Button>
            </div>
          }
        />
        {dialog}
      </div>
    )

  const needle = query.trim().toLowerCase()
  const shown = (showArchived ? archived : portfolios).filter((p) => matches(p, needle))
  return (
    <div className="fol-page">
      <section className="fol-sec" aria-label="Paper portfolios">
        {/* The Automations page's own toolbar: search, then one New menu (Lucius or by hand). */}
        <div className="a3-toolbar">
          <label className="a3-search">
            <Search size={15} strokeWidth={1.6} aria-hidden="true" />
            <span className="ui-visually-hidden">Search paper portfolios</span>
            <input
              type="search"
              value={query}
              placeholder="Search paper portfolios…"
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <span className="grow" />
          <AutomationMenu
            triggerClassName="a3-new-button"
            popoverClassName="a3-new-pop"
            label="New paper portfolio"
            trigger={
              <>
                <Plus className="a3-toolbar-ic" size={14} aria-hidden="true" />
                <span className="a3-toolbar-label">New paper portfolio</span>
                <ChevronDown
                  className="a3-toolbar-chevron"
                  size={12}
                  strokeWidth={1.7}
                  aria-hidden="true"
                />
              </>
            }
            items={[
              ...(onAskLucius
                ? [
                    {
                      label: 'Create with Lucius',
                      icon: (
                        <span className="a3-menu-ic lucius">
                          <LuciusMark size={13} aria-hidden="true" />
                        </span>
                      ),
                      onSelect: () => onAskLucius(LUCIUS_PROMPT),
                    },
                  ]
                : []),
              {
                label: 'Set up manually',
                icon: (
                  <span className="a3-menu-ic">
                    <Wrench size={14} aria-hidden="true" />
                  </span>
                ),
                onSelect: () => setCreating(true),
              },
            ]}
          />
        </div>
        {shown.length > 0 ? (
          <div className="fol-cards">
            {shown.map((p) => (
              <PaperCard key={p.id} p={p} onOpen={() => setOpenId(p.id)} />
            ))}
          </div>
        ) : (
          <p className="fol-by">
            {needle
              ? `No paper portfolio matches “${query.trim()}”.`
              : 'No active paper portfolios'}
          </p>
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
