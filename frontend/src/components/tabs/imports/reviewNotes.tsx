import { useState } from 'react'
import { Button, Cluster } from '../../ui'
import type { BudgetAppInfo } from '../automations/datasync/budgetApps'
import type { Preview, PreviewCategory } from './runs'

// What the import does to this book beyond adding the plan — each one sentence, only when true.
// An app that settles types shows more of them, so its informational lines fold after the first
// few; what stops the import is never folded.

const SHOWN_NOTES = 3
const MANY_ASKS = 3

function lines(app: BudgetAppInfo, p: Preview, asking: PreviewCategory[]) {
  const pruned = [...p.prune.categories, ...p.prune.groups]
  return {
    asks: asking.map(
      (c) =>
        `${app.name}’s ${c.name} matches more than one of your categories. Pick one in Categories.`,
    ),
    blocked: p.currency_blocked
      ? [
          `Can’t import: this book is in ${p.base_currency} and already has data, and the ${app.planWord} is in ${p.currency}. Import into an empty book, or one in ${p.currency}.`,
        ]
      : [],
    foreign:
      p.book_state === 'foreign' && !p.currency_blocked
        ? [
            `This book already has accounts of its own. Each ${app.name} account comes in as a new one unless you pick one of yours under In this book.`,
          ]
        : [],
    rerun:
      p.book_state === 'rerun'
        ? [`This ${app.planWord} was imported before; importing again adds only what is new.`]
        : [],
    change: p.currency_change
      ? [`The book’s base currency changes from ${p.base_currency} to ${p.currency}.`]
      : [],
    prune:
      pruned.length > 0
        ? [
            `The starter categories the app came with that you never used are removed, since ${app.name} brings its own: ${pruned.join(', ')}. To keep one, pick it for ${app.aName} category in Categories.`,
          ]
        : [],
  }
}

export function Notes({
  app,
  preview: p,
  asking,
}: {
  app: BudgetAppInfo
  preview: Preview
  asking: PreviewCategory[]
}) {
  const l = lines(app, p, asking)
  const list: { text: string; bad?: boolean }[] = [
    ...l.asks.map((text) => ({ text, bad: true })),
    ...l.blocked.map((text) => ({ text, bad: true })),
    ...[...l.foreign, ...l.rerun, ...l.change, ...l.prune, ...p.warnings].map((text) => ({ text })),
  ]
  if (list.length === 0) return null
  return (
    <ul className="ynr-notes">
      {list.map((n) => (
        <li key={n.text} className={n.bad ? 'ynr-note ynr-note--bad' : 'ynr-note'}>
          {n.text}
        </li>
      ))}
    </ul>
  )
}

export const TYPES_NOTE_ID = 'ynr-types-note'
export const BLOCKED_NOTE_ID = 'ynr-blocked-note'

/** The sentence over a review with types to settle, and the button that settles the suggested. */
function TypesLine(props: {
  app: BudgetAppInfo
  unsettled: number
  suggested: number
  onAccept: () => void
}) {
  const { app, unsettled, suggested } = props
  const one = unsettled === 1
  return (
    <li id={TYPES_NOTE_ID} className="ynr-note ynr-note--bad">
      <Cluster justify="between" gap="--space-3">
        <span>
          {app.name} doesn’t say what kind each account is. {unsettled} still{' '}
          {one ? 'needs a type: check it' : 'need a type: check them'} in Accounts
          {suggested > 0 ? `, or accept the ${suggested === 1 ? 'suggestion' : 'suggestions'}` : ''}
          .
        </span>
        {suggested > 0 && (
          <Button size="sm" variant="secondary" className="tap-target" onClick={props.onAccept}>
            {suggested === 1 ? 'Accept suggestion' : 'Accept suggestions'}
          </Button>
        )}
      </Cluster>
    </li>
  )
}

export function SettleNotes({
  app,
  preview: p,
  asking,
  blocked,
  unsettled,
  suggested,
  onAccept,
}: {
  app: BudgetAppInfo
  preview: Preview
  asking: PreviewCategory[]
  /** What the server would refuse this import with right now. */
  blocked: string | null
  unsettled: number
  suggested: number
  onAccept: () => void
}) {
  const [allShown, setAllShown] = useState(false)
  const l = lines(app, p, asking)
  const asks =
    l.asks.length > MANY_ASKS
      ? [
          `${l.asks.length} ${app.name} categories match more than one of yours. Pick one for each in Categories.`,
        ]
      : l.asks
  const stops = [...asks, ...l.blocked]
  const notes = [...l.change, ...l.foreign, ...p.warnings, ...l.rerun, ...l.prune]
  const shown = allShown ? notes : notes.slice(0, SHOWN_NOTES)
  const more = notes.length - SHOWN_NOTES
  if (!blocked && unsettled === 0 && stops.length === 0 && notes.length === 0) return null
  return (
    <>
      <ul className="ynr-notes">
        {blocked && (
          <li id={BLOCKED_NOTE_ID} className="ynr-note ynr-note--bad">
            {blocked}
          </li>
        )}
        {unsettled > 0 && (
          <TypesLine app={app} unsettled={unsettled} suggested={suggested} onAccept={onAccept} />
        )}
        {stops.map((text) => (
          <li key={text} className="ynr-note ynr-note--bad">
            {text}
          </li>
        ))}
        {shown.map((text) => (
          <li key={text} className="ynr-note">
            {text}
          </li>
        ))}
      </ul>
      {more > 0 && (
        <div>
          <button
            type="button"
            className="a3-linkbtn ynr-more"
            onClick={() => setAllShown(!allShown)}
          >
            {allShown
              ? 'Show fewer notes'
              : more === 1
                ? 'Show 1 more note'
                : `Show ${more} more notes`}
          </button>
        </div>
      )}
    </>
  )
}
