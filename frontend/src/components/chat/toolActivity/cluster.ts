// Grouping tool calls into what a reader actually wants to know.
//
// Two levels, answering two different questions, and the difference matters:
//
//   ROWS are adjacency-only. Consecutive calls sharing a key merge into one row with a
//   count. Never reordered, never merged across a gap — the transcript is a timeline, and
//   collapsing non-adjacent calls would misrepresent the order things happened in.
//
//   The SUMMARY re-aggregates the whole group, so an interleaved
//   navigate → screenshot → navigate → screenshot renders as four honest rows but
//   summarizes as "Opened 2 pages, took 2 screenshots".
//
// Everything here works off the `display` block the SERVER resolved (verb/noun/owner). This
// module does no vocabulary of its own — it counts and joins. That split is the point: only
// the client knows what is adjacent on screen and what is still streaming, and only the
// server knows what a tool is called.

import type { ToolDisplay, ToolStatus } from '../../../types/chat'
import { stripExternalMcpFence } from '../../../utils/untrusted'
import { pluralize } from '../../../utils/pluralize'

export interface ToolItem {
  type: 'tool'
  name: string
  id: string
  status: ToolStatus
  display?: ToolDisplay
  input?: Record<string, unknown>
  result?: string
  resultElided?: number
  inputElided?: number
}

export interface FetchItem {
  type: 'fetch'
  url: string
}

/** A thought that happened BETWEEN calls. It rides the same rail so the group can span it —
 *  a run of activity interrupted by reasoning is still one run. */
export interface ThinkingItem {
  type: 'thinking'
  content: string
  /** Only the LAST thought of a live turn is still being written. */
  streaming?: boolean
}

export type ActionItem = ToolItem | FetchItem | ThinkingItem

export interface Cluster {
  /** Stable across renders — the first member's tool_use_id. */
  key: string
  items: ToolItem[]
  /** The row's sentence. A multi-item cluster restates it in the plural with a count. */
  label: string
  verb: string
  noun: string
  ownerId: string
  ownerName: string
  /** Present only on a single-item cluster — a count already says what N details cannot. */
  detail: string
  /** A failed call is always alone (see `isFailure`), so this describes the whole row. */
  failed: boolean
  /** The failure's own message, shown WITHOUT expanding the row. */
  errorText: string
  busy: boolean
}

/** The verb class the UI has no better word for, and the noun to match. */
const FALLBACK: ToolDisplay = {
  label: '',
  verb: 'unknown',
  noun: 'calls',
  owner_id: 'core',
  owner_name: '',
  detail: '',
}

/**
 * Strip a raw tool name down to something typeset, for the fallback path only.
 *
 * `mcp__interactive-brokers-mcp__get_positions` → "Get positions". This is TYPOGRAPHY, not
 * vocabulary: the same words the name already contains, with the transport prefix and the
 * underscores removed. It states nothing the name did not, which is the line this module
 * does not cross — real labels come from the server.
 */
function typeset(name: string): { label: string; noun: string } {
  let rest = name
  const mcp = /^mcp__[^_]*(?:_[^_]+)*?__(.+)$/.exec(name)
  if (mcp?.[1]) rest = mcp[1]
  else if (name.startsWith('mcp__')) rest = name.slice(5)
  const words = rest.replace(/[_-]+/g, ' ').trim()
  if (!words) return { label: name, noun: name }
  const label = words.charAt(0).toUpperCase() + words.slice(1)
  const noun = words.split(' ').slice(1).join(' ') || words
  return { label, noun }
}

/**
 * The display block, or a synthesized one for a client talking to an older backend.
 *
 * The fallback carries no verb class (so the row shows the neutral "some tool" glyph) and
 * no owner, because guessing either would state something about the call nobody can check.
 * It does typeset the name — printing `mcp__…__get_positions` raw, twice, once in the
 * summary and once on the row, is what version skew looked like before this existed.
 */
