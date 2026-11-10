// WriteReceiptChip — "what that turn actually changed to your book".
//
// Replaces the per-write confirm card (owner: "i would rather see the chip of newly
// created/updated ones in the ai response and then tap on it to see the details").
// The write has ALREADY happened by the time this renders; the job here is verifiability,
// not permission.
//
// RECEIPT ONLY — no Undo, no Redo (owner, 2026-07-20: "please stop having these undo! just
// show!"). The writes stay fully recoverable (checkpoint auto-snapshot on every _save_raw,
// txn_audit.json, soft-delete) — recovery just is not this component's job, and a button
// here promised something with an expiry (MAX_CHECKPOINTS evicts) that the rest of the
// system does not have.
//
// Three rules it exists to keep:
//   • ONE chip per turn, whatever the row count OR the type count — a 12-row import plus
//     three envelope assignments is one chip, not fifteen.
//   • ENTITY-ONLY — the collapsed line shows per-type counts; expanded detail lists the
//     affected entity chips and operation glyphs, without warnings or domain metadata.
//   • Every row is a REAL MENTION CHIP, rendered by the same <Chip> the @-picker uses, and
//     tapping it opens the REAL domain drawer — never a chat-only lookalike that would
//     drift from it. A type with no deep link yet renders a chip that simply does not
//     navigate, which is honest; a dead link would not be.
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useTransactionSurfaces } from '../tabs/accounts/transactionSurfacesContext'
import { labelFor } from '../../lucius/entityRegistry'
import { pluralize } from '../../utils/pluralize'
import { Chip } from './lucius/Chip'
import type { MentionRef, MentionType } from '../../types/mentions'
import type {
  ReceiptMention,
  WriteReceipt,
  WriteReceiptGroup,
  WriteReceiptItem,
} from '../../types/chat'
import { Check, ChevronDown, ChevronRight } from '../../ui/icons'

// Where a row of each type opens: the screen plus the search key that addresses the entity.
//
// Only types whose route ACTUALLY declares that search param in `router.tsx::validateSearch`
// appear here. A type absent from this table renders a chip that does not navigate — and
// that is the deliberate degradation, because the alternative is worse than no link: `/plan`
// has no `validateSearch`, so an `?envelope=<id>` would be silently stripped and the user
// would land on Plan with nothing selected, having been promised a specific row. Adding a
// type here is one line, AFTER its route learns to address the entity.
const RECEIPT_ROUTES: Record<string, { to: string; key: string }> = {
  transaction: { to: '/accounts', key: 'txn' },
  account: { to: '/accounts', key: 'account' },
  commitment: { to: '/commitments', key: 'open' },
}

// The op glyph is rendered BESIDE the chip, never baked into it — that is what keeps the
// chip byte-identically the search-path chip rather than a receipt-only fork.
const OP_GLYPH: Record<string, string> = { create: '+', update: '~', delete: '−' }

const OP_VERB: Record<string, string> = {
  create: 'added',
  update: 'edited',
  delete: 'deleted',
}

// A purchase batch is a "lot" to the ledger and a "holding" to the person who bought it: the
// receipt speaks to the second reader ("4 holdings added", not "4 lots added").
const RECEIPT_NOUN: Record<string, string> = { lot: 'holding' }

// Singular/plural for a type noun, derived from labelFor() (the registry the client already
// has) rather than a second table shipped down the wire — so a type this frontend has never
// heard of renders with its raw type name instead of vanishing, the same discipline as the
// unknown-flag fallback above.
function typeNoun(type: string, count: number): string {
  const base = (RECEIPT_NOUN[type] ?? labelFor(type) ?? type).replace(/_/g, ' ').toLowerCase()
  return pluralize(base, count)
}

// "3 transactions added · 1 envelope edited". The head used to read "1 edited", which never
// named WHAT was edited — harmless while every row was a transaction, actively ambiguous the
// moment they are not. A group with several different ops reads "changed", because naming
// one of them would be wrong.
function headLabel(r: WriteReceipt): string {
  const groups: WriteReceiptGroup[] = r.groups ?? []
  if (groups.length === 0) return `${r.total} change${r.total === 1 ? '' : 's'} recorded`
  const shown = groups.slice(0, 2).map((g) => {
    const verb = g.ops.length === 1 ? (OP_VERB[g.ops[0] ?? ''] ?? 'changed') : 'changed'
    return `${g.count} ${typeNoun(g.type, g.count)} ${verb}`
  })
  if (groups.length > 2) shown.push(`+${groups.length - 2} more`)
  return shown.join(' • ')
}

