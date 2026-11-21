// plan/budgetEnvelopeOps.tsx — envelope rows Part A (P4 spec §2 + the handoff-port delta):
// the merge/split/delete/create kebab wiring + MergeEnvelopeModal/AddCategoriesModal/
// AddToEnvelopeModal/RemoveCategoryModal/DeleteEnvelopeModal/NewEnvelopeModal. Copies
// ReassignDeleteModal's shell 1:1 (.pl-ov/.pl-modal/head/body/intro/label/.pl-selwrap/sub/
// list/foot) — new modals below add only what the shell doesn't already cover.
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import * as PD from './planData'
import { Chevron, X } from './icons'
import type { KebabItem } from './budgetParts'
import { Button, Checkbox, Select, Skeleton } from '../../ui'
import {
  fetchEnvelopeDeletePreview,
  STRUCTURAL_CATEGORY_KEYS,
  type CategoryMeta,
  type EnvelopeDeletePreview,
} from '../../../hooks/useBudget'
import { NEWCAT_PALETTE } from './budgetParts'
import type { LocalCat, LocalGroup } from './useBudgetModel'
import { PlanningModal } from './PlanningModal'
import { SyncFollowsNote } from './SyncFollowsNote'

const { fSmart } = PD

/** The slice of an envelope row these modals read. Structural on purpose: 50/30/20's rows are the
 *  SAME envelopes (budgetLens/proportional), and open these same modals without a LocalCat. */
export type EnvelopeRef = Pick<LocalCat, 'envelopeId' | 'label' | 'categoryIds'>
export interface EnvelopeGroupRef {
  name: string
  synced?: boolean
  cats: EnvelopeRef[]
}

// ── Merge-target picker (source: envelope A's row) ────────────────────────────────
// Excludes the row's own envelope, any locked/Commitments-synced group's rows — P4 spec
// §2.5/§2.7 (BUD-ENV-MERGE-EXCLUDES-SELF) — AND any envelope-less pseudo-row (envelopeId ===
// ''): the merge endpoint takes two REAL envelope ids, so a no-envelope row would 422 as a
// target. An N>1 target's option label carries its own "N categories" count so the picker
// stays unambiguous.
export function mergeTargetsFor(source: EnvelopeRef, allGroups: EnvelopeGroupRef[]) {
  return allGroups
    .filter((g) => !g.synced)
    .map((g) => ({
      name: g.name,
      cats: g.cats
        .filter((x) => x.envelopeId !== '' && x.envelopeId !== source.envelopeId)
        .map((x) => ({
          key: x.envelopeId,
          label:
            x.categoryIds.length > 1 ? `${x.label} (${x.categoryIds.length} categories)` : x.label,
        })),
    }))
    .filter((g) => g.cats.length)
}

// ── Kebab items: envelope-aware composition (P4 spec §2.3; handoff items 1 + 2) ───────
// 1:1 rows keep every existing item + "Merge into…" (when targets exist). N>1 rows swap
// Rename's target (the envelope, not the category), drop Move-to-group (the group store is
// still category-keyed — RFC P4 baseline fact 5), and add "Remove from envelope" (per member).
// Handoff delta vs the P4 baseline: "Remove from envelope" now lists on EVERY linked row
// (1:1 included — the only way to free a 1:1 category for another envelope) and never mints a
// fresh envelope (confirmed via RemoveCategoryModal); the old 1:1-only "Delete" (which
// destroyed the CATEGORY) is gone — every non-synced row instead gets "Delete envelope…"
// (deletes the envelope for real, unlinks its members, category deletion moves to the
// manager; a delete just made is undoable via the confirm toast's Undo pill).
export interface EnvelopeKebabHandlers {
  onRename: () => void
  onEditTarget: () => void
  onMoveTo: (groupId: string) => void
  onToggleHide: () => void
  onDeleteEnvelope: () => void
  onMerge: () => void
  onSplit: (categoryId: string, categoryLabel: string) => void
  onAddCats: () => void
}

