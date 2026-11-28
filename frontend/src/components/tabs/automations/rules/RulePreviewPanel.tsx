// "Matches N transactions": what the Rule, as it is being edited, would do to the book today —
// each existing match with its before → after, ticked to be applied now. Preview and apply both
// take the unsaved rule, so a rule can be tried on history before it is saved. Past the first
// page, "Apply to all N" applies it to every match the server finds, minus the unticked rows.
import { useEffect, useState } from 'react'
import {
  applyRule,
  applyRuleToAll,
  previewRule,
  type RuleApplyResult,
  type RuleBody,
  type RuleMatch,
  undoRuleApply,
} from '../../../../api/automations'
import { Button, Checkbox } from '../../../ui'
import { describeChanges } from './previewChanges'

const DEBOUNCE_MS = 400
const SHOWN = 50

interface Labels {
  categories: Array<{ id: string; label: string }>
}

function money(match: RuleMatch): string {
  const figure = match.amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return `${match.currency} ${figure}`
}

type Settled =
  | { key: string; matches: RuleMatch[]; total: number; changeable: number; error?: undefined }
  | { key: string; error: string; matches?: undefined; total?: undefined; changeable?: undefined }

export function RulePreviewPanel({
  body,
  labels,
  incompleteHint,
  onApplied,
}: {
  /** The rule as it would be saved; null while it is not complete enough to preview. */
  body: RuleBody | null
  labels: Labels
  incompleteHint: string
  onApplied?: (result: RuleApplyResult) => void
}) {
  const [settled, setSettled] = useState<Settled | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [result, setResult] = useState<RuleApplyResult | null>(null)
  const [undoing, setUndoing] = useState(false)
  const [undone, setUndone] = useState(false)
  const [reload, setReload] = useState(0)
  const key = body ? `${reload}|${JSON.stringify(body)}` : null

  useEffect(() => {
    if (key === null) return
    let current = true
    const timer = window.setTimeout(() => {
      void previewRule(JSON.parse(key.slice(key.indexOf('|') + 1)) as RuleBody, SHOWN)
        .then((response) => {
          if (!current) return
          setSettled({
            key,
            matches: response.matches,
            total: response.total,
            changeable: response.changeable,
          })
          setPicked(
            new Set(
              response.matches
                .filter((m) => m.after && !m.skipped_reason)
                .map((m) => m.transaction_id),
            ),
          )
        })
        .catch((error) => {
          if (!current) return
          setSettled({
            key,
            error: error instanceof Error ? error.message : 'The preview could not be loaded.',
          })
        })
    }, DEBOUNCE_MS)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [key])

  const idle = key === null
  const loading = !idle && settled?.key !== key
  const matches = idle ? [] : (settled?.matches ?? [])
  const total = idle ? undefined : settled?.total
  const changeable = idle ? 0 : (settled?.changeable ?? 0)
  const error = idle ? undefined : settled?.error

  const apply = async (all: boolean) => {
    if (!body) return
    setApplying(true)
    setApplyError(null)
    try {
      const applied = all
        ? await applyRuleToAll(body, excluded, total ?? 0)
        : await applyRule(body, [...picked])
      setUndone(false)
      setResult(applied)
      onApplied?.(applied)
      setReload((n) => n + 1)
    } catch (error) {
      setApplyError(error instanceof Error ? error.message : 'The rule could not be applied.')
      // The book may have moved (the server refuses a stale "all" count): show it as it is now.
      if (all) setReload((n) => n + 1)
    } finally {
      setApplying(false)
    }
  }

  const undo = async () => {
    if (!result?.checkpoint_id) return
    setUndoing(true)
    setApplyError(null)
    try {
      await undoRuleApply(result.checkpoint_id)
      setResult(null)
      setUndone(true)
      setReload((n) => n + 1)
    } catch (error) {
      setApplyError(error instanceof Error ? error.message : 'The change could not be undone.')
    } finally {
      setUndoing(false)
    }
  }

  const toggle = (id: string, on: boolean) =>
    setPicked((current) => {
      const next = new Set(current)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  const selectable = matches.filter((m) => m.after && !m.skipped_reason)
  // The shown rows left unticked (or that can't change); every unseen match goes in. Counts
  // follow `changeable`, so a row the Rule would skip or leave as it is never inflates them.
  const excluded = matches.filter((m) => !picked.has(m.transaction_id)).map((m) => m.transaction_id)
  const unpicked = selectable.filter((m) => !picked.has(m.transaction_id)).length
  const hidden = changeable > selectable.length
  const allCount = changeable - unpicked

  return (
    <section className="rl-preview" aria-label="Matching transactions" aria-busy={loading}>
      <header className="rl-preview-head">
        <h3 className="rl-preview-title">
          {total === undefined
            ? 'Matching transactions'
            : `Matches ${total.toLocaleString()} ${total === 1 ? 'transaction' : 'transactions'}`}
        </h3>
        {loading && <span className="a3-muted">Checking…</span>}
        <span className="grow" />
        {selectable.length > 0 && (
          <Button
            size="sm"
            loading={applying}
            disabled={picked.size === 0}
            onClick={() => void apply(false)}
          >
            Apply to {picked.size}
          </Button>
        )}
        {hidden && allCount > 0 && (
          <Button size="sm" variant="primary" loading={applying} onClick={() => void apply(true)}>
            Apply to all {allCount.toLocaleString()}
          </Button>
        )}
      </header>
      {idle && <p className="a3-muted">{incompleteHint}</p>}
      {error && (
        <div className="a3-inline-error" role="alert">
          {error}
        </div>
      )}
      {applyError && (
        <div className="a3-inline-error" role="alert">
          {applyError}
        </div>
      )}
      {result && (
        <p className="rl-result" role="status">
          Applied to {result.applied} {result.applied === 1 ? 'transaction' : 'transactions'}
          {result.unchanged > 0 ? `, ${result.unchanged} already matched` : ''}
          {result.skipped.length > 0 ? `, ${result.skipped.length} skipped` : ''}.{' '}
          {result.checkpoint_id && result.applied > 0 && (
            <Button size="sm" variant="ghost" loading={undoing} onClick={() => void undo()}>
              Undo
            </Button>
          )}
        </p>
      )}
      {undone && !result && (
        <p className="rl-result" role="status">
          Undone — those transactions are back as they were.
        </p>
      )}
      {total === 0 && (
        <p className="a3-muted">Nothing in your book matches yet. It will apply to new ones.</p>
      )}
      {matches.length > 0 && (
        <div className="rl-table-wrap">
          <table className="rl-table">
            <thead>
              <tr>
                <th scope="col">
                  <span className="ui-visually-hidden">Apply</span>
                </th>
                <th scope="col">Date</th>
                <th scope="col">Transaction</th>
                <th scope="col" className="num">
                  Amount
                </th>
                <th scope="col">Change</th>
              </tr>
            </thead>
            <tbody>
              {matches.map((match) => (
                <PreviewRow
                  key={match.transaction_id}
                  match={match}
                  labels={labels}
                  checked={picked.has(match.transaction_id)}
                  onToggle={(on) => toggle(match.transaction_id, on)}
                />
              ))}
            </tbody>
          </table>
          {total !== undefined && total > matches.length && (
            <p className="a3-muted rl-more">
              Showing the newest {matches.length} of {total.toLocaleString()}. Apply to all also
              changes the ones not shown; Undo reverses it in one step.
            </p>
          )}
        </div>
      )}
    </section>
  )
}

function PreviewRow({
  match,
  labels,
  checked,
  onToggle,
}: {
  match: RuleMatch
  labels: Labels
  checked: boolean
  onToggle: (on: boolean) => void
}) {
  const changes = describeChanges(match, labels)
  const title = match.payee_name || match.description || 'Transaction'
  return (
    <tr className={match.skipped_reason ? 'rl-row skipped' : 'rl-row'}>
      <td>
        <Checkbox
          label={<span className="ui-visually-hidden">Apply to {title}</span>}
          checked={checked}
          disabled={!match.after || !!match.skipped_reason}
          onChange={(event) => onToggle(event.target.checked)}
        />
      </td>
      <td className="rl-date">{match.date}</td>
      <td>
        <span className="rl-title">{title}</span>
        {match.account_name && <span className="a3-muted rl-sub">{match.account_name}</span>}
      </td>
      <td className="num">{money(match)}</td>
      <td>
        {match.skipped_reason ? (
          <span className="rl-skip">Skipped: {match.skipped_reason}</span>
        ) : changes.length === 0 ? (
          <span className="a3-muted">No change</span>
        ) : (
          <ul className="rl-changes">
            {changes.map((change) => (
              <li key={change}>{change}</li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  )
}
