import { useMemo, useState, type ReactElement } from 'react'
import { diffLines, diffWordsWithSpace } from 'diff'
import type {
  AutomationFieldChange,
  AutomationUpdateDisplay,
  ProposedChange,
  ToolPreview,
} from '../../../../types/chat'
import {
  ApprovalChange,
  ApprovalCode,
  ApprovalDetails,
  ApprovalDiff,
  ApprovalDiffLine,
  ApprovalDiffWord,
  ApprovalFact,
  ApprovalFacts,
  ApprovalMeta,
  ApprovalNote,
  ApprovalSection,
  ApprovalTable,
  ApprovalToggle,
  type DiffKind,
} from '../ApprovalCard'
import {
  humanizeType,
  isRecord,
  permissionArguments,
  permissionTextEdits,
  plainText,
  previewHasNoChanges,
  scalarText,
  withNamesInPlace,
} from '../cardTypes'
import { BatchCell } from './BatchBody'
import { fBase } from '../../../../utils/format'
import { pluralize } from '../../../../utils/pluralize'

type Entry = [string, unknown]

// A value this short reads as a fact row; anything longer folds behind a disclosure.
const INLINE_MAX = 80
const TEXT_INLINE_MAX = 160

const isScalar = (v: unknown) => v == null || ['string', 'number', 'boolean'].includes(typeof v)

/** A value as one line of plain words: lists joined, an object as "key: value" pairs. */

/** The value as a fact row's text, or null when it is too big for one row. */
function inlineText(v: unknown): string | null {
  const flat =
    isScalar(v) ||
    (Array.isArray(v) && v.every(isScalar)) ||
    (isRecord(v) && Object.keys(v).length <= 3 && Object.values(v).every(isScalar))
  if (!flat) return null
  const text = plainText(v)
  const max = isScalar(v) ? TEXT_INLINE_MAX : INLINE_MAX
  return text.length <= max && !text.includes('\n') ? text : null
}

function foldSummary(key: string, v: unknown): string {
  const [n, unit] =
    typeof v === 'string'
      ? [v.length, 'character']
      : Array.isArray(v)
        ? [v.length, 'item']
        : [Object.keys(v as object).length, 'field']
  return `${humanizeType(key)} · ${n} ${pluralize(unit, n)}`
}

function RawArguments({ value }: { value: unknown }) {
  return (
    <ApprovalDetails summary="Raw arguments">
      <ApprovalCode>{JSON.stringify(value, null, 2)}</ApprovalCode>
    </ApprovalDetails>
  )
}

/** Each entry on its own row; a value too big for a row folds behind a summary that names
 * its key and size — never one row holding a JSON blob. */