export function displayOf(item: ToolItem): ToolDisplay {
  if (item.display) return item.display
  const { label, noun } = typeset(item.name)
  return { ...FALLBACK, label, noun }
}

/**
 * Did this call fail? Read from the S2 error contract (`{"error": …}` / `ok: false`), which
 * is the shape every tool's failure comes back in — there is no separate error STATUS on the
 * wire, so the result is the only place the answer lives.
 *
 * Returns the message too, because a failure is shown without expanding the row: burying
 * "account not found" one click down, under a row that otherwise looks like every successful
 * one, is how a failed turn reads as a successful one.
 */
export function failureOf(
  item: ToolItem,
  declinedIsNotFailure = false,
): { failed: boolean; message: string } {
  if (item.status !== 'done' || item.result === undefined) return { failed: false, message: '' }
  // The fence is display-only noise here; what the model received is untouched.
  const raw = stripExternalMcpFence(item.result).trim()
  if (!raw) return { failed: false, message: '' }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return plainTextFailure(raw)
  }
  if (!parsed || typeof parsed !== 'object') return plainTextFailure(raw)
  const obj = parsed as Record<string, unknown>
  // permissions_v2 only (`declinedIsNotFailure`): the owner pressed Deny. The call did not run,
  // but nothing FAILED — the card already says "Denied", and a "1 failed" beside it would say the
  // opposite of what happened. With the flag off the result is read exactly as before.
  if (declinedIsNotFailure && obj.declined_by === 'user') return { failed: false, message: '' }
  const err = obj.error
  if (typeof err === 'string' && err.trim()) return { failed: true, message: err.trim() }
  if (err !== undefined && err !== null) return { failed: true, message: 'The call failed.' }
  if (obj.ok === false) {
    const msg = typeof obj.message === 'string' ? obj.message : 'The call failed.'
    return { failed: true, message: msg }
  }
  return { failed: false, message: '' }
}

/**
 * A non-JSON result that announces itself as an error.
 *
 * External MCP tools do not answer in the S2 `{"error": …}` shape — a failure comes back as
 * prose ("Error: Authentication required to create alerts…"), so every one of them read as a
 * SUCCESS: no badge, no row message, missing from the "N failed" count, and free to cluster
 * with calls that actually worked (owner report, 2026-08-22, IBKR MCP).
 *
 * Anchored at the very start and nowhere else. A result is allowed to discuss errors — a
 * log excerpt, a diagnostics report — and matching the word anywhere in the body would
 * relabel working calls as broken, which is the same defect pointing the other way.
 */
const ERROR_PREFIX = /^(?:tool\s+)?(?:error|failed|failure)\b[\s:—-]*/i

function plainTextFailure(text: string): { failed: boolean; message: string } {
  const m = ERROR_PREFIX.exec(text)
  if (!m) return { failed: false, message: '' }
  const body = text.slice(m[0].length).trim()
  return { failed: true, message: body || text }
}

/**
 * The discovery gate's own internal retry-me-once bounce (`_gate_bounce_message` in
 * assistant.py) — a call that never ran because its full definition had not loaded yet,
 * always followed by the real retry in the very same turn. It is deliberately worded to
 * read as a failure to the MODEL (two live automations once mistook a softer wording for
 * "no results" and acted on it), but that wording was never meant for a human reader: a
 * fresh onboarding session bounces almost every non-core tool's first call, so a live user
 * watching the rail saw a red "1 failed" and a raw JSON dump for something that was never
 * actually wrong. Matched on `cause`, the one field this shape alone sets to this exact text.
 */
export function isGateBounce(result: string | undefined): boolean {
  if (!result) return false
  let parsed: unknown
  try {
    parsed = JSON.parse(stripExternalMcpFence(result).trim())
  } catch {
    return false
  }
  if (!parsed || typeof parsed !== 'object') return false
  const cause = (parsed as Record<string, unknown>).cause
  return (
    typeof cause === 'string' &&
    cause.startsWith('you called it before its full definition was loaded')
  )
}

const isBusy = (s: ToolStatus) => s === 'streaming' || s === 'executing'

