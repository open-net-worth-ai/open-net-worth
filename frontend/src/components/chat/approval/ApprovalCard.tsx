import type { ReactNode } from 'react'

/** The approval-card design system (docs/APPROVAL_CARDS.md). A card body is, top to
 * bottom: ONE lead line, an optional muted meta line, fact rows, and details behind a tap.
 * Every piece renders at the card's one text size — emphasis is a `tone` (weight/colour),
 * never a font size — so two cards built from these can't drift apart. */

export type Tone = 'strong' | 'good' | 'bad' | 'warn' | 'muted'

const toneClass = (tone?: Tone) => (tone ? ` ac-tone-${tone}` : '')

/** What happens, in one line; the key figure (a total, an amount) right-aligned. A
 * destructive write (delete, remove) takes `tone="bad"`. */
export function ApprovalLead({
  children,
  figure,
  tone,
}: {
  children: ReactNode
  figure?: ReactNode
  tone?: Tone
}) {
  return (
    <div className={`ac-lead${toneClass(tone)}`}>
      <span>{children}</span>
      {figure != null && <span className="ac-lead-figure">{figure}</span>}
    </div>
  )
}

/** Secondary facts in one muted line, joined by " · ". Falsy items are dropped. */
export function ApprovalMeta({ items }: { items: Array<ReactNode | false | null | undefined> }) {
  const shown = items.filter((i): i is ReactNode => Boolean(i))
  if (shown.length === 0) return null
  return <div className="ac-meta">{shown.flatMap((item, i) => (i ? [' · ', item] : [item]))}</div>
}

export function ApprovalFacts({ children }: { children: ReactNode }) {
  return <div className="ac-facts">{children}</div>
}

/** One label → value row. `title` carries any explanation (a hover), so the card body never
 * grows a paragraph. */
export function ApprovalFact({
  label,
  children,
  tone,
  title,
}: {
  label: ReactNode
  children: ReactNode
  tone?: Tone
  title?: string
}) {
  return (
    <div className="ac-fact">
      <span className="ac-fact-label">{label}</span>
      <span
        className={`ac-fact-value${toneClass(tone)}${title ? ' ac-has-title' : ''}`}
        title={title}
      >
        {children}
      </span>
    </div>
  )
}

/** One label row for an edit: the old value, struck and muted, then the new one. Place it
 * inside ApprovalFacts like any fact. */
export function ApprovalChange({
  label,
  before,
  after,
}: {
  label: ReactNode
  before: ReactNode
  after: ReactNode
}) {
  return (
    <div className="ac-fact">
      <span className="ac-fact-label">{label}</span>
      <span className="ac-fact-value">
        <span className="ac-before">{before}</span>
        <span className="ac-arrow"> → </span>
        {after}
      </span>
    </div>
  )
}

/** Supporting detail (a lot table, raw arguments) folded behind a one-line summary. The
 * summary must still carry the decision-relevant fact ("3 lots · all long-term"). */
export function ApprovalDetails({
  summary,
  children,
  open,
}: {
  summary: ReactNode
  children: ReactNode
  open?: boolean
}) {
  return (
    <details className="ac-details" open={open}>
      <summary>{summary}</summary>
      {children}
    </details>
  )
}

/** A compact table — column headings in caps, cells at the card's one size. Use it inside
 * ApprovalDetails unless the rows ARE the change (an import batch's sample). */
export function ApprovalTable({
  head,
  rows,
  numeric = [],
}: {
  head: ReactNode[]
  rows: Array<{ key: string | number; cells: ReactNode[] }>
  /** Column indexes holding amounts: right-aligned and never wrapped. */
  numeric?: number[]
}) {
  const cls = (i: number) => (numeric.includes(i) ? 'ac-num' : undefined)
  return (
    <div className="ac-table-wrap">
      <table className="ac-table">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className={cls(i)}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              {r.cells.map((c, i) => (
                <td key={i} className={cls(i)}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Source code or raw JSON the user is approving — monospace, scrolls inside the card. */
export function ApprovalCode({ children }: { children: ReactNode }) {
  return (
    <pre className="ac-code">
      <code>{children}</code>
    </pre>
  )
}

export type DiffKind = 'added' | 'removed' | 'context'

/** A line diff container; fill it with ApprovalDiffLine (or a collapsed-context toggle). */
export function ApprovalDiff({ children }: { children: ReactNode }) {
  return <div className="ac-diff">{children}</div>
}

export function ApprovalDiffLine({ kind, children }: { kind: DiffKind; children: ReactNode }) {
  return (
    <div className={`ac-diff-line ac-diff-${kind}`}>
      <span className="ac-diff-gutter">
        {kind === 'added' ? '+' : kind === 'removed' ? '−' : ' '}
      </span>
      <span>{children}</span>
    </div>
  )
}

/** The words that changed inside an ApprovalDiffLine paired with its opposite line. */
export function ApprovalDiffWord({ children }: { children: ReactNode }) {
  return <mark className="ac-diff-word">{children}</mark>
}

/** ONE short line that must be seen before approving (a warning, a consequence). Not for
 * explanations — those go in a fact's `title` and the docs. */
export function ApprovalNote({ children, tone = 'muted' }: { children: ReactNode; tone?: Tone }) {
  return <div className={`ac-note${toneClass(tone)}`}>{children}</div>
}

/** A titled group inside a card that changes several things (several automations, several
 * fields with their own diffs). */
export function ApprovalSection({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <section className="ac-section">
      <div className="ac-section-title">{title}</div>
      {children}
    </section>
  )
}

/** An inline text button inside a card ("show diff", "show 12 unchanged lines"). */
export function ApprovalToggle({
  children,
  onClick,
}: {
  children: ReactNode
  onClick: () => void
}) {
  return (
    <button type="button" className="ac-toggle" onClick={onClick}>
      {children}
    </button>
  )
}