// ── Candidate categories for "Add categories…" (envelope-side direction) ──────────
// Every OTHER real-envelope row's member categories, individually. A sole member (1:1 row)
// brings its assigned money when it joins; a member pulled out of another multi-category
// envelope leaves that envelope's pool behind (split semantics).
export interface AddCatCandidate {
  categoryId: string
  label: string
  sourceEnvelopeId: string
  soleMember: boolean
  fromEnvelope: string | null
  /** In no envelope at all (Outside envelopes): linking it is a plain add, nothing to detach. */
  unlinked?: boolean
}

export function addCategoryCandidatesFor(
  target: LocalCat,
  allGroups: LocalGroup[],
  catLabel: (key: string) => string,
  // Categories in NO envelope, offered first (owner, 2026-09-25: Gifts / Household — exactly the
  // ones that need an envelope — were missing, since only other envelopes' members were listed).
  unlinked: CategoryMeta[] = [],
): Array<{ name: string; cats: AddCatCandidate[] }> {
  const outside: Array<{ name: string; cats: AddCatCandidate[] }> = unlinked.length
    ? [
        {
          name: 'Not in an envelope',
          cats: unlinked.map((c) => ({
            categoryId: c.id,
            label: c.label,
            sourceEnvelopeId: '',
            soleMember: false,
            fromEnvelope: null,
            unlinked: true,
          })),
        },
      ]
    : []
  return outside.concat(
    allGroups
      .filter((g) => !g.synced)
      .map((g) => ({
        name: g.name,
        cats: g.cats
          .filter((x) => x.envelopeId !== '' && x.envelopeId !== target.envelopeId)
          .flatMap((x) =>
            x.categoryIds.map((k) => ({
              categoryId: k,
              label: catLabel(k),
              sourceEnvelopeId: x.envelopeId,
              soleMember: x.categoryIds.length === 1,
              fromEnvelope: x.categoryIds.length > 1 ? x.label : null,
            })),
          ),
      }))
      .filter((g) => g.cats.length),
  )
}

export function categoryKebabItems(
  c: LocalCat,
  otherGroups: LocalGroup[],
  allGroups: LocalGroup[],
  catLabel: (key: string) => string,
  h: EnvelopeKebabHandlers,
): KebabItem[] {
  const isEnvelope = c.categoryIds.length > 1
  // Zero-category envelope (RFC envelope⊥category full split — created via "Add envelope",
  // no category linked yet): no BACKING category, so category-store ops (Hide/Delete/recolor
  // — recolor is gated in CategoryRow, not here) would 404 against a real category endpoint;
  // suppress them. Rename/target/move already resolve via the envelope id (backend "handles
  // keying" — RFC §Frontend), so those stay.
  const isEmpty = c.categoryIds.length === 0
  const canAddCats =
    c.envelopeId !== '' && addCategoryCandidatesFor(c, allGroups, catLabel).length > 0
  const canMerge = c.envelopeId !== '' && mergeTargetsFor(c, allGroups).length > 0
  const items: KebabItem[] = []
  // A zero-category row's next useful action is linking a category — lead with it (RFC
  // §Frontend: "its kebab leads with Add categories…") instead of burying it after Rename/target.
  if (isEmpty && canAddCats)
    items.push({ label: 'Add categories…', onClick: h.onAddCats }, { divider: true })
  // Container-model verbs (owner, 2026-07-03; ported verbatim from prototype/plan/budget.jsx:
  // 830-852): an envelope HOLDS categories — you move a category into an envelope and remove
  // it back out; "merge" only describes combining two multi-category envelopes.
  items.push({ label: isEnvelope ? 'Rename envelope' : 'Rename', onClick: h.onRename })
  items.push({ label: c.target ? 'Edit target…' : 'Add target…', onClick: h.onEditTarget })
  // "Move to group" is offered on EVERY row now — merged (N>1) envelopes included (the kebab
  // used to block them while drag-and-drop already allowed it; now consistent, prototype
  // comment: "kebab previously blocked merged rows while drag allowed them; now consistent").
  {
    const dests = otherGroups.filter((g) => !g.synced)
    if (dests.length) {
      items.push(
        { header: 'Move to group' },
        ...dests.map((g) => ({ label: g.name, indent: true, onClick: () => h.onMoveTo(g.id) })),
      )
    }
  }
  if (canMerge) items.push({ label: 'Merge into…', onClick: h.onMerge })
  if (!isEmpty && canAddCats) items.push({ label: 'Add categories…', onClick: h.onAddCats })
  // "Remove from envelope" (handoff item 1) — lists on EVERY linked row, 1:1 included: a 1:1
  // category can only be freed for another envelope this way (its envelope stays put, keeps
  // its pool; the category itself lands in Outside envelopes → Unbudgeted). Confirmed via
  // RemoveCategoryModal at the call site (onSplit) — this function only composes the menu.
  if (!isEmpty) {
    items.push(
      { header: 'Remove from envelope' },
      ...c.categoryIds.map((k) => ({
        label: catLabel(k),
        indent: true,
        onClick: () => h.onSplit(k, catLabel(k)),
      })),
    )
  }
  // Exactly ONE divider precedes the final danger action — paired with Hide/Unhide when
  // there's a backing category, alone otherwise (prototype budget.jsx:848-850).
  if (!isEmpty)
    items.push({ divider: true }, { label: c.hidden ? 'Unhide' : 'Hide', onClick: h.onToggleHide })
  else items.push({ divider: true })
  // "Delete envelope…" (handoff item 2) replaces the old 1:1-only "Delete" that destroyed the
  // CATEGORY — every non-synced row gets it now (merged, 1:1, empty alike; this function is
  // only ever called for a non-locked/non-synced row — CategoryRow hides the kebab entirely
  // for `locked` rows). Category deletion moves to Manage categories.
  items.push({ label: 'Delete envelope…', danger: true, onClick: h.onDeleteEnvelope })
  return items
}

