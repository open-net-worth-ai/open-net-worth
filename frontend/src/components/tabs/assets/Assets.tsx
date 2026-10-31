// Assets tab shell — a faithful port of the design handoff's main.jsx `App`, minus the
// standalone prototype's own AppNav/Surface shell (RouterShell already owns nav/topbar).
// See docs/adr/2026-09-06-assets-tab-faithful-port.md for the port strategy: every Lucius
// touchpoint opens the app's REAL dock via onAskLucius (never a scripted local chat panel),
// and every write goes through api.ts's typed helpers (owned_assets choke), never a local
// setState-only mock like the prototype's.
import { useRef, useState } from 'react'
import type { OwnedAsset } from '../../../types/assets'
import { useAssets } from '../../../hooks/useAssets'
import { useRegisterScreenFacts } from '../../../lucius/useScreenContract'
import { Container, ErrorState, TabSkeleton, PageHeader, Button } from '../../ui'
import * as api from './api'
import { catOf, classCardsFrom, fmt, fmtOrDash, monthYear } from './assetsData'
import { AssetWizard } from './AssetWizard'
import { AssetDetail } from './detail'
import { DeleteAssetConfirm, DisposeModal } from './modals'
import { FilterBar, Rail, SummaryStrip } from './register'
import { CardGrid } from './registerGrid'
import { EmptyPreview } from './EmptyPreview'
import { LUCIUS_SETUP, useAssetsIntro } from './assetsIntro'
import { LuciusCta } from '../../intro/LuciusCta'
import { useLuciusChatOptional } from '../../../state/luciusChat'
import { useViewScroll } from '../../../hooks/useViewScroll'
import './assets.css'

export interface AssetsProps {
  onAskLucius: (text: string) => void
  /** The Lucius side panel is open: the first-visit intro waits rather than interrupt him. */
  luciusOpen?: boolean
  onNavigate: (tab: string, search?: Record<string, string>) => void
  selectedId?: string | null
  onSelect?: (id: string | null) => void
  /** The category the register shows (from `?cat=`) — a place back can return from. */
  selectedCat?: string | null
  onSelectCat?: (cat: string | null) => void
}

// go(k) — the register/rail/class-card "open its own tab" targets map onto this app's real
// tab ids (the prototype's mock targets were literal sibling HTML files).
const NAV_TARGETS: Record<string, string> = {
  accounts: 'accounts',
  investments: 'investments',
  overview: 'dashboard',
  commitments: 'commitments',
  plan: 'plan',
  automations: 'automations',
}

const SAVE_FAILED = "That didn't save — try again."

