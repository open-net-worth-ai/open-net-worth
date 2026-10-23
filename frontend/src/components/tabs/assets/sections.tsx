// The asset page's record: Ownership and Purchase — the money — beside Details and Notes — the
// thing itself — as flat groups in two columns that keep their own heights (one column when the
// page is narrow). A group is drawn only when it has something to show; everything still
// missing is ONE row of "+" chips after them, each opening the editor its group's Edit opens
// (owner, 2026-09-28: the four boxed cards were "ugly, including when the information is not
// there and when it's even there") — and always one for a detail of your own, which was
// otherwise hidden inside the Details editor. The Notes editor lives here; the others have their
// own files.
import { useState, type ReactNode } from 'react'
import { useCaretAtEnd } from '../../../hooks/useCaretAtEnd'
import type { AssetCategory, AssetLoan, OwnedAsset } from '../../../types/assets'
import { Cluster, Modal, Stack, TextArea } from '../../ui'
import * as api from './api'
import { canHaveLoan, purchaseCopy, showsInsured } from './assetCategoryRules'
import { catOf, equityOf, fmt, loanOf, monthYear, shareOf } from './assetsData'
import { labelList, splitFacts, type Fact } from './detailsFacts'
import { EditorFooter, SaveError, useEditorSave } from './editorKit'
import { Icons } from './icons'

export type Section = 'ownership' | 'purchase' | 'details' | 'notes'
/** What an Edit or a "+" chip opens: a group's editor, or Details with a new row of your own. */
export type EditTarget = Section | 'custom-detail'

export interface AssetSectionsProps {
  a: OwnedAsset
  cats: AssetCategory[]
  loans: AssetLoan[]
  onNav: (tab: string) => void
  onEdit: (target: EditTarget) => void
}

