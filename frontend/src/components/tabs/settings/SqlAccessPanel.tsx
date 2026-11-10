// Settings → Developer → SQL access. The owner's half of the read-only SQL capability
// (spec docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md).
//
// TWO things, deliberately together: the kill switch, and the record of what Lucius has
// actually asked the database. A switch with no visibility is a switch nobody has any basis
// to flip — the log is what makes the toggle an informed decision rather than a mood.
//
// LAYOUT NOTE, learned the hard way. `.rd-panel-body > *` already gives EVERY direct child
// its own padding + bottom border (settings.css). So a child is a SECTION, and wrapping
// content in a div to "group" it produces a box inside a box. The first version of this panel
// wrapped the query list in a div AND rendered each statement in `.rd-code` — which is built
// for multi-line config snippets (`white-space: pre; overflow-x: auto`, border, 14px padding)
// — giving three nested borders and a horizontal scrollbar per row. Each query is now its own
// direct child, styled as a dense log line, and `.rd-code` is left to the MCP snippets it was
// designed for.
//
// Lives in its OWN file rather than inline in sections/DeveloperSection.tsx (where the other
// five panels live) because that file is already ~615 lines, past the ~400 guidance.
//
// The log rows carry SQL VERBATIM — see backend/db/sql_query_log_store.py for why that is the
// right call for THIS log. Two consequences here: the endpoint is auth-gated + no-store, and
// the list is data-ctx-secret so a statement embedding real amounts or account names never
// rides into the Lucius screen snapshot (same treatment as LogViewer and the MCP token).
import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { useCachedFetchState } from '../../../state/queryCache'
import { fDate } from '../../../utils/format'
import { Badge, Btn, Empty, Panel, Toggle, row, useSectionBusy } from './ds'
import type { SectionProps } from './ds'

export interface SqlQueryRow {
  id: string
  ts: string
  sql: string
  ok: boolean
  error: string | null
  row_count: number
  truncated: boolean
  elapsed_ms: number
  origin: string
}

const LOG_PATH = '/api/sql-queries?limit=25'
const CONFIG_PATH = '/api/config/sql-query'