// ── MergeEnvelopeModal ──────────────────────────────────────────────────────────
export function MergeEnvelopeModal({
  source,
  allGroups,
  moneyLine,
  onCancel,
  onConfirm,
}: {
  source: EnvelopeRef
  allGroups: EnvelopeGroupRef[]
  /** What the merge does to the caller's own money, replacing Zero-Based's "assigned money"
   *  sentence and bullet (50/30/20 passes its caps). Given the chosen target's name. */
  moneyLine?: (destLabel: string) => ReactNode
  onCancel: () => void
  onConfirm: (targetEnvelopeId: string, targetName: string) => void
}) {
  const [target, setTarget] = useState('')
  const mergeTargets = mergeTargetsFor(source, allGroups)
  const targetName = (() => {
    for (const g of mergeTargets) {
      const c = g.cats.find((x) => x.key === target)
      if (c) return c.label
    }
    return ''
  })()
  const sourceName = source.label
  const n = source.categoryIds.length
  const destLabel = targetName || 'the destination'

  return (
    <PlanningModal onClose={onCancel} label="Merge envelope">
      <div className="pl-modal-head">
        <h3>Merge envelope</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">
          {targetName ? (
            <>
              Merge <b>{sourceName}</b> into <b>{targetName}</b>? {sourceName}&rsquo;s categories
              join {targetName}
              {moneyLine ? '.' : ', and its assigned money moves too.'}
            </>
          ) : (
            <>
              Merge <b>{sourceName}</b> into another envelope? Its categories join the destination
              {moneyLine ? '.' : ', and its assigned money moves too.'}
            </>
          )}
        </p>
        <label className="pl-modal-label" htmlFor="pl-merge-sel">
          Merge into
        </label>
        <div className="pl-selwrap">
          <Select
            dense
            id="pl-merge-sel"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="Select an envelope…"
            options={mergeTargets.map((g) => ({
              group: g.name,
              options: g.cats.map((c) => ({ value: c.key, label: c.label })),
            }))}
          />
          <span className="pl-sel-caret">
            <Chevron dir="down" size={13} />
          </span>
        </div>
        <p className="pl-modal-sub">What happens:</p>
        <ul className="pl-modal-list">
          {/* CONF-2 — an empty (zero-category) source envelope has nothing to enumerate; the
                prototype special-cases n===0 with its own copy (envelopes.jsx:115). */}
          <li>
            {n === 0
              ? 'This empty envelope folds'
              : `${n} categor${n === 1 ? 'y' : 'ies'} from ${sourceName} move${n === 1 ? 's' : ''}`}{' '}
            into {destLabel}
          </li>
          {moneyLine ? (
            <li>{moneyLine(destLabel)}</li>
          ) : (
            <li>
              All of {sourceName}&rsquo;s assigned money — every month, past and future — folds into{' '}
              {destLabel}
            </li>
          )}
          <li>Any category can be taken back out later from {destLabel}&rsquo;s menu</li>
        </ul>
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="dense"
          disabled={!target}
          onClick={() => onConfirm(target, targetName)}
        >
          Merge envelopes
        </Button>
      </div>
    </PlanningModal>
  )
}

