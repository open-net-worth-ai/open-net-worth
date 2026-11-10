// plan/categoriesManager.tsx — "Manage categories" settings surface (P4 spec §5; grown per
// the handoff-port item 6 into the shared 4-kind manager, ported from prototype/plan/
// envelopes.jsx CategoriesManager/CatMgrRow/EnvelopeCell — DOM/class/copy verbatim, wired to
// the real backend): a classification-only view over EVERY category — expense · inflow ·
// transfer · investment — joined against envelope membership for display only, plus create +
// delete (reassign-or-leave-Uncategorized via DeleteCategoryModal). Money, targets, and envelope
// membership stay on the Budget table — nothing here ever writes to an envelope. Retires the
// old expense/income-only filter set and the separate InflowManager modal (income categories
// are managed here now too).
import { useState, useEffect, useCallback, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { authFetch } from '../../../api/client'
import { X, Plus } from './icons'
import { ColorDot } from './budgetParts'
import { DeleteCategoryModal, AddToEnvelopeModal } from './budgetEnvelopeOps'
import type { DeleteCategoryOption } from './budgetEnvelopeOps'
import type { LocalCat, LocalGroup } from './useBudgetModel'
import { STRUCTURAL_CATEGORY_KEYS } from '../../../hooks/useCategories'
import { useHorizontalOverscrollLock } from '../../../hooks/useHorizontalOverscrollLock'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { useElementSize } from '../../../hooks/useElementWidth'
import { ErrorState, EmptyState, Button, Select, InfoPopover, TabSkeleton } from '../../ui'
import { PlanningModal } from './PlanningModal'
import { CategoryKindFilter } from './CategoryKindFilter'
import { CatMgrCard } from './CatMgrCard'
// CatMgrRow (the desktop grid row) and its shared pieces now live in CatMgrRow.tsx — split out
// to break a categoriesManager.tsx <-> CatMgrCard.tsx import cycle (mirrors accounts/TxnRow.tsx
// vs TxnCard.tsx: TxnCard imports from TxnRow, TxnRow never imports from Register). Nothing
// outside plan/ imports CatMgrRow/ManagedCategory/EnvelopeRow, so there's no compat re-export
// here — import CatMgrRow.tsx directly instead.
import { CatMgrRow, KIND_LABEL } from './CatMgrRow'
import type { ManagedCategory, EnvelopeRow } from './CatMgrRow'

const KIND_ORDER = ['expense', 'income', 'transfer', 'investment']
// The search pill keeps at least this much beside inline Kind chips; below it they fold into
// the filter icon. The gap mirrors .pl-catmgr-tools' own `gap`.
const CATMGR_SEARCH_MIN = 220
const CATMGR_TOOLS_GAP = 10

const FILTERS: [string, string][] = [
  ['all', 'All'],
  ...KIND_ORDER.map((k): [string, string] => [k, KIND_LABEL[k]!]),
]

// ── Pure filter/search (vitest-covered without mounting the modal). Alphabetical by label
// (owner decision 2026-07-04) — store order is creation order, useless for scanning a long
// list. ─────────────────────────────────────────────────────────────────────────────────────
export function filterManagedCategories(
  categories: ManagedCategory[],
  filter: string | null,
  search: string,
): ManagedCategory[] {
  const q = search.trim().toLowerCase()
  return categories
    .filter(
      (c) =>
        (!filter || filter === 'all' || c.kind === filter) &&
        (!q || c.label.toLowerCase().includes(q)),
    )
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }))
}

// ── Pure reassign-target options for "Delete…" (prototype envelopes.jsx delOptions): EVERY
// kind is a valid destination — grouped by kind, the deleted category's OWN kind sorted first
// (almost always the right destination) rather than restricted to it. ─────────────────────
export function deleteCategoryOptions(
  categories: ManagedCategory[],
  deleteKey: string,
  deleteKind: string,
): DeleteCategoryOption[] {
  return KIND_ORDER.slice()
    .sort((a, b) => (a === deleteKind ? -1 : 0) - (b === deleteKind ? -1 : 0))
    .map((k) => ({
      name: KIND_LABEL[k]!,
      cats: categories
        .filter((c) => c.kind === k && c.id !== deleteKey && !STRUCTURAL_CATEGORY_KEYS.has(c.id))
        .map((c) => ({ id: c.id, label: c.label })),
    }))
    .filter((g) => g.cats.length)
}

