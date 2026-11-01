import { Badge, CollapsibleSection, Stack, Text } from '../../ui'
import type { Check, Counts, Problem, Verification } from './runs'

// The tables of a run report. Rows are `<tr>`s, but on a phone each one stacks into labelled
// lines (`.imp-table` in index.css, from the `data-label` of each cell).

const ITEMS: { key: string; label: string }[] = [
  { key: 'accounts', label: 'Accounts' },
  { key: 'categories', label: 'Categories' },
  { key: 'envelopes', label: 'Envelopes' },
  { key: 'transactions', label: 'Transactions' },
  { key: 'assigned', label: 'Assigned months' },
]

const fmt = (v: number | string) =>
  typeof v === 'number'
    ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : v

const COUNT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

// A transaction count is a whole number; only money carries two decimals.
const show = (v: number | string, unit: Check['unit']) =>
  unit === 'count' && typeof v === 'number' ? COUNT.format(v) : fmt(v)

const signed = (v: number, unit: Check['unit']) => `${v > 0 ? '+' : ''}${show(v, unit)}`

export function ResultsTable({ counts }: { counts: Record<string, Counts> }) {
  const rows = ITEMS.flatMap((i) => {
    const c = counts[i.key]
    return c ? [{ ...i, c }] : []
  })
  if (rows.length === 0) {
    return (
      <Text size="sm" tone="faint">
        Nothing booked yet.
      </Text>
    )
  }
  return (
    <table className="imp-table">
      <thead>
        <tr>
          <th>Item</th>
          <th className="imp-table__num">Created</th>
          <th className="imp-table__num">Already there</th>
          <th className="imp-table__num">Refused</th>
          <th className="imp-table__num">Failed</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ key, label, c }) => (
          <tr key={key}>
            <td data-label="Item">{label}</td>
            <td data-label="Created" className="imp-table__num">
              {c.created}
            </td>
            <td data-label="Already there" className="imp-table__num">
              {c.existing}
            </td>
            <td data-label="Refused" className="imp-table__num">
              {c.refused}
            </td>
            <td data-label="Failed" className="imp-table__num">
              {c.failed}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function ProblemList({ problems }: { problems: Problem[] }) {
  return (
    <Stack gap="--space-3">
      {problems.map((p, i) => (
        <div key={`${p.item}-${i}`}>
          <Text size="sm" weight={600}>
            {p.item}
          </Text>
          {p.reason && (
            <Text as="div" size="sm">
              {p.reason}
            </Text>
          )}
          {p.hint && (
            <Text as="div" size="xs" tone="faint">
              {p.hint}
            </Text>
          )}
        </div>
      ))}
    </Stack>
  )
}

function ChecksTable({ checks }: { checks: Check[] }) {
  return (
    <table className="imp-table">
      <thead>
        <tr>
          <th>Check</th>
          <th className="imp-table__num">YNAB</th>
          <th className="imp-table__num">Open Net Worth</th>
          <th className="imp-table__num">Difference</th>
        </tr>
      </thead>
      <tbody>
        {checks.map((c, i) => (
          <tr key={`${c.check}-${i}`}>
            <td data-label="Check" className="imp-table__wrap">
              {c.check}
              {c.note && (
                <Text as="div" size="2xs" tone="faint">
                  {c.note}
                </Text>
              )}
            </td>
            <td data-label="YNAB" className="imp-table__num">
              {show(c.ynab_value, c.unit)}
            </td>
            <td data-label="Open Net Worth" className="imp-table__num">
              {show(c.onw_value, c.unit)}
            </td>
            <td data-label="Difference" className="imp-table__num">
              {c.difference == null ? '—' : signed(c.difference, c.unit)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// Mismatches come first; the checks that agree stay one click away.
export function VerificationBody({ verification: v }: { verification: Verification }) {
  return (
    <Stack gap="--space-4">
      <Badge tone={v.ok ? 'pos' : 'neg'}>
        {v.ok ? 'All match' : `${v.mismatches.length} to look at`}
      </Badge>
      {v.mismatches.length > 0 && <ChecksTable checks={v.mismatches} />}
      {!v.ok && (
        <Text size="xs" tone="faint">
          A transaction count that differs usually means a row was not booked (listed under Needs
          attention). An envelope or Ready to Assign difference means the two apps&apos; budget
          rules gave different numbers for your history; compare that month in both before relying
          on it.
        </Text>
      )}
      {v.matches.length > 0 && (
        <CollapsibleSection title={`Show all ${v.matches.length} that match`}>
          <ChecksTable checks={v.matches} />
        </CollapsibleSection>
      )}
    </Stack>
  )
}