/** A noun already ending in "s" is left alone, which covers both real plurals ("transactions")
 *  and words like "status". The rest inflect through the shared `pluralize`. */
function plural(noun: string): string {
  return !noun || noun.endsWith('s') ? noun : pluralize(noun, 2)
}

/**
 * One row per call. Adjacent identical calls are NOT merged into a counted row.
 *
 * They were until 2026-08-23, and it caused two complaints with one root: a counted row
 * ("Update 2 run tasks") restated what the bar above it already said, and expanding it
 * opened a NESTED third level listing the calls it stood for. Aggregation is the bar's job —
 * that is what a summary IS — so a row is just a call, and every call expands in place.
 *
 * A long run of identical calls gets long; folding the bar is what that is for.
 */
export function clusterItems(items: ToolItem[], declinedIsNotFailure = false): Cluster[] {
  return items.map((item, i) => {
    const d = displayOf(item)
    const { failed, message } = failureOf(item, declinedIsNotFailure)
    return {
      key: item.id || `${d.owner_id}|${d.verb}|${d.noun}-${i}`,
      items: [item],
      label: d.label,
      verb: failed ? 'error' : d.verb,
      noun: d.noun,
      ownerId: d.owner_id,
      ownerName: d.owner_name,
      detail: d.detail,
      failed,
      errorText: message,
      busy: isBusy(item.status),
    }
  })
}

export function summarize(clusters: Cluster[]): string {
  const { phrase, failed } = summaryParts(clusters)
  return [phrase, failed].filter(Boolean).join(phrase && failed ? ' • ' : '')
}

/**
 * The summary, split so a caller can drop the half that would only repeat the rows.
 *
 * A group of two calls both labelled "Get connector permissions" summarizes to exactly that
 * phrase — printed above two rows that already say it, three identical lines stacked up
 * (owner report, 2026-08-22). The counts and the failure note are always additive; the
 * PHRASE is only additive when it says something no single row does.
 */
export function summaryParts(clusters: Cluster[]): { phrase: string; failed: string } {
  const order: string[] = []
  const totals = new Map<string, { verb: string; noun: string; n: number }>()
  let failures = 0

  for (const c of clusters) {
    if (c.failed) {
      failures += 1
      continue
    }
    const d = displayOf(c.items[0]!)
    const verb = d.label.split(' ')[0] || d.label
    const key = `${verb}|${d.noun}`
    const seen = totals.get(key)
    if (seen) seen.n += c.items.length
    else {
      totals.set(key, { verb, noun: d.noun, n: c.items.length })
      order.push(key)
    }
  }

  const phrases = order.map((k) => {
    const t = totals.get(k)!
    return t.n === 1 ? `${t.verb} ${t.noun}` : `${t.verb} ${t.n} ${plural(t.noun)}`
  })

  const shown = phrases.slice(0, 3)
  const extra = phrases.length - shown.length
  let phrase = shown.join(', ')
  if (extra > 0) phrase = phrase ? `${phrase} +${extra} more` : `${extra} more`
  // Sentence case: the first phrase's verb arrives capitalized ("Opened"), the rest read as
  // a continuing list ("opened 2 pages, took 2 screenshots").
  phrase = phrase.replace(/, ([A-Z])/g, (_m, c: string) => `, ${c.toLowerCase()}`)

  // The phrase is NEVER suppressed as a restatement of the rows any more.
  //
  // It used to be: a lone row whose label equalled the phrase printed the same sentence
  // twice, stacked (owner report, 2026-08-23). But the group folds by default now, so the
  // bar is usually the ONLY thing on screen — and suppressing the phrase there left a bar
  // reading "1 step" above an answer, which says nothing about what actually ran (owner
  // report, 2026-08-25). Expanded, the phrase reads as the heading for the rows it
  // summarizes, which is what it is.
  const failed = failures === 0 ? '' : failures === 1 ? '1 failed' : `${failures} failed`
  return { phrase, failed }
}
