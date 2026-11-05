// The asset page — header (thumb, name, the ⋯ menu), the value stats with a visible Update
// value, the value chart, the Value history / Attachments sub-tabs and the rail. Each region
// lives in its own file (markChart, ValueHistory, Attachments, sections); this is the shell that
// composes them. DOM/class vocabulary follows the design handoff's detail.jsx (ADR
// 2026-09-06-assets-tab-faithful-port).
import { useEffect, useState, type ReactNode } from 'react'
import type { AssetCategory, AssetLoan, OwnedAsset } from '../../../types/assets'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { ActionMenu, Button, Cluster, MaskedText, PageTabs } from '../../ui'
import {
  annualizedReturnOf,
  equityOf,
  fmt,
  fmtOrDash,
  gainOf,
  gainPctOf,
  loanOf,
  monthYear,
  pct,
  sharePct,
  yearsHeldOf,
} from './assetsData'
import { HeaderPhoto, PhotoViewer, usePhotoPicker } from './AssetPhoto'
import { Attachments } from './Attachments'
import { useDocUpload, useFileDrop } from './docUpload'
import { DetailsEditor } from './DetailsEditor'
import { MarkChart } from './markChart'
import { OwnershipEditor } from './OwnershipEditor'
import { PurchaseEditor } from './PurchaseEditor'
import { RenameCategoryEditor } from './RenameCategoryEditor'
import { AssetSections, NotesEditor, type EditTarget } from './sections'
import { UpdateValueDialog } from './ValueDialogs'
import { ValueHistory } from './ValueHistory'

const MORE_ICON = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
    <circle cx="3" cy="8" r="1.4" />
    <circle cx="8" cy="8" r="1.4" />
    <circle cx="13" cy="8" r="1.4" />
  </svg>
)

export interface AssetDetailProps {
  a: OwnedAsset
  cats: AssetCategory[]
  loans: AssetLoan[]
  onBack: () => void
  onDispose: (a: OwnedAsset) => void
  onDelete: (a: OwnedAsset) => void
  onPhoto: (id: string, file: File) => void
  onNav: (tab: string) => void
  /** Shows a toast once a dialog on this page has saved. */
  onToast: (message: string) => void
}

/** The page's own dialogs — one open at a time, each mounted fresh from the asset. */
type PageDialog = 'update' | 'rename' | EditTarget

interface PageDialogsProps {
  open: PageDialog | null
  a: OwnedAsset
  cats: AssetCategory[]
  loans: AssetLoan[]
  onClose(): void
  onSaved(message: string): void
}

function PageDialogs({ open, a, cats, loans, onClose, onSaved }: PageDialogsProps) {
  const props = { a, onClose, onSaved }
  switch (open) {
    case 'update':
      return <UpdateValueDialog {...props} />
    case 'rename':
      return <RenameCategoryEditor {...props} cats={cats} />
    case 'ownership':
      return <OwnershipEditor {...props} loans={loans} />
    case 'purchase':
      return <PurchaseEditor {...props} />
    case 'details':
      return <DetailsEditor {...props} />
    case 'custom-detail':
      return <DetailsEditor {...props} addDetail />
    case 'notes':
      return <NotesEditor {...props} />
    default:
      return null
  }
}

interface ValueStatsProps {
  a: OwnedAsset
  loans: AssetLoan[]
  /** A phone's header has no room for it, so Update value closes the Value stat there. */
  isMobile: boolean
  onUpdate(): void
}