export function CategoriesManager({
  toast,
  onClose,
  onMutated,
}: {
  toast: (msg: string) => void
  onClose: () => void
  /** ENV-RES-03 — every successful write here ALSO invalidates the live Budget table's model
   *  (m.refreshAll from useBudgetModel), not just the manager's own local `load()`: a
   *  rename/recolor/hide/delete/link done here used to leave the table behind it stale until
   *  an unrelated write forced a resync (or a 422 from a now-ghost row snapped it back). */
  onMutated?: () => void
}) {
  const tableRef = useRef<HTMLDivElement>(null)
  useHorizontalOverscrollLock(tableRef)
  const isMobile = useIsMobile()
  const [toolsRef, tools] = useElementSize<HTMLDivElement>()
  const [categories, setCategories] = useState<ManagedCategory[] | null>(null)
  const [envelopes, setEnvelopes] = useState<EnvelopeRow[]>([])
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [renamingKey, setRenamingKey] = useState<string | null>(null)
  // "+ New category" — inline add row. A new category is classification-only on arrival: it
  // starts NOT budgeted; linking it to an envelope happens on the Budget table.
  const [addingNew, setAddingNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [newKind, setNewKind] = useState('expense')
  const [newColor, setNewColor] = useState('#3b86dd')
  const [deleteTarget, setDeleteTarget] = useState<ManagedCategory | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  // ENV-RES-06 — "Add to envelope…" per unlinked row, reusing the same AddToEnvelopeModal the
  // Outside-envelopes surface uses.
  const [addToEnvFor, setAddToEnvFor] = useState<ManagedCategory | null>(null)
  const [addToEnvBusy, setAddToEnvBusy] = useState(false)
  // Real ledger txn count for the pending delete — 0 collapses the modal to a plain confirm
  // (nothing to reassign; owner, 2026-07-04). null while loading; mirrors budget.tsx's
  // useCatTxnCount pattern.
  const [deleteTxnCount, setDeleteTxnCount] = useState<number | null>(null)
  useEffect(() => {
    if (!deleteTarget) {
      setDeleteTxnCount(null)
      return
    }
    let alive = true
    setDeleteTxnCount(null)
    authFetch(`/api/transactions?category=${encodeURIComponent(deleteTarget.id)}&limit=1`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (alive) setDeleteTxnCount((d?.total as number) ?? 0)
      })
      .catch(() => {
        if (alive) setDeleteTxnCount(null)
      })
    return () => {
      alive = false
    }
  }, [deleteTarget])

  const load = useCallback(() => {
    setError('')
    Promise.all([
      authFetch('/api/categories?include_hidden=true').then((r) =>
        r.ok ? r.json() : Promise.reject(new Error('Failed to load categories')),
      ),
      authFetch('/api/planning/envelopes').then((r) => (r.ok ? r.json() : [])),
    ])
      .then(([cats, envs]: [ManagedCategory[], EnvelopeRow[]]) => {
        setCategories(cats)
        setEnvelopes(envs)
      })
      .catch((e: Error) => setError(e.message || 'Failed to load categories'))
  }, [])
  useEffect(() => {
    load()
  }, [load])
  const rename = (id: string, label: string) => {
    setRenamingKey(null)
    authFetch(`/api/categories/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_label: label }),
    })
      .then((r) => {
        if (r.ok) {
          toast('Category renamed')
          load()
          onMutated?.()
        } else toast('Failed to rename category')
      })
      .catch(() => toast('Failed to rename category'))
  }
  const recolor = (id: string, color: string) => {
    authFetch(`/api/categories/${encodeURIComponent(id)}/color`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ color }),
    })
      .then((r) => {
        if (r.ok) {
          load()
          onMutated?.()
        } else toast('Failed to update color')
      })
      .catch(() => toast('Failed to update color'))
  }
  const toggleHide = (c: ManagedCategory) => {
    authFetch(`/api/categories/${encodeURIComponent(c.id)}/${c.hidden ? 'unhide' : 'hide'}`, {
      method: 'POST',
    })
      .then((r) => {
        if (r.ok) {
          load()
          onMutated?.()
        } else toast('Failed to update category')
      })
      .catch(() => toast('Failed to update category'))
  }
  const startAdd = () => {
    setFilter(null)
    setNewName('')
    setNewKind('expense')
    setNewColor('#3b86dd')
    setAddingNew(true)
  }
  const addCat = () => {
    const label = newName.trim()
    if (!label) return
    setAddingNew(false)
    // The server mints the opaque id — no client-side key/slug derivation (category-opaque-ids
    // migration; POST /api/categories ignores any caller-supplied key).
    authFetch('/api/categories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, kind: newKind, color: newColor }),
    })
      .then((r) => {
        if (r.ok) {
          toast(
            `Category “${label}” added — link it to an envelope on the Budget table to budget for it`,
          )
          load()
          onMutated?.()
        } else toast('Failed to add category')
      })
      .catch(() => toast('Failed to add category'))
  }
  // Shared by both the desktop add-ROW and mobile add-CARD name inputs — the only bit of the
  // ~duplicated add-category markup that's pure behavior rather than layout, so it can't drift
  // between the two independently of a future edit.
  const onAddNameKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      addCat()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setAddingNew(false)
    }
  }
  const confirmDelete = (reassignKey: string | null, reassignLabel: string) => {
    if (!deleteTarget) return
    const c = deleteTarget
    setDeleteBusy(true)
    const params = new URLSearchParams({ reassign: 'true' })
    if (reassignKey) params.set('reassign_to', reassignKey)
    authFetch(`/api/categories/${encodeURIComponent(c.id)}?${params.toString()}`, {
      method: 'DELETE',
    })
      .then(async (r) => {
        if (r.ok) {
          // The server carries this month's assigned money to an envelope-backed target
          // (`carried_assigned`) rather than releasing it to Ready to Assign — say so, since
          // money moving is exactly the kind of thing a toast may not leave unsaid.
          const carried = await r
            .json()
            .then((b) => Number(b?.carried_assigned) || 0)
            .catch(() => 0)
          toast(
            reassignKey
              ? carried > 0
                ? `Category deleted — activity and its assigned money moved to ${reassignLabel}`
                : `Category deleted — activity moved to ${reassignLabel}`
              : 'Category deleted — its transactions are now in Uncategorized',
          )
          onMutated?.()
        } else toast('Failed to delete category')
        load()
      })
      .catch(() => toast('Failed to delete category'))
      .finally(() => {
        setDeleteBusy(false)
        setDeleteTarget(null)
      })
  }

  const envelopeOf = (id: string) => envelopes.find((e) => e.category_ids.includes(id))
  // Structural plumbing (the `split` sentinel) is NOT a user category — never list, count,
  // or search it here (owner catch, 2026-07-04). deleteCategoryOptions already excludes it.
  const all = (categories ?? []).filter((c) => !STRUCTURAL_CATEGORY_KEYS.has(c.id))
  // CONF-6 — resolves a category id to its LABEL, for EnvelopeCell's tooltip. A category the
  // registry hasn't seen (yet, or ever) never paints its raw id to the user.
  const labelOf = (id: string) => all.find((c) => c.id === id)?.label ?? 'Uncategorized'
  const filterCounts: Record<string, number> = { all: all.length }
  for (const k of KIND_ORDER) filterCounts[k] = all.filter((c) => c.kind === k).length
  const rows = filterManagedCategories(all, filter, search)
  const deleteOptions = deleteTarget
    ? deleteCategoryOptions(all, deleteTarget.id, deleteTarget.kind)
    : []

  // ENV-RES-06 — this modal only ever fetches the flat EnvelopeRow[] list (never the grouped
  // Budget-table model), so AddToEnvelopeModal (which groups by envelope GROUP) is handed one
  // synthetic group wrapping every real envelope flatly. Field values other than
  // key/label/color/envelopeId/categoryIds are irrelevant to its own allEnvelopeTargets() scan.
  const allGroupsForModal: LocalGroup[] = [
    {
      id: '_all',
      name: 'Envelopes',
      collapsed: false,
      synced: false,
      subtotal: { assigned: 0, activity: 0, available: 0 },
      cats: envelopes.map(
        (e): LocalCat => ({
          key: e.category_ids[0] ?? e.id,
          label: e.name,
          color: e.color || '#3b86dd',
          assigned: 0,
          activity: 0,
          available: 0,
          target: 0,
          tt: null,
          cad: null,
          status: 'none',
          needed: 0,
          envelopeId: e.id,
          categoryIds: e.category_ids,
        }),
      ),
    },
  ]
  const confirmAddToEnv = async (targetId: string, targetName: string) => {
    if (!addToEnvFor) return
    setAddToEnvBusy(true)
    try {
      const r = await authFetch(
        `/api/planning/envelopes/${encodeURIComponent(targetId)}/add-category`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category_id: addToEnvFor.id }),
        },
      )
      if (r.ok) {
        toast(`${addToEnvFor.label} now draws from ${targetName}`)
        load()
        onMutated?.()
      } else toast('Failed to add to envelope')
    } catch {
      toast('Failed to add to envelope')
    } finally {
      setAddToEnvBusy(false)
      setAddToEnvFor(null)
    }
  }

  return (
    <PlanningModal onClose={onClose} label="Manage categories" className="pl-catmgr-modal">
      <div className="pl-modal-head">
        <h3>
          Manage categories
          <InfoPopover
            width={290}
            text="Every category — expense, inflow, transfer, investment — in one place. This is classification only: rename, recolor, or hide. A category isn't budgeted on its own — link it to an envelope on the Budget table (one envelope per category) to start assigning it money."
          />
        </h3>
        <button className="pl-modal-x" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      {/* data-autofocus parks initial focus on the (non-interactive) body instead of Radix's
          own default — the first tabbable descendant. There's no primary field to jump to in a
          browsing/list modal like this one, and the two candidates that default would otherwise
          pick between are both worse: the info button popped its tooltip open unprompted (the
          bug this fixed), and a close button would put Enter/Space one accidental keypress away
          from dismissing the dialog you just opened. */}
      <div className="pl-modal-body pl-catmgr-body" tabIndex={-1} data-autofocus>
        <div className="pl-catmgr-tools" ref={toolsRef}>
          <CategoryKindFilter
            inlineWidth={tools.w - CATMGR_SEARCH_MIN - CATMGR_TOOLS_GAP}
            options={FILTERS.map(([key, label]) => ({
              id: key,
              label,
              count: filterCounts[key] ?? 0,
            }))}
            value={filter || 'all'}
            onChange={(id) => setFilter(id === 'all' ? null : id)}
          />
          <span className="pl-tsearch">
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="M21 21l-4.3-4.3" />
            </svg>
            <input
              type="text"
              value={search}
              placeholder="Search…"
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setSearch('')
              }}
              aria-label="Search categories"
            />
            {search && (
              <button
                className="pl-tsearch-x"
                onClick={() => setSearch('')}
                aria-label="Clear search"
              >
                <X size={10} />
              </button>
            )}
          </span>
        </div>
        <div className="pl-catmgr-table" ref={tableRef}>
          {categories === null && !error ? (
            <div className="pl-catmgr-emptywrap">
              <TabSkeleton variant="categories" label="Loading categories…" header={false} />
            </div>
          ) : error ? (
            <div className="pl-catmgr-emptywrap">
              <ErrorState message={error} onRetry={load} />
            </div>
          ) : rows.length === 0 && !addingNew ? (
            <div className="pl-catmgr-emptywrap">
              <EmptyState
                title="No categories match"
                body="Try a different filter or search term."
              />
            </div>
          ) : (
            <div className="pl-table-inner">
              {!isMobile && (
                <div className="pl-trow pl-thead">
                  <span>Category</span>
                  <span>Kind</span>
                  <span>Envelope</span>
                  <span>Hidden</span>
                </div>
              )}
              <div className="pl-tbody">
                {addingNew &&
                  (isMobile ? (
                    <div className="pl-catmgr-card pl-catmgr-addcard">
                      <span className="pl-cname">
                        <ColorDot
                          color={newColor}
                          onPick={setNewColor}
                          label="Color for new category"
                          icon="tag"
                        />
                        {/* deliberately NOT InlineEdit: blur must not commit/cancel, or tapping
                              the kind select would close the card before it registers */}
                        <input
                          className="pl-inline-input"
                          value={newName}
                          autoFocus
                          placeholder="New category name…  (Enter to add, Esc to cancel)"
                          onChange={(e) => setNewName(e.target.value)}
                          onKeyDown={onAddNameKeyDown}
                        />
                      </span>
                      <span className="pl-catmgr-card-row">
                        <Select
                          dense
                          className="pl-catmgr-kindsel"
                          value={newKind}
                          aria-label="Kind"
                          onChange={(e) => {
                            setNewKind(e.target.value)
                          }}
                          options={KIND_ORDER.map((k) => ({ value: k, label: KIND_LABEL[k]! }))}
                        />
                      </span>
                    </div>
                  ) : (
                    <div className="pl-trow pl-crow pl-catmgr-row pl-catmgr-addrow">
                      <span className="pl-cname">
                        <ColorDot
                          color={newColor}
                          onPick={setNewColor}
                          label="Color for new category"
                          icon="tag"
                        />
                        {/* deliberately NOT InlineEdit: blur must not commit/cancel, or clicking
                              the kind select would close the row before it registers */}
                        <input
                          className="pl-inline-input"
                          value={newName}
                          autoFocus
                          placeholder="New category name…  (Enter to add, Esc to cancel)"
                          onChange={(e) => setNewName(e.target.value)}
                          onKeyDown={onAddNameKeyDown}
                        />
                      </span>
                      <span>
                        <Select
                          dense
                          className="pl-catmgr-kindsel"
                          value={newKind}
                          aria-label="Kind"
                          onChange={(e) => {
                            setNewKind(e.target.value)
                          }}
                          options={KIND_ORDER.map((k) => ({ value: k, label: KIND_LABEL[k]! }))}
                        />
                      </span>
                      <span>
                        <span className="pl-catmgr-env pl-catmgr-env--none">Not budgeted</span>
                      </span>
                      <span />
                    </div>
                  ))}
                {rows.map((c) => {
                  const envelope = envelopeOf(c.id)
                  // ENV-RES-06 — only an UNLINKED, non-structural, non-income category gets
                  // the affordance (envelopes are spending-only; income never budgets here).
                  const canAddToEnv =
                    !envelope && c.kind !== 'income' && !STRUCTURAL_CATEGORY_KEYS.has(c.id)
                  const Row = isMobile ? CatMgrCard : CatMgrRow
                  return (
                    <Row
                      key={c.id}
                      c={c}
                      envelope={envelope}
                      labelOf={labelOf}
                      renaming={renamingKey === c.id}
                      onStartRename={() => setRenamingKey(c.id)}
                      onCancelRename={() => setRenamingKey(null)}
                      onRename={(label) => rename(c.id, label)}
                      onRecolor={(color) => recolor(c.id, color)}
                      onToggleHide={() => toggleHide(c)}
                      onDelete={() => setDeleteTarget(c)}
                      onAddToEnvelope={canAddToEnv ? () => setAddToEnvFor(c) : undefined}
                    />
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="pl-modal-foot" style={{ justifyContent: 'space-between' }}>
        {addingNew ? (
          <>
            <span className="pl-catmgr-addhint">
              New categories start unbudgeted — link them to an envelope on the Budget table
            </span>
            <span className="pl-catmgr-addactions">
              <Button variant="secondary" size="dense" onClick={() => setAddingNew(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="dense"
                className="pl-catmgr-addbtn"
                disabled={!newName.trim()}
                onClick={addCat}
              >
                Add category
              </Button>
            </span>
          </>
        ) : (
          <>
            <Button
              variant="secondary"
              size="dense"
              iconLeft={<Plus size={12} />}
              onClick={startAdd}
            >
              New category
            </Button>
            <Button variant="primary" size="dense" onClick={onClose}>
              Done
            </Button>
          </>
        )}
      </div>
      {/* Gate on the resolved txn count: rendering before it resolves flashed the reassign
          variant, then morphed into the plain confirm — read as "two popups" (owner catch). */}
      {deleteTarget && deleteTxnCount != null && (
        <DeleteCategoryModal
          categoryLabel={deleteTarget.label}
          options={deleteOptions}
          envelopeName={envelopeOf(deleteTarget.id)?.name ?? null}
          txnCount={deleteTxnCount}
          onCancel={() => {
            if (!deleteBusy) setDeleteTarget(null)
          }}
          onConfirm={confirmDelete}
        />
      )}
      {addToEnvFor && (
        <AddToEnvelopeModal
          categoryLabel={addToEnvFor.label}
          allGroups={allGroupsForModal}
          busy={addToEnvBusy}
          onCancel={() => {
            if (!addToEnvBusy) setAddToEnvFor(null)
          }}
          onConfirm={confirmAddToEnv}
        />
      )}
    </PlanningModal>
  )
}