/** A group: its small title and a quiet Edit, then what it holds. */
function Group(props: { title: string; onEdit(): void; children: ReactNode }) {
  return (
    <section className="as-rec-group" aria-label={props.title}>
      <div className="as-rec-head">
        <h2 className="as-rec-title">{props.title}</h2>
        <button
          type="button"
          className="as-rec-edit"
          aria-label={`Edit ${props.title.toLowerCase()}`}
          onClick={props.onEdit}
        >
          Edit
        </button>
      </div>
      {props.children}
    </section>
  )
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="as-rec-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

/** The linked loan while anything is owed on it — a paid-off one secures nothing. */
function securedBy(a: OwnedAsset, loans: AssetLoan[]): AssetLoan | null {
  const loan = loanOf(a, loans)
  return loan && loan.balance > 0 ? loan : null
}

interface LoanSplitProps {
  a: OwnedAsset
  loan: AssetLoan
  cats: AssetCategory[]
  loans: AssetLoan[]
  onNav(tab: string): void
}

/** The value, split between what is yours and what is owed — and the way to the loan. */
function LoanSplit({ a, loan, cats, loans, onNav }: LoanSplitProps) {
  const eq = equityOf(a, loans) ?? 0
  const ofValue = (n: number) => (a.value ? (n / a.value) * 100 : 0)
  const color = catOf(cats, a.category).color
  const alsoSecures = loan.held_by && loan.held_by.id !== a.id ? loan.held_by.name : null
  const terms = [loan.rate_label, loan.term_label].filter(Boolean).join(' · ')
  return (
    <div className="as-rec-loan">
      <div className="as-split" aria-hidden="true">
        <i style={{ width: `${Math.max(0, ofValue(eq))}%`, background: color }} />
        <i
          style={{
            width: `${Math.min(100, ofValue(loan.balance))}%`,
            background: 'var(--bad)',
            opacity: 0.55,
          }}
        />
      </div>
      <dl className="as-rec-list">
        <Row
          label={
            <>
              <i className="as-rec-dot" style={{ background: color }} />
              Equity
            </>
          }
        >
          {fmt(eq)} · {Math.round(ofValue(eq))}%
        </Row>
        <Row
          label={
            <>
              <i className="as-rec-dot as-rec-dot--owed" />
              Owed
            </>
          }
        >
          {fmt(loan.balance)} · {Math.round(ofValue(loan.balance))}%
        </Row>
      </dl>
      {terms && <p className="as-rec-caption">{terms}</p>}
      {alsoSecures && <p className="as-rec-caption">This loan also secures {alsoSecures}.</p>}
      <button type="button" className="as-rec-link" onClick={() => onNav('accounts')}>
        See this loan in Accounts
      </button>
    </div>
  )
}

function Ownership({ a, cats, loans, onNav, onEdit }: AssetSectionsProps) {
  const loan = securedBy(a, loans)
  return (
    <Group title="Ownership" onEdit={() => onEdit('ownership')}>
      <dl className="as-rec-list">
        <Row label="Your share">{shareOf(a)}</Row>
        {loan && <Row label="Secured by">{loan.name}</Row>}
      </dl>
      {loan && <LoanSplit a={a} loan={loan} cats={cats} loans={loans} onNav={onNav} />}
    </Group>
  )
}

function Purchase({ a, onEdit }: { a: OwnedAsset; onEdit(): void }) {
  const copy = purchaseCopy(a.category)
  return (
    <Group title={copy.cardTitle} onEdit={onEdit}>
      <dl className="as-rec-list">
        {a.cost != null && <Row label={copy.costLabel}>{fmt(a.cost)}</Row>}
        {a.cost_date && <Row label={copy.dateLabel}>{monthYear(a.cost_date)}</Row>}
      </dl>
    </Group>
  )
}

/** Every detail with a value — the category's own first, in its order, then the rest. An
 *  unticked Insured is not a fact (it is also what an asset starts with), so only Yes shows. */
function detailRows(a: OwnedAsset): Fact[] {
  const { guided, other } = splitFacts(a.category, a.facts)
  const rows = guided.filter((g) => g.value).map((g): Fact => [g.prompt[0], g.value])
  if (showsInsured(a.category)) {
    if (a.insured) rows.push(['Insured', 'Yes'])
  } else if (a.maturity_date) rows.push(['Matures on', monthYear(a.maturity_date)])
  return [...rows, ...other]
}

interface Missing {
  label: string
  aria: string
  opens: EditTarget
}

/** What the record could still hold, in the order its groups are drawn. A detail of your own
 *  is always one of them — a category's prompts are never the only things worth writing down. */
function missing(a: OwnedAsset, loans: AssetLoan[]): Missing[] {
  const out: Missing[] = []
  const add = (label: string, opens: EditTarget, aria = `Add ${labelList([label])}`) =>
    out.push({ label, aria, opens })
  if (!securedBy(a, loans) && (canHaveLoan(a.category) || loanOf(a, loans)))
    add('Link a loan', 'ownership', 'Link a loan')
  if (a.cost == null && !a.cost_date) add(purchaseCopy(a.category).chipLabel, 'purchase')
  for (const g of splitFacts(a.category, a.facts).guided) if (!g.value) add(g.prompt[0], 'details')
  if (!showsInsured(a.category) && !a.maturity_date) add('Maturity date', 'details')
  add('Custom detail', 'custom-detail', 'Add a custom detail')
  if (!a.notes) add('A note', 'notes')
  return out
}

/** The asset's record — where it is edited, group by group — as part of the page itself. */
export function AssetSections(props: AssetSectionsProps) {
  const { a, onEdit } = props
  const details = detailRows(a)
  const bought = a.cost != null || Boolean(a.cost_date)
  const chips = missing(a, props.loans)
  return (
    <div className="as-record">
      <div className="as-rec-cols">
        <div className="as-rec-col">
          <Ownership {...props} />
          {bought && <Purchase a={a} onEdit={() => onEdit('purchase')} />}
        </div>
        {(details.length > 0 || a.notes) && (
          <div className="as-rec-col">
            {details.length > 0 && (
              <Group title="Details" onEdit={() => onEdit('details')}>
                <dl className="as-rec-list">
                  {details.map(([k, v], i) => (
                    <Row key={i} label={k}>
                      {v}
                    </Row>
                  ))}
                </dl>
              </Group>
            )}
            {a.notes && (
              <Group title="Notes" onEdit={() => onEdit('notes')}>
                <p className="as-rec-notes">{a.notes}</p>
              </Group>
            )}
          </div>
        )}
      </div>
      <Cluster gap="--space-2" className="as-rec-add">
        {chips.map((m) => (
          <button
            key={m.label}
            type="button"
            className="as-chip as-chip--add"
            aria-label={m.aria}
            onClick={() => onEdit(m.opens)}
          >
            <span aria-hidden="true">{Icons.plus!({ width: 12, height: 12 })}</span>
            {m.label}
          </button>
        ))}
      </Cluster>
    </div>
  )
}

export interface NotesEditorProps {
  a: OwnedAsset
  onClose(): void
  onSaved(message: string): void
}

/** Notes — one text box; saves exactly `{notes}`. */
export function NotesEditor({ a, onClose, onSaved }: NotesEditorProps) {
  const [notes, setNotes] = useState(a.notes)
  const { saving, save, dialogError } = useEditorSave()
  const caretAtEnd = useCaretAtEnd()

  async function onSave() {
    if (await save(() => api.updateAsset(a.id, { notes: notes.trim() }))) onSaved('Note saved.')
  }

  return (
    <Modal
      className="as-modal"
      open
      onClose={onClose}
      title="Notes"
      subtitle={a.name}
      size="lg"
      footer={<EditorFooter saving={saving} label="Save" onCancel={onClose} onSave={onSave} />}
    >
      <Stack gap="--space-4">
        <TextArea
          aria-label="Notes"
          rows={12}
          autoFocus
          onFocus={caretAtEnd}
          value={notes}
          maxLength={api.TEXT_CAPS.notes}
          placeholder="Anything worth remembering — where the receipt is, who to call, condition…"
          onChange={(e) => setNotes(e.target.value)}
        />
        <SaveError message={dialogError([])} />
      </Stack>
    </Modal>
  )
}