export function Assets(props: AssetsProps) {
  const { onAskLucius, onNavigate, selectedId, onSelect, selectedCat, onSelectCat, luciusOpen } =
    props
  const {
    assets,
    categories,
    loans,
    cashAccounts,
    summary,
    baseCurrency,
    loading,
    error,
    refresh,
  } = useAssets()

  const [localOpenId, setLocalOpenId] = useState<string | null>(null)
  const urlControlled = onSelect !== undefined
  const openId = urlControlled ? (selectedId ?? null) : localOpenId

  // A category is in the URL when the shell routes it, so back returns to All (owner:
  // "how to go back after tapping on a group?"); standalone, it is local state.
  const [localCat, setLocalCat] = useState('all')
  const catControlled = onSelectCat !== undefined
  const cat = catControlled ? (selectedCat ?? 'all') : localCat

  // The register, a category and an asset are one path and one scroller: going forward opens
  // the next view at its top, and Back returns to where the list was (owner, 2026-10-03).
  const rememberScroll = useViewScroll(openId ? `asset:${openId}` : `cat:${cat}`)
  const setOpenId = (id: string | null) => {
    if (id) rememberScroll()
    if (urlControlled) onSelect!(id)
    else setLocalOpenId(id)
  }
  const setCat = (next: string) => {
    if (next !== cat) rememberScroll()
    if (catControlled) onSelectCat!(next === 'all' ? null : next)
    else setLocalCat(next)
  }
  const [addOpen, setAddOpen] = useState(false)
  // A placeholder tile on the empty page opens the wizard on its own category.
  const [addCat, setAddCat] = useState<string | null>(null)
  // The asset just added, shown until the refetch that includes it lands — so its page opens
  // at once instead of the register flashing back first.
  const [justCreated, setJustCreated] = useState<OwnedAsset | null>(null)
  if (justCreated && assets.some((a) => a.id === justCreated.id)) setJustCreated(null)
  const [disposing, setDisposing] = useState<OwnedAsset | null>(null)
  const [deleting, setDeleting] = useState<OwnedAsset | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const showToast = (msg: string) => {
    setToast(msg)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 2600)
  }

  // Held only while Lucius is mid-reply beside the page, never just for his panel being open.
  const replying = !!useLuciusChatOptional()?.streaming
  const luciusReplying = !!luciusOpen && replying
  const intro = useAssetsIntro(
    luciusReplying || !summary
      ? null
      : assets.length === 0 && summary.own === 0 && summary.owe === 0
        ? 'empty'
        : 'filled',
    onAskLucius,
  )

  const go = (k: string) => {
    const tab = NAV_TARGETS[k]
    if (tab) onNavigate(tab)
  }

  const setPhoto = async (id: string, file: File) => {
    try {
      await api.uploadPhoto(id, file)
    } catch (e) {
      // The server's own reason (e.g. "The photo must be an image.") — never a generic line.
      showToast(e instanceof Error ? e.message : SAVE_FAILED)
    }
  }

  const created = (asset: OwnedAsset) => {
    setAddOpen(false)
    setJustCreated(asset)
    setOpenId(asset.id)
    showToast(`${asset.name} added`)
  }

  // Sold or deleted: the asset leaves the register, so its page closes.
  const disposed = () => {
    setDisposing(null)
    setOpenId(null)
    showToast('Disposal recorded · asset archived')
  }
  const deleted = (asset: OwnedAsset) => {
    setDeleting(null)
    setOpenId(null)
    showToast(`${asset.name} deleted`)
  }

  const open = openId
    ? (assets.find((a) => a.id === openId) ?? (justCreated?.id === openId ? justCreated : null))
    : null

  // ONE registration for both the register and the detail screen — branches on which is on
  // screen, exactly like Commitments.tsx (a second useRegisterScreenFacts inside the detail
  // would race this one on mount/unmount).
  useRegisterScreenFacts(() => {
    if (open) {
      return [
        ['Viewing asset', open.name || '(unnamed)'],
        ['Category', catOf(categories, open.category).label],
        ['Value', fmtOrDash(open.value)],
        ['Valued', monthYear(open.valued_on)],
      ]
    }
    if (!summary) return []
    return [
      ['Assets tracked', String(assets.length)],
      ['Everything you own', fmt(summary.own)],
      ['Net worth contribution', fmt(summary.net_worth)],
    ]
  })

  if (loading) {
    return (
      <Container size="wide">
        <div className="as-page">
          <div className="as-wrap">
            <TabSkeleton variant="assets" label="Loading assets…" />
          </div>
        </div>
      </Container>
    )
  }
  // Blocking only when there is nothing to show. A background refetch that fails (say, the one
  // a write's data-changed signal starts while a dialog is still saving) keeps the last payload
  // on screen — unmounting here would throw away an open dialog and everything typed into it.
  if (!summary) {
    return (
      <Container size="wide">
        <ErrorState
          message={error ?? 'The server returned an invalid assets payload.'}
          onRetry={refresh}
        />
      </Container>
    )
  }

  const classCards = classCardsFrom(summary)
  const shown = cat === 'all' ? assets : assets.filter((a) => a.category === cat)
  const shownClasses = cat === 'all' ? classCards : []
  // Kinds you own nothing of yet stay on the page as tiles, so adding the next one is one click.
  const usedCats = new Set(assets.map((a) => a.category))
  const moreCats = categories.filter((c) => !usedCats.has(c.key))
  const emptyBook = assets.length === 0 && summary.own === 0 && summary.owe === 0

  return (
    <Container size="wide">
      <div className="as-page" data-screen-label="Assets">
        <div className="as-wrap">
          {open ? (
            // Another asset is another page: nothing opened on the last one carries over.
            <AssetDetail
              key={open.id}
              a={open}
              cats={categories}
              loans={loans}
              onBack={() => setOpenId(null)}
              onDispose={setDisposing}
              onDelete={setDeleting}
              onPhoto={setPhoto}
              onNav={go}
              onToast={showToast}
            />
          ) : emptyBook ? (
            <div className="as-empty-book">
              <PageHeader
                title="Assets"
                subtitle="Cash, investments, and things you own"
                actions={
                  <div className="as-empty-actions">
                    <Button
                      variant="primary"
                      size="sm"
                      data-tour="assets-add"
                      onClick={() => setAddOpen(true)}
                    >
                      Add asset
                    </Button>
                    <LuciusCta
                      label="Set up with Lucius"
                      message={LUCIUS_SETUP}
                      onAskLucius={onAskLucius}
                    />
                  </div>
                }
              />
              <EmptyPreview
                cats={categories}
                onAddAccount={(type) => onNavigate('accounts', { add: type })}
                onAddAsset={(key) => {
                  setAddCat(key)
                  setAddOpen(true)
                }}
              />
            </div>
          ) : (
            <>
              {/* Desktop: this whole block is plain scrolling flow (no position:sticky
                  anywhere below) — title, KPIs, chips and cards all scroll together. Mobile
                  (≤640px, assets.css): .as-head and .as-bar EACH become their own sticky
                  band (title+Add asset pinned at the very top, chips pinned directly below
                  it), while the KPI strip in between stays in normal scrolling flow — see
                  each class's own mobile media-query comment for why. */}
              <div className="as-head">
                <PageHeader
                  title="Assets"
                  subtitle="Everything belongs to you"
                  actions={
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
                        Add asset
                      </Button>
                    </div>
                  }
                />
              </div>
              <div className="as-kpi-row" style={{ marginTop: 16 }}>
                <SummaryStrip summary={summary} />
              </div>
              <FilterBar list={assets} cats={categories} cat={cat} setCat={setCat} />
              <div className="as-body-cq">
                <div className="as-body">
                  <div style={{ minWidth: 0 }}>
                    <CardGrid
                      list={shown}
                      classes={shownClasses}
                      cats={categories}
                      loans={loans}
                      cat={cat}
                      onOpen={setOpenId}
                      // The "Add more" tiles below already offer every kind not owned yet.
                      onAdd={
                        cat === 'all' && moreCats.length > 0 ? undefined : () => setAddOpen(true)
                      }
                      onNav={go}
                      onCat={setCat}
                    />
                    {cat === 'all' && moreCats.length > 0 && (
                      <section className="as-more" aria-label="Add more">
                        <h3 className="as-more-h">Add more</h3>
                        <EmptyPreview
                          cats={moreCats}
                          accounts={false}
                          onAddAccount={() => {}}
                          onAddAsset={(key) => {
                            setAddCat(key)
                            setAddOpen(true)
                          }}
                        />
                      </section>
                    )}
                  </div>
                  <Rail cats={categories} classes={classCards} summary={summary} loans={loans} />
                </div>
              </div>
            </>
          )}
        </div>

        {addOpen && (
          <AssetWizard
            baseCurrency={baseCurrency ?? ''}
            cats={categories}
            loans={loans}
            // A category chip active on the register starts the wizard at step 2 with it.
            initialCategory={addCat ?? (categories.some((c) => c.key === cat) ? cat : null)}
            onClose={() => {
              setAddOpen(false)
              setAddCat(null)
            }}
            onCreated={created}
            onAskLucius={onAskLucius}
          />
        )}
        {disposing && (
          <DisposeModal
            asset={disposing}
            cashAccounts={cashAccounts}
            onClose={() => setDisposing(null)}
            onDisposed={disposed}
          />
        )}
        {deleting && (
          <DeleteAssetConfirm
            asset={deleting}
            onClose={() => setDeleting(null)}
            onDeleted={() => deleted(deleting)}
          />
        )}
        {intro.element}
        {toast && <div className="as-toast mb-anchored">{toast}</div>}
      </div>
    </Container>
  )
}