function ValueStats({ a, loans, isMobile, onUpdate }: ValueStatsProps) {
  const loan = loanOf(a, loans)
  const eq = equityOf(a, loans)
  const g = gainOf(a)
  const gp = gainPctOf(a)
  const yrs = yearsHeldOf(a)
  const ann = annualizedReturnOf(a)
  // Share-of-value figures need a value to divide by; an imported asset may have none yet.
  const ofValue = (n: number) => (a.value ? (n / a.value) * 100 : 0)
  return (
    <>
      <div className="as-dstats">
        <div>
          <div className="as-dstat-k">Value</div>
          <div className="as-dstat-v money">{fmtOrDash(a.value)}</div>
          {a.valued_on && <div className="as-dstat-s">valued {monthYear(a.valued_on)}</div>}
          {a.share_pct < 100 && (
            <div className="as-dstat-share">
              <MaskedText>{`Your ${sharePct(a.share_pct)}% · ${fmtOrDash(a.share_value)}`}</MaskedText>
            </div>
          )}
        </div>
        {loan && loan.balance > 0 && eq != null && (
          <div>
            <div className="as-dstat-k">Net equity</div>
            <div className="as-dstat-v money">{fmt(eq)}</div>
            <div className="as-dstat-s">
              Whole asset · <span className="money">{Math.round(ofValue(eq))}%</span> of value
            </div>
          </div>
        )}
        <div>
          <div className="as-dstat-k">Unrealized</div>
          {/* Zero is neither a gain nor a loss: plain "S$0", never a green "+S$0". */}
          <div className={'as-dstat-v money ' + (!g ? '' : g > 0 ? 'pos' : 'neg')}>
            {g == null ? '—' : g === 0 ? fmt(0) : (g > 0 ? '+' : '−') + fmt(Math.abs(g))}
          </div>
          <div className="as-dstat-s">
            <MaskedText>
              {g == null
                ? 'No purchase price yet'
                : `${pct(gp ?? 0)}${yrs ? ` · ${yrs.toFixed(1)} yr` : ''}${ann != null ? ` · ${pct(ann)}/yr` : ''}`}
            </MaskedText>
          </div>
        </div>
      </div>
      {isMobile && (
        <Button variant="primary" fullWidth className="as-dstat-value-cta" onClick={onUpdate}>
          Update value
        </Button>
      )}
    </>
  )
}

interface DetailHeaderProps {
  a: OwnedAsset
  cats: AssetCategory[]
  isMobile: boolean
  onBack(): void
  onUpdate(): void
  onPhoto(file: File): void
  onRename(): void
  onDispose(): void
  onDelete(): void
  onToast(message: string): void
  /** Desktop: the figures, stacked beside the photo's place under the name. */
  stats?: ReactNode
}

/** The breadcrumb, then the photo and name, with Update value and the ⋯ menu on the right (a
 *  phone keeps only the menu there, and shows the photo as the page's cover above the name). */
function DetailHeader(props: DetailHeaderProps) {
  const { a, cats, isMobile } = props
  const [viewing, setViewing] = useState(false)
  const picker = usePhotoPicker(props.onPhoto)
  const photo = (cover?: boolean) => (
    <HeaderPhoto
      a={a}
      cats={cats}
      cover={cover}
      onOpen={() => setViewing(true)}
      onAdd={picker.pick}
    />
  )
  // One layout, photo or not: the cover on a phone, a media block on desktop — the name,
  // description and figures beside the photo's place.
  const coverOnTop = isMobile
  const media = !isMobile
  return (
    <>
      <div className="as-crumb">
        <button onClick={props.onBack}>Assets</button>
        <span>/</span>
        <span className="cur">{a.name}</span>
      </div>
      {picker.input}
      {coverOnTop && photo(true)}
      <div className={media ? 'as-dhead as-dhead-media' : 'as-dhead'}>
        {!coverOnTop && photo()}
        <div className="as-dhead-title">
          <h1 className="as-dtitle">{a.name}</h1>
          {/* The asset's own description only — the category's color/icon is already on the
              thumb. Hidden entirely rather than left blank when there's no description. Two
              lines at most; the whole of it on hover. */}
          {a.description && (
            <div
              className="as-card-sub as-dhead-sub"
              style={{ fontSize: 12 }}
              title={a.description}
            >
              {a.description}
            </div>
          )}
          {media && props.stats}
        </div>
        <Cluster gap="--space-3" wrap={false} className="as-dhead-actions">
          {!isMobile && (
            <Button variant="primary" size="sm" onClick={props.onUpdate}>
              Update value
            </Button>
          )}
          <ActionMenu
            trigger={MORE_ICON}
            items={[
              { label: 'Rename or change category', onSelect: props.onRename },
              { label: a.photo_ref ? 'Change photo' : 'Add photo', onSelect: picker.pick },
              { label: 'Sell or dispose', onSelect: props.onDispose },
              { label: 'Delete asset', tone: 'danger', onSelect: props.onDelete },
            ]}
          />
        </Cluster>
      </div>
      {viewing && (
        <PhotoViewer
          a={a}
          onClose={() => setViewing(false)}
          onChange={() => {
            setViewing(false)
            picker.pick()
          }}
          onRemoved={() => {
            setViewing(false)
            props.onToast('Photo removed.')
          }}
        />
      )}
    </>
  )
}