function ArgumentRows({ entries, format }: { entries: Entry[]; format?: Format }) {
  const facts: Array<[string, string]> = []
  const tables: Array<[string, Record<string, unknown>[]]> = []
  const folds: Entry[] = []
  for (const [key, value] of entries) {
    if (isRecordList(value)) {
      tables.push([key, value])
      continue
    }
    const text = inlineText(value)
    if (text === null) folds.push([key, value])
    else facts.push([key, text])
  }
  if (entries.length === 0) return null
  // Folds and tables share the facts block, so a card of nothing BUT them still has one.
  return (
    <ApprovalFacts>
      {facts.map(([key, text]) => (
        <ApprovalFact key={key} label={humanizeType(key)}>
          {text}
        </ApprovalFact>
      ))}
      {tables.map(([key, rows]) => (
        <RecordsTable key={key} rows={rows} format={format} />
      ))}
      {folds.map(([key, value]) => (
        <ApprovalDetails key={key} summary={foldSummary(key, value)}>
          <ApprovalCode>
            {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
          </ApprovalCode>
        </ApprovalDetails>
      ))}
    </ApprovalFacts>
  )
}

type Format = (key: string, value: unknown) => unknown

// A short list of records (assignments, renames, one row per item) IS the change: shown as a
// table, never folded behind "2 items". A long one still folds.
const isRecordList = (v: unknown): v is Record<string, unknown>[] =>
  Array.isArray(v) && v.length > 0 && v.length <= 12 && v.every(isRecord)

function RecordsTable({ rows, format }: { rows: Record<string, unknown>[]; format?: Format }) {
  const named = rows.map((r) => Object.fromEntries(namedEntries(r, format)))
  const columns = [...new Set(named.flatMap((r) => Object.keys(r)))]
  return (
    <ApprovalTable
      head={columns.map(humanizeType)}
      rows={named.map((r, i) => ({
        key: i,
        cells: columns.map((c) => <BatchCell key={c} field={c} value={r[c]} />),
      }))}
    />
  )
}

// A record's entries with names standing in for their ids (`envelope_name` replaces
// `envelope_id`, labelled "Envelope"); `format` may restyle a value (a budget amount).
function namedEntries(record: Record<string, unknown>, format?: Format): Entry[] {
  return withNamesInPlace(record)
    .filter(([, value]) => value != null && value !== '')
    .map(([key, value]): Entry => [key, format ? format(key, value) : value])
}

// Bookkeeping on the change itself — never part of what is being confirmed.
const OMIT = new Set([
  'type',
  'id',
  'status',
  'confirmed',
  'display',
  'reason',
  'refused',
  'grant_tool',
  'tool_use_id',
  'session_id',
])

// What a card shows, in order of trust: a gated tool's own arguments, the backend's
// `display` summary, then the change's meaningful top-level payload. The tool's name is
// never an entry — the header already says what the card does.
function genericEntries(change: ProposedChange): Entry[] {
  const display = (change.display || {}) as Record<string, unknown>
  const shown = ([key, value]: Entry) =>
    value != null && value !== '' && !(key === 'tool' && value === change.grant_tool)
  if (change.grant_tool && isRecord(display.arguments)) {
    return Object.entries(display.arguments).filter(shown)
  }
  const fromDisplay = withNamesInPlace(display)
    // `op` is the card's title (CardHeader), not a fact.
    .filter(([key]) => key !== 'op')
    .filter(shown)
  if (fromDisplay.length > 0) return fromDisplay
  return Object.entries(change as unknown as Record<string, unknown>).filter(
    (entry) => !OMIT.has(entry[0]) && shown(entry),
  )
}

// Generic fallback for ANY change type without a bespoke body — including future tools'
// changes — and every gated tool that has no card of its own. The load-bearing safety net:
// the user always sees what they're confirming.
export function GenericChangePreview({ change }: { change: ProposedChange }) {
  const entries = genericEntries(change)
  if (entries.length === 0) {
    return (
      <ApprovalNote>
        {change.grant_tool && isRecord(change.display?.arguments)
          ? 'No arguments'
          : 'No further details'}
      </ApprovalNote>
    )
  }
  return <ArgumentRows entries={entries} />
}

type WordPart = { text: string; changed: boolean }
type DiffRow = { kind: DiffKind; text: string; parts?: WordPart[] }
type DiffItem = { type: 'line'; row: DiffRow } | { type: 'collapsed'; rows: DiffRow[] }

// Below this share of unchanged characters a line was rewritten, not edited — marking
// nearly every word would be noise, so the pair stays a plain line diff.
const MIN_SHARED = 0.4

function splitLines(value: string): string[] {
  const lines = value.split('\n')
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
}

// A one-line instruction edited by a phrase is one removed and one added line; mark the
// words that differ so the edit is visible without comparing the two paragraphs by eye.
function pairWords(removed: DiffRow, added: DiffRow) {
  const changes = diffWordsWithSpace(removed.text, added.text)
  const shared = changes
    .filter((c) => !c.added && !c.removed)
    .reduce((n, c) => n + c.value.length, 0)
  if (shared < MIN_SHARED * Math.max(removed.text.length, added.text.length)) return
  removed.parts = joinMarks(
    changes.filter((c) => !c.added).map((c) => ({ text: c.value, changed: c.removed })),
  )
  added.parts = joinMarks(
    changes.filter((c) => !c.removed).map((c) => ({ text: c.value, changed: c.added })),
  )
}

// The word diff keeps whichever space it can align, so "Rent, set x, and" → "Rent and" marks
// "," and "set x, " apart with a kept space between; one edit should read as one mark.
function joinMarks(parts: WordPart[]): WordPart[] {
  const out: WordPart[] = []
  parts.forEach((part, i) => {
    const bridge =
      !part.changed && !part.text.trim() && parts[i - 1]?.changed && parts[i + 1]?.changed
    const changed = part.changed || Boolean(bridge)
    const last = out[out.length - 1]
    if (last && last.changed === changed) last.text += part.text
    else out.push({ text: part.text, changed })
  })
  return out
}

function diffRows(before: string, after: string): DiffRow[][] {
  const blocks = diffLines(before, after).map((hunk) => {
    const kind: DiffKind = hunk.added ? 'added' : hunk.removed ? 'removed' : 'context'
    return splitLines(hunk.value).map((text): DiffRow => ({ kind, text }))
  })
  blocks.forEach((removed, i) => {
    const added = blocks[i + 1]
    if (removed[0]?.kind !== 'removed' || added?.[0]?.kind !== 'added') return
    if (added.length === removed.length) removed.forEach((row, j) => pairWords(row, added[j]!))
  })
  return blocks
}

// Keep `size` lines of context around every change; fold the rest into runs.
function collapseContext(rows: DiffRow[], size = 3): DiffItem[] {
  const items: DiffItem[] = []
  rows.forEach((row, i) => {
    const near = rows.slice(Math.max(0, i - size), i + size + 1).some((r) => r.kind !== 'context')
    const last = items[items.length - 1]
    if (near) items.push({ type: 'line', row })
    else if (last?.type === 'collapsed') last.rows.push(row)
    else items.push({ type: 'collapsed', rows: [row] })
  })
  return items
}

/** A line diff with its unchanged stretches folded; each expands on a tap. */
function TextDiff({ before, after }: { before: string; after: string }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const items = useMemo(() => collapseContext(diffRows(before, after).flat()), [before, after])
  return (
    <ApprovalDiff>
      {items.map((item, i) => {
        if (item.type === 'line') {
          return (
            <ApprovalDiffLine key={i} kind={item.row.kind}>
              {item.row.parts
                ? item.row.parts.map((part, j) =>
                    part.changed ? (
                      <ApprovalDiffWord key={j}>{part.text}</ApprovalDiffWord>
                    ) : (
                      part.text
                    ),
                  )
                : item.row.text}
            </ApprovalDiffLine>
          )
        }
        if (expanded[i]) {
          return item.rows.map((row, j) => (
            <ApprovalDiffLine key={`${i}-${j}`} kind="context">
              {row.text}
            </ApprovalDiffLine>
          ))
        }
        const n = item.rows.length
        return (
          <ApprovalDiffLine key={i} kind="context">
            <ApprovalToggle onClick={() => setExpanded((prev) => ({ ...prev, [i]: true }))}>
              Show {n} unchanged line{n === 1 ? '' : 's'}
            </ApprovalToggle>
          </ApprovalDiffLine>
        )
      })}
    </ApprovalDiff>
  )
}

export function PermissionArgumentsPreview({ change }: { change: ProposedChange }) {
  const args = permissionArguments(change)
  const identifiers = Object.entries(args).filter(
    ([key, value]) =>
      key !== 'changes' && key !== 'prompt' && value !== '' && typeof value !== 'object',
  )

  return (
    <>
      <ArgumentRows entries={identifiers} />
      {permissionTextEdits(change).map((edit) => (
        <ApprovalSection key={edit.path} title={edit.path.replace(/\./g, ' › ')}>
          <TextDiff before={edit.old} after={edit.new} />
        </ApprovalSection>
      ))}
      <RawArguments value={args} />
    </>
  )
}

// One automation field: a short value is a before → after row; text and structures get a
// line diff under a muted field label.
function AutomationFieldDiffView({
  fieldName,
  oldVal,
  newVal,
}: {
  fieldName: string
  oldVal: unknown
  newVal: unknown
}) {
  const structured = !isScalar(oldVal) || !isScalar(newVal)
  const toText = (v: unknown) =>
    structured ? JSON.stringify(v ?? null, null, 2) : v == null ? '' : String(v)
  return (
    <>
      <ApprovalMeta items={[humanizeType(fieldName)]} />
      <TextDiff before={toText(oldVal)} after={toText(newVal)} />
    </>
  )
}

const isShort = (v: unknown) =>
  isScalar(v) && !(typeof v === 'string' && (v.includes('\n') || v.length > INLINE_MAX))

function AutomationChanges({ changes }: { changes: Record<string, AutomationFieldChange> }) {
  const fields = Object.entries(changes)
  const short = fields.filter(([, c]) => isShort(c.old) && isShort(c.new))
  const long = fields.filter(([, c]) => !(isShort(c.old) && isShort(c.new)))
  if (fields.length === 0) return <ApprovalNote>No changes</ApprovalNote>
  return (
    <>
      {short.length > 0 && (
        <ApprovalFacts>
          {short.map(([field, c]) => (
            <ApprovalChange
              key={field}
              label={humanizeType(field)}
              before={scalarText(c.old)}
              after={scalarText(c.new)}
            />
          ))}
        </ApprovalFacts>
      )}
      {long.map(([field, c]) => (
        <AutomationFieldDiffView key={field} fieldName={field} oldVal={c.old} newVal={c.new} />
      ))}
    </>
  )
}

// A budget write's gate card (`_budget_confirm.NAMED_ARGS_PREVIEW`): the call's arguments
// with each envelope / group named instead of its id, a budget amount as money.
const budgetAmount: Format = (key, value) =>
  key === 'amount' && typeof value === 'number' ? fBase(value) : value

function NamedArgsPreview({ preview }: { preview: ToolPreview }) {
  const entries = namedEntries(isRecord(preview.data) ? preview.data : {}, budgetAmount)
  return (
    <>
      {entries.length > 0 ? (
        <ArgumentRows entries={entries} format={budgetAmount} />
      ) : (
        <ApprovalNote tone="muted">No arguments</ApprovalNote>
      )}
      {preview.raw_arguments && <RawArguments value={preview.raw_arguments} />}
    </>
  )
}

const PREVIEW_RENDERERS: Record<string, (preview: ToolPreview) => ReactElement> = {
  field_diff: (preview) => <FieldDiffPreview preview={preview} />,
  named_args: (preview) => <NamedArgsPreview preview={preview} />,
}

export function ToolPreviewBody({ preview }: { preview: ToolPreview }) {
  const renderer = PREVIEW_RENDERERS[preview.kind]
  return renderer ? renderer(preview) : <GenericPreviewBody preview={preview} />
}

function FieldDiffPreview({ preview }: { preview: ToolPreview }) {
  const updates = (
    Array.isArray(preview.data?.items) ? preview.data.items : []
  ) as AutomationUpdateDisplay[]

  return (
    <>
      {previewHasNoChanges(preview) ? (
        <ApprovalNote>The proposed values match the current automation</ApprovalNote>
      ) : (
        updates.map((update, i) => (
          <ApprovalSection
            key={update.id || i}
            title={update.name || update.id || `Automation ${i + 1}`}
          >
            <AutomationChanges changes={update.changes || {}} />
          </ApprovalSection>
        ))
      )}
      <RawArguments value={preview.raw_arguments ?? {}} />
    </>
  )
}

// A preview kind this card has no renderer for: its data reads like any other arguments.
function GenericPreviewBody({ preview }: { preview: ToolPreview }) {
  const entries = Object.entries(preview.data ?? {}).filter(
    ([, value]) => value != null && value !== '',
  )
  return (
    <>
      {entries.length > 0 ? (
        <ArgumentRows entries={entries} />
      ) : (
        <ApprovalNote>No preview available</ApprovalNote>
      )}
      {preview.raw_arguments && <RawArguments value={preview.raw_arguments} />}
    </>
  )
}