// Fields a PRE-GENERALIZATION receipt item carried instead of a `mention` (backend shape
// before 2026-07-21: every row was a transaction, so it inlined the ledger fields). These
// receipts are PERSISTED on turn checkpoints and rehydrated verbatim — they cannot be
// un-persisted, so the frontend must keep reading them, the same discipline as a tool-name
// alias. A receipt created before the deploy that has no `mention` is exactly what crashed
// the whole chat panel with "Cannot read properties of undefined (reading 'type')".
interface LegacyReceiptFields {
  txn_id?: string
  description?: string
  category_label?: string
}

// Every item, new or legacy, coerced to one with a guaranteed `mention`. A legacy row is
// reconstructed as the transaction it always was; the ledger fields it inlined become the
// chip's sublabel + amount. This is TOTAL — it never returns an item without a mention, so
// no downstream `.type` read can throw regardless of how old the stored payload is.
function normalizeItem(raw: WriteReceiptItem): WriteReceiptItem {
  if (raw.mention) return raw
  const l = raw as unknown as LegacyReceiptFields
  return {
    op: raw.op,
    mention: {
      type: 'transaction',
      id: l.txn_id ?? '',
      value: l.txn_id ?? '',
      label: l.description || l.category_label || '(transaction)',
      sublabel: '',
      icon: '',
      surface: 'receipt',
      // No stored id to open — the row still renders, it just does not navigate.
      stale: !l.txn_id,
    },
  }
}

// A ReceiptMention is already MentionRow-shaped; this narrows it into the composer's
// MentionRef so ONE chip component serves both surfaces.
function toMentionRef(m: ReceiptMention): MentionRef {
  return {
    type: m.type as MentionType,
    ref: m.value || m.id,
    // An unresolvable entity carries NO label (absent = "no label available", never a
    // fabricated name) — fall back to the type's own noun so the row still reads.
    label: m.label || labelFor(m.type) || m.type,
    ...(m.color ? { color: m.color } : {}),
  }
}

function ReceiptRow({ item, onOpen }: { item: WriteReceiptItem; onOpen: () => void }) {
  const m = item.mention
  const clickable = RECEIPT_ROUTES[m.type] !== undefined && !!m.id
  return (
    <div className="wr-row" data-receipt-type={m.type}>
      <span className="wr-row__op" aria-hidden="true">
        {OP_GLYPH[item.op] ?? '•'}
      </span>
      <button
        type="button"
        className="wr-row__chip"
        onClick={clickable ? onOpen : undefined}
        disabled={!clickable}
      >
        {/* Chip stops click propagation unconditionally, so the wrapper button alone would
            never see a click that landed on the chip itself. Wire both: the chip's own
            handler for a hit on the chip, the button for the focus/keyboard path. */}
        <Chip
          mention={toMentionRef(m)}
          variant="compact"
          stale={m.stale}
          onClick={clickable ? () => onOpen() : undefined}
        />
      </button>
    </div>
  )
}

export function WriteReceiptChip({ receipt }: { receipt: WriteReceipt }) {
  const navigate = useNavigate()
  const { openTransactionById } = useTransactionSurfaces()
  const [open, setOpen] = useState(false)

  const openRow = (m: ReceiptMention) => {
    const route = RECEIPT_ROUTES[m.type]
    if (!route || !m.id) return
    if (m.type === 'transaction') {
      // The drawer is an app-level surface: it opens right here, over the chat, at once —
      // the host fetches the row into it — instead of the chat handing the reader off to
      // the Accounts tab (owner, 2026-09-22).
      openTransactionById(m.id, m.label)
      return
    }
    void navigate({
      to: route.to,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      search: ((prev: any) => ({ ...prev, [route.key]: m.id })) as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any)
  }

  return (
    <div className="wr-chip" data-testid="write-receipt">
      <div className="wr-chip__bar">
        <button
          type="button"
          className="wr-chip__summary"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span className="wr-chip__glyph" aria-hidden="true">
            <Check size={12} />
          </span>
          <span className="wr-chip__counts">{headLabel(receipt)}</span>
          <span className="wr-chip__caret" aria-hidden="true">
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </span>
        </button>
      </div>

      {open && (
        <div className="wr-chip__detail">
          {receipt.items.length === 0 ? (
            // Counted but not resolvable — e.g. a row hard-removed between the write and
            // the turn's end. Say so rather than rendering an empty box that reads as
            // "nothing happened" when something did.
            <div className="wr-chip__empty">
              {receipt.total} change{receipt.total === 1 ? '' : 's'} recorded — details are no
              longer available for these rows.
            </div>
          ) : (
            <>
              {receipt.items.map(normalizeItem).map((item, i) => (
                <ReceiptRow
                  key={`${item.mention.type}:${item.mention.id}:${item.op}:${i}`}
                  item={item}
                  onOpen={() => openRow(item.mention)}
                />
              ))}
              {/* An exact total beside a sampled list — a partial view must never be
                  mistaken for the whole write. */}
              {receipt.total > receipt.showing && (
                <div className="wr-chip__more">
                  Showing {receipt.showing} of {receipt.total}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