export function AssetDetail(props: AssetDetailProps) {
  const { a, cats, loans, onToast } = props
  const isMobile = useIsMobile()
  const [tab, setTab] = useState<'history' | 'docs'>('history')
  const [dialog, setDialog] = useState<PageDialog | null>(null)
  // Files dropped anywhere on the page attach to this asset — in the Documents tab, whichever
  // tab was open — sharing one upload with its Add file.
  const upload = useDocUpload(a, onToast)
  const drop = useFileDrop((files) => {
    setTab('docs')
    void upload.addFiles(files)
  })

  // The register and this page share one scroller, and opening an asset changes only `?id=`,
  // never the path the shell resets scroll on — so it opened mid-page. A phone scrolls
  // .app-layout, a desktop the window: reset both, on mount and whenever another asset opens.
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
    document.querySelector('.app-layout')?.scrollTo(0, 0)
  }, [a.id])

  // Desktop: the figures sit beside the photo's place, in the header; a phone, under it.
  const statsInHeader = !isMobile
  const stats = (
    <ValueStats a={a} loans={loans} isMobile={isMobile} onUpdate={() => setDialog('update')} />
  )
  return (
    <div className="as-dpage">
      <DetailHeader
        stats={statsInHeader ? stats : undefined}
        a={a}
        cats={cats}
        isMobile={isMobile}
        onBack={props.onBack}
        onUpdate={() => setDialog('update')}
        onPhoto={(file) => props.onPhoto(a.id, file)}
        onRename={() => setDialog('rename')}
        onDispose={() => props.onDispose(a)}
        onDelete={() => props.onDelete(a)}
        onToast={onToast}
      />
      <div className="as-body as-dbody" {...drop.handlers}>
        {drop.over && (
          <div className="as-drop" aria-hidden="true">
            <span className="as-drop-label">Drop to add to {a.name}’s documents</span>
          </div>
        )}
        <div style={{ minWidth: 0 }}>
          {!statsInHeader && stats}
          {a.marks.length > 0 && <MarkChart a={a} />}
          <AssetSections a={a} cats={cats} loans={loans} onNav={props.onNav} onEdit={setDialog} />
          <div className="as-subtabs">
            <PageTabs
              value={tab}
              onChange={setTab}
              tabs={[
                { key: 'history', label: 'Value history' },
                { key: 'docs', label: 'Documents' },
              ]}
            />
          </div>
          {tab === 'history' && (
            <ValueHistory a={a} onEditPurchase={() => setDialog('purchase')} onToast={onToast} />
          )}
          {tab === 'docs' && <Attachments a={a} upload={upload} onToast={onToast} />}
        </div>
      </div>
      <PageDialogs
        open={dialog}
        a={a}
        cats={cats}
        loans={loans}
        onClose={() => setDialog(null)}
        onSaved={(message) => {
          setDialog(null)
          onToast(message)
        }}
      />
    </div>
  )
}
