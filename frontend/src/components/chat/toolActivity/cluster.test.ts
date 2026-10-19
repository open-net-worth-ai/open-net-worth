// The grouping rules. This is where the bugs live, so this is where the tests are.
//
// Two properties matter more than the rest and are asserted directly: the ROW order is the
// timeline (never reordered, never merged across a gap), and a FAILURE is never counted into
// a cluster or omitted from the summary.

import { describe, expect, it } from 'vitest'
import type { ToolDisplay } from '../../../types/chat'
import {
  clusterItems,
  displayOf,
  failureOf,
  summarize,
  summaryParts,
  type ToolItem,
} from './cluster'

let seq = 0

function display(over: Partial<ToolDisplay> = {}): ToolDisplay {
  return {
    label: 'Read transactions',
    verb: 'read',
    noun: 'transactions',
    owner_id: 'core',
    owner_name: '',
    detail: '',
    ...over,
  }
}

function tool(over: Partial<ToolItem> = {}): ToolItem {
  return {
    type: 'tool',
    name: 'get_transactions',
    id: `t${seq++}`,
    status: 'done',
    display: display(),
    result: '{"ok":true}',
    ...over,
  }
}

describe('clusterItems', () => {
  it('makes one row per call — adjacent identical calls are NOT merged', () => {
    // They were merged into a counted row until 2026-08-23. That row restated what the bar
    // above it already said, and expanding it opened a nested third level listing the calls
    // it stood for. Aggregation is the bar's job; a row is a call.
    const rows = clusterItems([tool(), tool(), tool()])
    expect(rows).toHaveLength(3)
    expect(rows.map((r) => r.label)).toEqual([
      'Read transactions',
      'Read transactions',
      'Read transactions',
    ])
    expect(rows.every((r) => r.items.length === 1)).toBe(true)
  })

  it('keeps each call its own row in the order it happened', () => {
    const other = { display: display({ verb: 'browse', noun: 'pages', label: 'Open a page' }) }
    const rows = clusterItems([tool(), tool(other), tool()])
    expect(rows.map((r) => r.verb)).toEqual(['read', 'browse', 'read'])
  })

  it("keeps a single call's own detail on its row", () => {
    const d = display({ verb: 'browse', noun: 'pages', label: 'Open a.test', detail: 'a.test' })
    expect(clusterItems([tool({ display: d })])[0]!.detail).toBe('a.test')
  })

  it('marks a failed call on its own row', () => {
    const rows = clusterItems([tool(), tool({ result: '{"error":"account not found"}' }), tool()])
    expect(rows).toHaveLength(3)
    expect(rows[1]!.failed).toBe(true)
    expect(rows[1]!.errorText).toBe('account not found')
    expect(rows[1]!.verb).toBe('error')
  })

  it('marks a row busy while its call is still running', () => {
    expect(clusterItems([tool({ status: 'executing', result: undefined })])[0]!.busy).toBe(true)
    expect(clusterItems([tool()])[0]!.busy).toBe(false)
  })
})

describe('failureOf', () => {
  it('reads the S2 error contract', () => {
    expect(failureOf(tool({ result: '{"error":"nope"}' }))).toEqual({
      failed: true,
      message: 'nope',
    })
    expect(failureOf(tool({ result: '{"ok":false,"message":"refused"}' }))).toEqual({
      failed: true,
      message: 'refused',
    })
  })

  it('does not call a plain-text or successful result a failure', () => {
    expect(failureOf(tool({ result: 'just some text' })).failed).toBe(false)
    // A call the owner denied did not run, but it did not FAIL either.
    expect(
      failureOf(tool({ result: '{"ok":false,"declined_by":"user","error":"THE USER DECLINED"}' }))
        .failed,
    ).toBe(false)
    expect(failureOf(tool({ result: '{"ok":true}' })).failed).toBe(false)
    expect(failureOf(tool({ result: '{"error":null}' })).failed).toBe(false)
  })

  it('reads a plain-text error, which is the only shape an external MCP tool returns', () => {
    // Owner report 2026-08-22 (IBKR MCP): these read as successes — no badge, no message,
    // absent from the failed count, and free to cluster with calls that actually worked.
    expect(failureOf(tool({ result: 'Error: Authentication required to create alerts.' }))).toEqual(
      { failed: true, message: 'Authentication required to create alerts.' },
    )
    expect(failureOf(tool({ result: 'Tool error — no such tool' })).failed).toBe(true)
  })

  it('only treats an error announced at the very START as a failure', () => {
    // A result is allowed to DISCUSS errors; matching anywhere would relabel working calls.
    expect(
      failureOf(tool({ result: 'Found 3 log lines containing Error: disk full' })).failed,
    ).toBe(false)
  })

  it('a call with no result yet has not failed', () => {
    expect(failureOf(tool({ status: 'executing', result: undefined })).failed).toBe(false)
  })
})