async function getJson<T>(path: string): Promise<T> {
  const r = await authFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

/** "3m ago" / "2h ago" / a date once it stops being recent. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return ''
  const secs = Math.max(0, Math.round((now - then) / 1000))
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return fDate(new Date(then), 'numeric')
}

/** One query, as a log line. Collapsed by default; click to read the whole statement.
 *
 * Collapsed truncates with an ellipsis rather than scrolling horizontally: a scrollbar per row
 * is noise, and a SELECT's first 80 characters already identify it. Expanded WRAPS rather than
 * scrolls, so the full statement is readable without dragging. */
// `data-ctx-secret` sits on the ROW, not on a wrapper around the list: a WHERE clause can
// embed real amounts and account names and must stay out of the Lucius screen snapshot, and a
// wrapper would stop each row being a DIRECT child of the panel body — which is exactly what
// gives it its padding and divider.
function QueryRow({ q }: { q: SqlQueryRow }) {
  const [open, setOpen] = useState(false)
  return (
    <button
      type="button"
      className="sqla-q"
      aria-expanded={open}
      data-ctx-secret=""
      onClick={() => setOpen((v) => !v)}
      title={open ? 'Collapse' : 'Show the full statement'}
    >
      <span className={'sqla-dot' + (q.ok ? ' ok' : ' bad')} aria-hidden="true" />
      <span className="sqla-body">
        <span className={'sqla-sql' + (open ? ' open' : '')}>{q.sql}</span>
        {!q.ok && q.error ? <span className="sqla-err">{q.error}</span> : null}
      </span>
      <span className="rd-row-meta sqla-meta">
        {q.ok ? (
          <span>
            {q.row_count} row{q.row_count === 1 ? '' : 's'}
            {q.truncated ? ' (capped)' : ''}
          </span>
        ) : (
          <span>refused</span>
        )}
        <span>{Math.round(q.elapsed_ms)} ms</span>
        {/* Origin is shown ONLY when it is not the owner's own browser. "web" on every row is
            noise; "scheduled" or "connector:telegram" is exactly what you want to notice. */}
        {q.origin && q.origin !== 'web' ? <span>{q.origin}</span> : null}
        <span>{relativeTime(q.ts)}</span>
      </span>
    </button>
  )
}

/** `available` is false only on Postgres, when the app could not prepare the separate
 * database login Lucius's SQL runs as; `fix` is set when only an operator can resolve it. */
interface SqlConfig {
  enabled: boolean
  available?: boolean
  reason?: string
  fix?: string
}

export function SqlAccessPanel({ toast }: SectionProps) {
  const config = useCachedFetchState<SqlConfig>(
    CONFIG_PATH,
    () => getJson<SqlConfig>(CONFIG_PATH),
    {
      staleTime: 0,
    },
  )
  const log = useCachedFetchState<{ queries: SqlQueryRow[] }>(
    LOG_PATH,
    () => getJson<{ queries: SqlQueryRow[] }>(LOG_PATH),
    { staleTime: 5_000 },
  )
  useSectionBusy(config.initialLoading && !config.data)

  const enabled = config.data?.enabled ?? true
  const unavailable = config.data?.available === false
  const fix = config.data?.fix ?? ''
  const queries = log.data?.queries ?? []

  async function setEnabled(next: boolean) {
    try {
      const r = await authFetch(CONFIG_PATH, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      void config.refresh()
      toast(next ? 'SQL access enabled' : 'SQL access blocked')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  return (
    <Panel
      title="SQL access"
      sub="Lucius can read this database directly — read-only, and never your credentials"
      actions={
        <Btn
          size="xs"
          variant="ghost"
          onClick={() => {
            void log.refresh()
            toast('Refreshed')
          }}
        >
          Refresh
        </Btn>
      }
    >
      {/* Each child below is its own bordered section — see the layout note at the top. */}
      {row(
        'Direct SQL reads',
        enabled
          ? 'Lucius may run a single read-only SELECT for questions the ordinary tools cannot express. Writes and credential tables are refused by the database itself.'
          : 'Blocked. Lucius falls back to the ordinary read tools.',
        <Toggle
          on={enabled}
          disabled={!config.data}
          onChange={(v) => void setEnabled(v)}
          label="Direct SQL reads"
        />,
      )}

      {enabled && unavailable
        ? row(
            'Not working on this database',
            <>
              {`Lucius can't run SQL right now: ${config.data?.reason ?? ''}`}
              {fix
                ? ' Run this once as a database admin; it starts working within a minute, no restart needed.'
                : null}
            </>,
            <Badge tone="warn">Unavailable</Badge>,
          )
        : null}
      {enabled && unavailable && fix ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <code className="rd-code" style={{ flex: 1, minWidth: 0 }}>
            {fix}
          </code>
          <Btn
            size="xs"
            onClick={() => {
              void navigator.clipboard
                .writeText(fix)
                .then(() => toast('Copied'))
                .catch(() => toast('Could not copy — select the command manually'))
            }}
          >
            Copy
          </Btn>
        </div>
      ) : null}

      {log.error && !log.data ? (
        <Empty
          title="Could not load the query log"
          body="The server did not answer."
          action={
            <Btn size="xs" onClick={() => void log.refresh()}>
              Retry
            </Btn>
          }
        />
      ) : log.initialLoading && !log.data ? (
        <span className="rd-help">Loading…</span>
      ) : queries.length === 0 ? (
        <Empty
          title="No queries yet"
          body={
            enabled
              ? "Nothing has been asked of the database directly. Ask Lucius something the ordinary tools can't answer and it will show up here."
              : 'SQL access is off, so nothing can run.'
          }
        />
      ) : (
        /* NOT wrapped in a div. `.rd-panel-body > *` gives every DIRECT child its padding
           and bottom divider, and `display: contents` on a wrapper does not help — it
           removes the wrapper's box, so the padding that rule applied is discarded and the
           children end up flush against the panel edge. An array renders its items as
           siblings, so each label/row below is a real direct child and is styled by the
           panel exactly like the toggle row above it. */
        <>
          <div className="rd-bars-lbl">Recent queries</div>
          {queries.map((q) => (
            <QueryRow key={q.id} q={q} />
          ))}
        </>
      )}
    </Panel>
  )
}