// ── AddCategoriesModal — pick N categories to pull INTO this envelope ─────────────
// Same .pl-modal shell as the move/merge modal; multi-select via the ui Checkbox
// primitive. Per-candidate money semantics are spelled out in the description line.
export function AddCategoriesModal({
  target,
  allGroups,
  categories,
  hiddenKeys,
  catLabel,
  busy,
  onCancel,
  onConfirm,
}: {
  target: LocalCat
  allGroups: LocalGroup[]
  categories: CategoryMeta[]
  hiddenKeys: Set<string>
  catLabel: (key: string) => string
  busy?: boolean
  onCancel: () => void
  onConfirm: (picks: AddCatCandidate[]) => void
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const groups = addCategoryCandidatesFor(
    target,
    allGroups,
    catLabel,
    unlinkedCandidateCategories(categories, allGroups, hiddenKeys),
  )
  const all = groups.flatMap((g) => g.cats)
  const toggle = (key: string) =>
    setPicked((p) => {
      const n = new Set(p)
      n.has(key) ? n.delete(key) : n.add(key)
      return n
    })
  const picks = all.filter((c) => picked.has(c.categoryId))

  return (
    <PlanningModal onClose={onCancel} label="Add categories">
      <div className="pl-modal-head">
        <h3>Add categories to {target.label}</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">
          Pick the categories <b>{target.label}</b> should fund. Their transactions keep their own
          labels — they just draw from this envelope&rsquo;s pool.
        </p>
        <div className="pl-addcats-list">
          {groups.map((g) => (
            <div key={g.name} className="pl-addcats-group">
              <div className="pl-addcats-gname">{g.name}</div>
              {g.cats.map((c) => (
                <Checkbox
                  key={c.categoryId}
                  checked={picked.has(c.categoryId)}
                  onChange={() => toggle(c.categoryId)}
                  label={c.label}
                  description={
                    c.unlinked
                      ? 'not in an envelope yet — its spending this month moves in with it'
                      : c.fromEnvelope
                        ? `in ${c.fromEnvelope} — moves over; that envelope keeps its money`
                        : 'brings its assigned money along'
                  }
                />
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="dense"
          disabled={!picks.length || !!busy}
          onClick={() => onConfirm(picks)}
        >
          {busy
            ? 'Adding…'
            : picks.length
              ? `Add ${picks.length} ${picks.length === 1 ? 'category' : 'categories'}`
              : 'Add categories'}
        </Button>
      </div>
    </PlanningModal>
  )
}

// ── "Add to envelope…" target picker (Unbudgeted line → any real envelope) ────────
// Unlike mergeTargetsFor, there is no "source" envelope to exclude — the category isn't
// linked to ANY envelope yet (that's the whole point of Unbudgeted) — only synced
// (Commitments) groups and envelope-less rows (defensive; can't happen post-migration) drop out.
export function allEnvelopeTargets(allGroups: LocalGroup[]) {
  return allGroups
    .filter((g) => !g.synced)
    .map((g) => ({
      name: g.name,
      cats: g.cats
        .filter((x) => x.envelopeId !== '')
        .map((x) => ({
          key: x.envelopeId,
          label:
            x.categoryIds.length > 1 ? `${x.label} (${x.categoryIds.length} categories)` : x.label,
        })),
    }))
    .filter((g) => g.cats.length)
}

// ── AddToEnvelopeModal — the Unbudgeted surface's "Add to envelope…" (single category →
// pick ONE target envelope). Same .pl-modal shell as MergeEnvelopeModal, inverted: the
// category has no envelope to merge FROM, so confirming links it directly (useBudget's
// addCategoryToEnvelope) rather than composing split+merge.
export function AddToEnvelopeModal({
  categoryLabel,
  allGroups,
  busy,
  onCancel,
  onConfirm,
}: {
  categoryLabel: string
  allGroups: LocalGroup[]
  busy?: boolean
  onCancel: () => void
  onConfirm: (targetEnvelopeId: string, targetName: string) => void
}) {
  const [target, setTarget] = useState('')
  const targets = allEnvelopeTargets(allGroups)
  const targetName = (() => {
    for (const g of targets) {
      const c = g.cats.find((x) => x.key === target)
      if (c) return c.label
    }
    return ''
  })()

  return (
    <PlanningModal onClose={onCancel} label="Add to envelope">
      <div className="pl-modal-head">
        <h3>Add {categoryLabel} to an envelope</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">
          {targetName ? (
            <>
              <b>{categoryLabel}</b>&rsquo;s spending will draw from <b>{targetName}</b> going
              forward.
            </>
          ) : (
            <>
              Pick an envelope for <b>{categoryLabel}</b> to draw from. A category links to{' '}
              <b>one</b> envelope at a time.
            </>
          )}
        </p>
        <label className="pl-modal-label" htmlFor="pl-addtoenv-sel">
          Add to
        </label>
        <div className="pl-selwrap">
          <Select
            dense
            id="pl-addtoenv-sel"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="Select an envelope…"
            options={targets.map((g) => ({
              group: g.name,
              options: g.cats.map((c) => ({ value: c.key, label: c.label })),
            }))}
          />
          <span className="pl-sel-caret">
            <Chevron dir="down" size={13} />
          </span>
        </div>
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="dense"
          disabled={!target || !!busy}
          onClick={() => onConfirm(target, targetName)}
        >
          {busy ? 'Adding…' : 'Add to envelope'}
        </Button>
      </div>
    </PlanningModal>
  )
}

// ── RemoveCategoryModal — confirm "Remove from envelope" (handoff item 1) ─────────────────
// Ported verbatim from prototype/plan/envelopes.jsx RemoveCategoryModal. Unlinking never
// deletes anything — the category becomes envelope-less (surfaces in Outside envelopes,
// relinkable any time); the envelope keeps its pool (a sole member's activity follows the
// category out — the "sole" flag drives the "becomes an empty envelope" bullet).
export function RemoveCategoryModal({
  envelopeName,
  categoryLabel,
  sole,
  whereLine,
  keepsLine,
  onCancel,
  onConfirm,
}: {
  envelopeName: string
  categoryLabel: string
  sole: boolean
  /** Where the category's spending shows next — replaces Zero-Based's "Outside envelopes". */
  whereLine?: ReactNode
  /** What the envelope keeps — replaces Zero-Based's "keeps all its assigned money". */
  keepsLine?: ReactNode
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <PlanningModal onClose={onCancel} label="Remove category from envelope">
      <div className="pl-modal-head">
        <h3>
          Remove {categoryLabel} from {envelopeName}?
        </h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">
          <b>{categoryLabel}</b> stops drawing from <b>{envelopeName}</b>&rsquo;s pool. Nothing is
          deleted.
        </p>
        <ul className="pl-modal-list">
          <li>
            {whereLine ?? (
              <>
                {categoryLabel} moves to <b>Outside envelopes</b> — link it to another envelope any
                time
              </>
            )}
          </li>
          <li>
            {keepsLine ?? `${envelopeName} keeps all its assigned money`}
            {sole ? ' and becomes an empty envelope' : ''}
          </li>
          <li>Past transactions keep their {categoryLabel} label</li>
        </ul>
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="danger" size="dense" onClick={onConfirm}>
          Remove from envelope
        </Button>
      </div>
    </PlanningModal>
  )
}

// ── DeleteEnvelopeModal — confirm "Delete envelope…" (handoff item 2) ─────────────────────
// Ported verbatim from prototype/plan/envelopes.jsx DeleteEnvelopeModal. Pure copy-builder
// for the member-categories bullet — extracted so the pluralization rule is unit-testable
// without mounting the modal (no truncation — every member's label is spelled out, matching
// the prototype's plain `.join(', ')`).
export function describeUnlinkedCategoriesLine(labels: string[]): string {
  const n = labels.length
  if (n === 0) return ''
  return (
    `${n} categor${n === 1 ? 'y' : 'ies'} (${labels.join(', ')}) become${n === 1 ? 's' : ''} unbudgeted — ` +
    `link ${n === 1 ? 'it' : 'them'} to another envelope any time`
  )
}

export function DeleteEnvelopeModal({
  envelopeId,
  month,
  envelopeName,
  categoryLabels,
  notes,
  busy,
  onCancel,
  onConfirm,
}: {
  /** Which envelope + month `GET .../delete-preview` computes the figure for — fetched on
   *  mount, never passed in precomputed: the confirm dialog and the DELETE response it triggers
   *  must show the SAME server-realized number (`services.planning.envelope_delete_preview`). */
  envelopeId: string
  month: string
  envelopeName: string
  categoryLabels: string[]
  /** Extra consequences the caller's own method adds (50/30/20: its cap and share). */
  notes?: ReactNode[]
  busy?: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const n = categoryLabels.length
  const [preview, setPreview] = useState<EnvelopeDeletePreview | null>(null)

  // Mounted only while the dialog is open, for one fixed (envelope, month) — the fetch runs
  // exactly once, and the null starting state IS the loading state (mirrors FreshStartDialog's
  // own preview fetch). A failure is swallowed (no bullet renders) rather than blocking the
  // delete itself on a slow/broken preview read — this dialog's job is to inform, not to gate.
  useEffect(() => {
    let live = true
    fetchEnvelopeDeletePreview(envelopeId, month)
      .then((p) => live && setPreview(p))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [envelopeId, month])

  return (
    <PlanningModal onClose={onCancel} label="Delete envelope">
      <div className="pl-modal-head">
        <h3>Delete {envelopeName}?</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">
          Deleting an envelope never deletes categories or transactions — it just stops budgeting
          for them together.
        </p>
        <ul className="pl-modal-list">
          {n > 0 && <li>{describeUnlinkedCategoriesLine(categoryLabels)}</li>}
          {/* `delta_rta` is the TRUE figure — everything assigned releases, only the uncovered
             share of a cash hole stays held elsewhere, so it can be MORE than `available`
             (docs/launch/BOARD.md's undercounting repro: assign $100, spend $50 cash → $100 back,
             not $50). `available` alone still drives the second, "still held" bullet below: a
             negative Available means an unresolved overspend, a separate fact from how much
             returns — verified: available −200, Ready to Assign 900 → 1000 on delete (the whole
             zeroed assignment returns; the hole stays held), then charged once at the roll. Held
             as a property over random books in tests/test_delete_envelope_never_lowers_rta.py. */}
          {preview === null && (
            <li>
              <Skeleton width={160} height={13} radius={4} />
            </li>
          )}
          {preview !== null && preview.delta_rta > 0.005 && (
            <li>
              <b>{fSmart(preview.delta_rta)}</b> returns to Ready to Assign
            </li>
          )}
          {preview !== null && preview.available < -0.005 && (
            <li>
              Deleting this won&apos;t erase its <b>{fSmart(-preview.available)}</b> overspend — its
              categories keep holding it until month-end, so Ready to Assign doesn&apos;t drop now
            </li>
          )}
          {notes?.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="danger" size="dense" disabled={!!busy} onClick={onConfirm}>
          {busy ? 'Deleting…' : 'Delete envelope'}
        </Button>
      </div>
    </PlanningModal>
  )
}

// ── NewEnvelopeModal — category-first creation (handoff item 4) ──────────────────────────
// Ported from prototype/plan/envelopes.jsx NewEnvelopeModal (checklist + name-prefill logic
// verbatim; the checkbox rows compose the ui/ Checkbox primitive (like AddCategoriesModal)
// instead of the prototype's bespoke `.pl-ck` — already the established checkbox convention
// elsewhere in plan.css, so no parallel class family). Replaces the inline add-row: the group
// "+" opens this modal, a checklist of still-unlinked money-out categories (non-income,
// non-structural, non-hidden — a category already linked elsewhere is pulled in later via
// "Add categories…", not offered here). Zero picks starts a pure empty envelope (legal — a
// named goal pot); the name prefills from the FIRST pick until the user types.
export function unlinkedCandidateCategories(
  categories: CategoryMeta[],
  allGroups: { cats: Pick<EnvelopeRef, 'categoryIds'>[] }[],
  hiddenKeys: Set<string>,
): CategoryMeta[] {
  const linked = new Set(allGroups.flatMap((g) => g.cats.flatMap((c) => c.categoryIds)))
  return categories.filter(
    (c) =>
      c.kind !== 'income' &&
      !STRUCTURAL_CATEGORY_KEYS.has(c.id) &&
      !hiddenKeys.has(c.id) &&
      !linked.has(c.id),
  )
}

export function deriveEnvelopeName(
  pickedLabels: string[],
  typed: boolean,
  manualValue: string,
): string {
  return typed ? manualValue : (pickedLabels[0] ?? '')
}

export function NewEnvelopeModal({
  groupName,
  allGroups,
  categories,
  hiddenKeys,
  rowCount,
  busy,
  onCancel,
  onConfirm,
}: {
  /** The group it lands in — omitted where placement isn't a group (50/30/20 sorts it into a
   *  share afterwards). */
  groupName?: string
  allGroups: { cats: Pick<EnvelopeRef, 'categoryIds'>[] }[]
  categories: CategoryMeta[]
  hiddenKeys: Set<string>
  /** Total visible envelope-row count across the whole book (handoff item 5 / CONF-10) — the
   *  prototype cycles a fresh empty envelope's default swatch through NEWCAT_PALETTE by this
   *  count so consecutive empty envelopes don't all land on the same green dot. Optional/
   *  defaults to 0 (first-swatch fallback) so a caller that hasn't threaded it through yet
   *  degrades to the old behavior rather than crashing. */
  rowCount?: number
  busy?: boolean
  onCancel: () => void
  onConfirm: (name: string, color: string, categoryIds: string[]) => void
}) {
  const candidates = unlinkedCandidateCategories(categories, allGroups, hiddenKeys)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [manualName, setManualName] = useState('')
  const [typed, setTyped] = useState(false)
  const pickedCats = candidates.filter((c) => picked.has(c.id))
  const name = deriveEnvelopeName(
    pickedCats.map((c) => c.label),
    typed,
    manualName,
  )
  const color = pickedCats[0]?.color || NEWCAT_PALETTE[(rowCount ?? 0) % NEWCAT_PALETTE.length]!
  const toggle = (key: string) =>
    setPicked((p) => {
      const n = new Set(p)
      n.has(key) ? n.delete(key) : n.add(key)
      return n
    })
  const submit = () => {
    if (name.trim())
      onConfirm(
        name.trim(),
        color,
        pickedCats.map((c) => c.id),
      )
  }

  return (
    <PlanningModal onClose={onCancel} label="New envelope" className="pl-nemod-modal">
      <div className="pl-modal-head">
        <h3>{groupName ? `New envelope in ${groupName}` : 'New envelope'}</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        <p className="pl-modal-intro">
          Pick the categories this envelope funds — or pick none to start empty. Categories already
          in another envelope can be pulled in later via <b>Add categories…</b>
        </p>
        {candidates.length === 0 ? (
          <p className="pl-modal-sub" style={{ fontWeight: 500, color: 'var(--faint)' }}>
            Every category is already linked to an envelope — this one will start empty.
          </p>
        ) : (
          <div className="pl-nemod-list">
            {/* Same ui/ Checkbox its sibling AddCategoriesModal composes (design-review
                  conformance catch, 2026-07-04) — the color dot rides inside the label node. */}
            {candidates.map((c) => (
              <Checkbox
                key={c.id}
                checked={picked.has(c.id)}
                onChange={() => toggle(c.id)}
                className="pl-nemod-row"
                label={
                  <>
                    <span className="pl-nemod-dot" style={{ background: c.color }} />
                    {c.label}
                  </>
                }
                description={
                  c.kind === 'expense' ? 'not budgeted yet' : `${c.kind} • not budgeted yet`
                }
              />
            ))}
          </div>
        )}
        <label
          className="pl-modal-label"
          htmlFor="pl-nemod-name"
          style={{ marginTop: 16, display: 'block' }}
        >
          Envelope name
        </label>
        <input
          id="pl-nemod-name"
          className="pl-inline-input pl-newenv-name"
          placeholder={pickedCats.length ? '' : 'e.g. Everyday food'}
          value={name}
          onChange={(e) => {
            setTyped(true)
            setManualName(e.target.value)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              submit()
            }
          }}
        />
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" size="dense" disabled={!name.trim() || !!busy} onClick={submit}>
          {busy
            ? 'Creating…'
            : pickedCats.length
              ? `Create • funds ${pickedCats.length} ${pickedCats.length === 1 ? 'category' : 'categories'}`
              : 'Create empty envelope'}
        </Button>
      </div>
    </PlanningModal>
  )
}

// ── DeleteCategoryModal — reassign-first delete, any kind (Manage-categories only) ────────
// Ported verbatim from prototype/plan/envelopes.jsx DeleteCategoryModal. Deleting rewrites
// history, so the primary path reassigns past activity to another category first; the escape
// hatch ("delete anyway") leaves those transactions uncategorized — they resurface in the
// budget's Uncategorized pile, not silently lost.
export interface DeleteCategoryOption {
  name: string
  cats: Array<{ id: string; label: string }>
}
export function DeleteCategoryModal({
  categoryLabel,
  options,
  envelopeName,
  txnCount,
  onCancel,
  onConfirm,
}: {
  categoryLabel: string
  options: DeleteCategoryOption[]
  /** The envelope this category is currently linked to, if any (its link is stripped, not
   *  the envelope itself). */
  envelopeName: string | null
  /** Real ledger transaction count for this category (null = still loading). 0 → the whole
   *  reassign apparatus is meaningless and hidden: plain confirm (owner, 2026-07-04). */
  txnCount: number | null
  onCancel: () => void
  onConfirm: (reassignKey: string | null, reassignLabel: string) => void
}) {
  const [target, setTarget] = useState('')
  const targetName = (() => {
    for (const g of options) {
      const c = g.cats.find((x) => x.id === target)
      if (c) return c.label
    }
    return ''
  })()
  const empty = txnCount === 0

  return (
    <PlanningModal onClose={onCancel} label="Delete category">
      <div className="pl-modal-head">
        <h3>Delete {categoryLabel}</h3>
        <button className="pl-modal-x" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="pl-modal-body">
        {empty ? (
          <p className="pl-modal-intro">
            <b>{categoryLabel}</b> has no transactions — there&rsquo;s nothing to reassign.
          </p>
        ) : (
          <p className="pl-modal-intro">
            Deleting rewrites history — if you just want <b>{categoryLabel}</b> out of the way,{' '}
            <b>Hide</b> keeps its records intact. To delete, reassign its past activity to another
            category first.
          </p>
        )}
        {!empty && (
          <>
            <label className="pl-modal-label" htmlFor="pl-delcat-sel">
              Reassign activity to
            </label>
            <div className="pl-selwrap">
              <Select
                dense
                id="pl-delcat-sel"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                placeholder="Select a category…"
                options={options.map((g) => ({
                  group: g.name,
                  options: g.cats.map((c) => ({ value: c.id, label: c.label })),
                }))}
              />
              <span className="pl-sel-caret">
                <Chevron dir="down" size={13} />
              </span>
            </div>
          </>
        )}
        {!empty && <SyncFollowsNote />}
        <p className="pl-modal-sub">What happens:</p>
        <ul className="pl-modal-list">
          {empty ? (
            <li>No transactions are touched — it has none</li>
          ) : (
            <li>
              All of {categoryLabel}&rsquo;s transactions
              {txnCount != null ? (
                <>
                  {' '}
                  (<b>{txnCount}</b>)
                </>
              ) : null}{' '}
              move to {targetName || 'the selected category'}
            </li>
          )}
          {envelopeName ? (
            <li>
              Its link to the <b>{envelopeName}</b> envelope is removed — the envelope and its money
              stay
            </li>
          ) : (
            <li>It isn&rsquo;t linked to any envelope — no budget rows change</li>
          )}
          <li>{categoryLabel} disappears from every picker and report</li>
        </ul>
        {!empty && (
          <button className="pl-delcat-skip" onClick={() => onConfirm(null, '')}>
            Delete without reassigning — its transactions become uncategorized and land in{' '}
            <b>Uncategorized</b>
          </button>
        )}
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="danger"
          size="dense"
          disabled={empty ? false : !target}
          onClick={() => (empty ? onConfirm(null, '') : onConfirm(target, targetName))}
        >
          Delete category
        </Button>
      </div>
    </PlanningModal>
  )
}