describe('summarize', () => {
  it('aggregates the WHOLE group, so interleaved work still reads as one sentence', () => {
    const nav = display({ verb: 'browse', noun: 'pages', label: 'Opened a.test' })
    const shot = display({ verb: 'capture', noun: 'screenshots', label: 'Took a screenshot' })
    const clusters = clusterItems([
      tool({ display: nav }),
      tool({ display: shot }),
      tool({ display: nav }),
      tool({ display: shot }),
    ])
    // Four rows, one per call...
    expect(clusters).toHaveLength(4)
    // ...one aggregate sentence.
    expect(summarize(clusters)).toBe('Opened 2 pages, took 2 screenshots')
  })

  it('always reports failures — a summary that hid them is the one place nobody looks twice', () => {
    const browse = display({ verb: 'browse', noun: 'pages', label: 'Opened a page' })
    const clusters = clusterItems([
      tool(),
      tool({ display: browse }),
      tool({ result: '{"error":"boom"}' }),
    ])
    expect(summarize(clusters)).toBe('Read transactions, opened pages • 1 failed')
  })

  it('keeps the phrase even when one row would say the same words', () => {
    // It used to be dropped as a restatement (owner report, 2026-08-22: the same words once
    // per row AND again above them). But the group folds by default now, so the bar is
    // usually the only thing on screen — dropping the phrase left it reading "1 step", which
    // says nothing about what ran (owner report, 2026-08-25).
    const clusters = clusterItems([tool(), tool({ result: '{"error":"boom"}' })])
    expect(summaryParts(clusters)).toEqual({ phrase: 'Read transactions', failed: '1 failed' })
    expect(summarize(clusters)).toBe('Read transactions • 1 failed')
  })

  it('counts repeated calls in the phrase, since the rows no longer do', () => {
    const browse = display({ verb: 'browse', noun: 'pages', label: 'Open a page' })
    const clusters = clusterItems([tool(), tool(), tool({ display: browse })])
    expect(summarize(clusters)).toBe('Read 2 transactions, open pages')
  })

  it('caps at three phrases rather than running on', () => {
    const mk = (n: string) => display({ noun: n, label: `Read ${n}` })
    const clusters = clusterItems(['a', 'b', 'c', 'd', 'e'].map((n) => tool({ display: mk(n) })))
    expect(summarize(clusters)).toBe('Read a, read b, read c +2 more')
  })

  it('is empty when there is nothing to say', () => {
    expect(summarize([])).toBe('')
  })
})

describe('displayOf', () => {
  it('typesets the raw name for a client talking to an older backend', () => {
    const d = displayOf(
      tool({ display: undefined, name: 'mcp__interactive-brokers-mcp__get_positions' }),
    )
    // Typography, not vocabulary — the same words the name already contains.
    expect(d.label).toBe('Get positions')
    expect(d.noun).toBe('positions')
    // No verb class and no owner are GUESSED: the row shows the neutral glyph and no
    // breadcrumb rather than claiming something nobody can check.
    expect(d.verb).toBe('unknown')
    expect(d.owner_id).toBe('core')
  })

  it('never prints a raw mcp__ name, which is what version skew used to look like', () => {
    for (const name of ['mcp__gmail__search_threads', 'mcp__a__b__c', 'plain_tool']) {
      expect(displayOf(tool({ display: undefined, name })).label).not.toContain('mcp__')
      expect(displayOf(tool({ display: undefined, name })).label).not.toContain('_')
    }
  })
})
