/**
 * importHelpers.ts — Pure parsing and display utilities for ImportModal.
 * No React. No API calls. Safe to import in tests.
 *
 * The client-side grid parse (parseCSVGrid/parseXlsxGrid/findHeaderRow/computeMap) exists
 * ONLY to drive the manual column-mapping FORM — the parse that books money always runs
 * server-side through the one decision pipeline (the old client `analyse()` preview brain
 * is gone; the mapping is sent to the server as a teach instead).
 */

import { fCur } from './format'

// ── Format detection ──────────────────────────────────────────────────────

export const FORMAT_LABEL: Record<string, string> = {
  csv: 'CSV',
  xlsx: 'XLSX',
  ofx: 'OFX / QFX',
  qif: 'QIF',
  mt940: 'MT940',
  camt: 'CAMT.053',
  pdf: 'PDF',
}
const MAPPED = new Set(['csv', 'xlsx'])
export const ACCEPT = '.csv,.ofx,.qfx,.qbo,.qif,.mt940,.sta,.camt,.xml,.xlsx,.pdf'

export function needsMapping(fmt: string): boolean {
  return MAPPED.has(fmt)
}
export function detectFormat(name: string): string {
  const n = (name || '').toLowerCase()
  if (n.endsWith('.ofx') || n.endsWith('.qfx') || n.endsWith('.qbo')) return 'ofx'
  if (n.endsWith('.qif')) return 'qif'
  if (n.endsWith('.mt940') || n.endsWith('.sta')) return 'mt940'
  if (n.endsWith('.camt')) return 'camt'
  if (n.endsWith('.xlsx')) return 'xlsx'
  if (n.endsWith('.pdf')) return 'pdf'
  return 'csv'
}

// ── Types ─────────────────────────────────────────────────────────────────

/** One option on a matched row's `Change match` picker — the wire's `flags[].alternatives`
 * (RFC 2026-07-29 §D3 amendment (a)). Each entry carries its OWN `_existing_summary` rather
 * than a bare id: the engine's candidate pool is ±FETCH_WINDOW_DAYS across ALL accounts,
 * which is wider than anything the client can look an id up in, so a bare id list would force
 * the client to silently drop options the server would happily accept. */
export interface MatchAlternative {
  id: string
  existing?: ExistingSummaryLike
}

export interface ImportPreviewDraft {
  date: string
  payee: string | null
  description: string
  amount: number
  kind: 'income' | 'expense'
  category: string | null
  currency: string
  // Match flags (RFC batch-aware-dedup): near_duplicate/transfer_pair/transfer_leg_echo carry
  // existing_id/existing/score/klass + the batch engine's band/promoted verdict; other types
  // (intra_batch_duplicate, amount_anomaly, no_account_for_currency, fx_transfer_pair/leg)
  // carry only type/reason.
  flags: Array<{
    type: string
    reason: string
    existing_id?: string
    // SI-50: source/account_id/original_amount/original_currency ride on `_existing_summary`
    // (backend/services/import_anomalies.py) — enough to render the match line's own dim
    // sub-line (date · account · category · source · amount) without a second round-trip.
    existing?: {
      note?: string
      amount?: number
      currency?: string
      kind?: string
      has_category?: boolean
      date?: string
      source?: string
      account_id?: string
      // On the wire since _existing_summary always emitted them; typed for the after-import
      // projection (the existing row's own category label + cleared state).
      category?: string | null
      cleared?: boolean
      original_amount?: number | null
      original_currency?: string | null
    }
    score?: number
    klass?: string
    /** `RowMatch.band` from `match_batch`. `'weak'` is the engine's own settled-drift tier
     * (RFC 2026-07-29 §D2): scored into [SCORE_WEAK, SCORE_FENCE), 1:1-claimed like every
     * other banded class, but never promotable and never auto-mergeable. Absent/unknown →
     * treat as 'review' (never assume strong on missing data). */
    band?: 'strong' | 'weak' | 'review'
    /** The match picker's option list, server-chosen and best-first (`RowMatch.alternatives`,
     * stamped by `decisions._apply_batch_match`): the OTHER ledger entries the engine derived
     * for THIS row that no other row ended up claiming. Because they are unclaimed they stay
     * in the uncovered set, so `decision_commit`'s acceptance set already accepts every one —
     * which is why the client renders them VERBATIM: never re-sorted, re-filtered or dimmed. */
    alternatives?: MatchAlternative[]
    /** Whether books-close promotion lifted this row — surfaced only as the headline tooltip. */
    promoted?: boolean
  }>
  dup: boolean
  /** The existing ledger transaction id this row is already booked under (dup=true only) —
   *  lets the review UI link straight to it instead of just naming the reason. */
  dupOf?: string | null
  preRecon: boolean
  autoCat: boolean
  destId?: string
  destName?: string
  /** Internal FX conversion pairing: the OUT leg books ONE transfer; the IN leg folds into it. */
  pair?: { role: 'out' | 'in'; partner_index: number } | null
  origAmount?: number | null
  origCurrency?: string | null
  // Server-side enrichment (snake_case, from services/import_common.display_draft): the
  // pre-conversion foreign charge, the bank reference, and the dedup provenance pairs the commit
  // re-derives (one external_refs list, ADR 2026-07-21).
  original_amount?: number | null
  original_currency?: string | null
  reference?: string | null
  external_refs?: Array<{ source: string | null; ref: string }>
  failed?: boolean
  /** Decision payload (RFC D7): the server-side row index decisions reference. */
  rowIndex?: number
  /** The 1-based row of the file it came from (CSV, or an Excel file's first sheet) — the
   *  review's file pane highlights it. Absent where no file row maps 1:1. */
  sourceRow?: number
  /** Low model confidence / guard note — offered but defaulted to skip, never silent. */
  needsEyes?: boolean
  confidence?: number
  notes?: string[]
  /** SI-66 routing rule verdict: this row drafts as a two-leg TRANSFER (direction-aware —
   *  a money-out row books statement account → routed account; money-in the reverse).
   *  ``rule_id === 'lucius_pass'`` is the SI-68 sentinel — a Lucius-pass op, not a saved
   *  rule, reshaped this row (the `lucius` field below carries its own provenance/reason;
   *  the row rendering prefers that chip over this generic one when both are present). */
  routed?: {
    rule_id: string
    from_account_id: string
    from_account_name?: string | null
    to_account_id: string
    to_account_name?: string | null
  } | null
  /** SI-68: the Lucius draft-operations pass op that shaped THIS row (from the owner's
   *  upload instructions) — null for every untouched row. Reasoned + undoable in the UI.
   *  `reject_match`/`force_match` (ADR 2026-07-28) act on the row's `flags` rather than the
   *  draft itself — a rejected match simply has no flag left to show; a forced one rides the
   *  SAME near_duplicate flag shape (review band, never strong) a real engine match would, so
   *  no separate rendering path is needed for either. */
  lucius?: {
    op: 'route_transfer' | 'set_category' | 'skip' | 'reject_match' | 'force_match'
    reason?: string
    account_id?: string | null
    category?: string | null
    category_label?: string | null
    existing_id?: string | null
  } | null
  /** The dry-run verdict: what commit WILL assign (rule / payee / ✦ hint). */
  enrich?: {
    payee_id?: string | null
    payee_name?: string | null
    category?: string | null
    category_source?: 'rule' | 'payee' | 'hint' | null
  } | null
}

export interface StatementMetaSummary {
  currency?: string | null
  period_start?: string | null
  period_end?: string | null
  statement_date?: string | null
  payment_due_date?: string | null
  opening_balance?: number | null
  closing_balance?: number | null
  amount_due?: number | null
  minimum_payment?: number | null
  rewards?: { balance?: number; unit?: string } | null
  issuer?: string | null
}

/** One row of the ledger-check panel (`rows` or `boundary`) — a ledger transaction the
 *  statement's period covers but its own rows don't account for. Mirrors
 *  `import_anomalies._existing_summary`'s wire shape (SI-50 already relies on this same
 *  projection for the existing-transaction match sub-line). */
export interface LedgerCheckRow {
  id: string
  date?: string
  note?: string
  amount?: number | null
  currency?: string | null
  kind?: string
  source?: string
  category?: string | null
  account_id?: string
  /** The pre-conversion foreign charge. On the wire for every projected row (rows AND
   * boundary) since `_existing_summary` computes it once (backend/services/import_anomalies.py);
   * typed here so the ledger-side surfaces can show the original beside the converted figure
   * exactly like the statement side already does. */
  original_amount?: number | null
  original_currency?: string | null
  /** Which statement(s) this entry is ALREADY on, resolved server-side from `external_refs`
   * (backend/services/statements/ledger_statement_links.py). Null when it is on none we hold —
   * hand-entered, an agent booking, a bank-feed sync, an import whose statement was deleted.
   * Render `label` verbatim; it is the finished human string, including the "+N more" when the
   * row is on several. NOT derived from `batch_id`, which answers where the row came
   * FROM and is blind to every row a statement linked rather than created. */
  on_statements?: OnStatements | null
  /** Set exactly when this entry sits near a window cut-off — which edge and how far, decided
   * by the SAME predicate that files it under `boundary`
   * (backend/services/statements/ledger_diff.py `_near_cutoff`). Never re-derive it from
   * `period`: the client's own edge test was wrong (it compared against the widened window
   * end, so its "dated after the close" group could never be non-empty). */
  near_cutoff?: NearCutoff | null
}

/** One statement a ledger row is on, matched by an `external_refs` entry. */
export interface StatementLink {
  statement_id: string
  /** The statement's identity — "5 May – 4 Jun 2026 statement", or its filename. */
  label: string
  filename?: string | null
  /** The BANK's own reference for the row, as printed on the statement. Null whenever the feed
   * carried none — the alternative on the wire is our own content hash, which is a dedup key,
   * not a reference, and is never shown. */
  reference?: string | null
  period_start?: string | null
  period_end?: string | null
}

/** Every statement a ledger row is on. `statements` is ordered: the cycle whose printed period
 * CONTAINS the row leads, then the latest-closing. `label` and `reference` describe that
 * primary one, and `label` already carries the "+N more" when there are several — the client
 * neither counts nor phrases. */
export interface OnStatements {
  label: string
  reference?: string | null
  statements: StatementLink[]
}

export interface NearCutoff {
  edge: 'start' | 'end'
  /** Whole days between the entry and that edge; 0 = sitting on it. */
  days: number
  /** Chip-length human form — "Closing cut-off", "2 days before closing". */
  label: string
}

export interface ImportPreviewResult {
  drafts: ImportPreviewDraft[]
  importable: number
  duplicates: number
  /** The decision payload's `rows` carries every skipped row uncapped — full list. The legacy
   *  fallback (`skip_samples`, no decision payload present) is capped at 10 server-side, so
   *  don't rely on `.length` for the true count either way; use `skippedTotal`. */
  skipped: Array<{
    description?: string
    line?: number
    reason: string
    amount?: number
    currency?: string
    failed?: boolean
  }>
  /** The REAL total of skipped/unbookable rows (server's `skipped` int, uncapped) — use this
   *  for any "N skipped" count; `skipped.length` is only ever the first-10 sample. */
  skippedTotal: number
  total: number
  anomaly: {
    near_duplicate: number
    transfer_pair?: number
    transfer_leg_echo?: number
    intra_batch_duplicate: number
    amount_anomaly: number
    flagged_rows: number
  }
  multi?: boolean
  /** Per-currency wallet routing for a consolidated multi-currency statement (null = no wallet). */
  routing?: Record<string, { account_id: string; account_name?: string } | null> | null
  /** What THIS file's parse cost in LLM calls (0 on a format-memory / cached re-preview). */
  llmCostUsd?: number | null
  /** The anchor account actually used — chosen, or detected from the printed account number. */
  resolvedAccountId?: string | null
  /** True when no account was chosen AND detection found nothing — pick one (parse is cached). */
  needsAccount?: boolean
  /** PRE-COMMIT ledger check: what your ledger holds for this period that the statement
   *  does NOT (rows = needs eyes; boundary = near the cut-off, likely adjacent statement).
   *  Both arrays are the SAME `_existing_summary` projection on the wire (backend/services/
   *  import_anomalies.py) — boundary rows carry the full shape too (account_id/category
   *  included).
   *
   *  A PURE SET DIFFERENCE since RFC 2026-07-29 §D3: `preview_ledger_check` subtracts every
   *  entry some flag carries an `existing_id` for, so a weak-band claim already removes its
   *  entry here. There is no scoring, no `statement_twin` annotation and no `twin_eligible`
   *  map left on this payload — "which rows might pair" is the FORWARD view's job
   *  (`flags[].band` / `flags[].alternatives`), and this one only answers "what is left". */
  ledgerCheck?: {
    period?: { start: string; end: string }
    rows?: Array<LedgerCheckRow>
    boundary?: Array<LedgerCheckRow>
    /** Set instead of `rows`/`boundary` when the parse never finished reading every
     *  page/chunk (`parse_incomplete`), the statement's own balances don't reconcile
     *  (`reconcile_off`), or the cached parse predates completeness tracking entirely and
     *  was never verified either way (`parse_unverified`) — "not on this statement" cannot
     *  be trusted in any of the three cases, so the band renders nothing rather than a
     *  wrong or partial answer (ADR 2026-09-09-an-incomplete-parse-withholds-the-ledger-
     *  diff). */
    withheld?: 'parse_incomplete' | 'reconcile_off' | 'parse_unverified'
    pages_incomplete?: number
    delta?: number
    currency?: string
  } | null
  /** Decision flow (present for pdf/csv/xlsx auto-parses): commit references these. */
  statementId?: string
  /** B20 async, code review round B (M4): a plain sync upload/reopen of a file whose async
   *  job is already `parsing` server-side gets a 202 ATTACH response instead of a real
   *  preview — every other field on this object is meaningless when this is true; only
   *  `statementId` is. The modal switches into its resume-by-id flow rather than rendering
   *  an empty preview. */
  attached?: boolean
  meta?: StatementMetaSummary | null
  reconcile?: {
    status: 'ok' | 'ok_if_flipped' | 'off' | 'unknown'
    delta?: number
    currency?: string
  } | null
  needsEyes?: number
  accountSuggestion?: string | null
  formatProfile?: {
    id: string
    origin?: string
    learned?: boolean
    relearned?: boolean
    source_label?: string
  } | null
  alreadyImported?: { statement_id: string; imported_at?: string } | null
  /** SI-67: the owner instructions that applied to this preview (request-supplied or
   *  remembered on the format profile). `used_in_parse` = an LLM row-extraction prompt
   *  actually saw them (a cached/deterministic parse only feeds the hint batch).
   *  `remembered` false = a one-off ("Remember for this source" unchecked): applied to
   *  this import only, never saved. */
  instructions?: {
    text: string
    lines: number
    used_in_parse: boolean
    remembered?: boolean
  } | null
  /** SI-68: what the Lucius draft-operations pass did to THIS preview's rows. null = no
   *  owner guidance was in play at all; otherwise `status` says whether it actually ran
   *  ('ran' | 'cached' | 'skipped' | 'unavailable'). `fresh` = billed on this preview; a
   *  cached re-preview re-applies the same ops for free. `proposed` -> `validated` ->
   *  `applied` is the decision funnel: ops die at hard validation or at the contextual
   *  appliers, and only three separate numbers make either drop visible. */
  luciusPass?: {
    status: 'ran' | 'cached' | 'skipped' | 'unavailable'
    proposed: number
    validated: number
    applied: number
    routes: number
    categories: number
    skips: number
    fresh: boolean
    llm_calls: number
    cost_usd: number
  } | null
}

/**
 * Map a server `/api/statements/preview` response (a `PreviewResult` dataclass, serialised) into
 * the shape the ImportModal renders. The JSON keys are the dataclass field names — `sample`
 * (the bounded draft list), `total_rows`, `skip_samples`, `anomaly_summary` — NOT
 * `drafts`/`total`/`skipped`/`anomaly`. Reading the wrong names left `drafts` empty, so every
 * statement upload showed "0 to import / 0 transactions read" even though the server parsed
 * the rows correctly. Kept pure + exported so it is unit-tested against the real wire shape.
 */
export function mapServerPreview(data: Record<string, unknown>): ImportPreviewResult {
  // Decision payload (RFC D7): `rows` is the FULL uncapped row list with per-row verdicts —
  // preferred over the display-bounded legacy `sample` whenever present.
  const decisionRows = data.rows as Array<Record<string, unknown>> | undefined
  const drafts = (decisionRows && decisionRows.length
    ? decisionRows
        .filter((r) => !r.skip)
        .map((r) => ({
          date: r.date,
          payee: r.payee,
          description: r.description,
          amount: r.amount,
          currency: r.currency,
          kind: (r.amount as number) >= 0 ? 'income' : 'expense',
          category: (r.enrich as Record<string, unknown> | null)?.category ?? null,
          autoCat:
            ((r.enrich as Record<string, unknown> | null)?.category_source ?? null) === 'hint',
          original_amount: r.original_amount,
          original_currency: r.original_currency,
          reference: r.reference,
          external_refs: r.external_refs,
          dup: r.duplicate,
          dupOf: (r.duplicate_of as string | null) ?? null,
          rowIndex: r.index,
          sourceRow: (r.source_row as number | null | undefined) ?? undefined,
          needsEyes: r.needs_eyes,
          confidence: r.confidence,
          destId: (r.account_id as string) || undefined,
          destName: (r.account_name as string) || undefined,
          pair: (r.pair as { role: 'out' | 'in'; partner_index: number } | null) ?? null,
          routed: (r.routed as ImportPreviewDraft['routed']) ?? null,
          lucius: (r.lucius as ImportPreviewDraft['lucius']) ?? null,
          notes: Array.isArray(r.notes) ? r.notes : [],
          enrich: r.enrich ?? null,
          flags: Array.isArray(r.flags) ? r.flags : [],
        }))
    : ((data.sample as Array<Record<string, unknown>>) || []).map((d) => ({
        ...d,
        flags: Array.isArray(d.flags) ? d.flags : [],
      }))) as unknown as ImportPreviewDraft[]
  // Same reasoning as `drafts` above: the decision payload's `rows` is uncapped, so prefer it
  // over the legacy `skip_samples` (hard-capped at 10 server-side) whenever it's present — the
  // review screen can then list every skipped row, not just the first 10.
  const skipped = (
    decisionRows && decisionRows.length
      ? decisionRows
          .filter((r) => r.skip)
          .map((r) => ({
            line: (r.index as number) + 1,
            reason: (r.skip_reason as string) || 'skipped',
            description: r.description as string | undefined,
            amount: r.amount as number | undefined,
            currency: r.currency as string | undefined,
          }))
      : (data.skip_samples as ImportPreviewResult['skipped']) || []
  ) as ImportPreviewResult['skipped']
  const num = (v: unknown, fallback: number) => (typeof v === 'number' ? v : fallback)
  return {
    drafts,
    importable: num(data.importable, drafts.filter((d) => !d.dup).length),
    duplicates: num(data.duplicates, drafts.filter((d) => d.dup).length),
    skipped,
    skippedTotal: num(data.skipped, skipped.length),
    total: num(data.total_rows, drafts.length),
    anomaly: (data.anomaly_summary as ImportPreviewResult['anomaly']) ?? {
      near_duplicate: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 0,
    },
    multi: (data.multi as boolean) ?? false,
    statementId: (data.statement_id as string) || undefined,
    attached: (data.attached as boolean) ?? false,
    meta: (data.meta as ImportPreviewResult['meta']) ?? null,
    reconcile: (data.reconcile as ImportPreviewResult['reconcile']) ?? null,
    needsEyes: num(data.needs_eyes, 0),
    accountSuggestion: (data.account_suggestion as string) ?? null,
    formatProfile: (data.format_profile as ImportPreviewResult['formatProfile']) ?? null,
    alreadyImported: (data.already_imported as ImportPreviewResult['alreadyImported']) ?? null,
    routing: (data.routing as ImportPreviewResult['routing']) ?? null,
    llmCostUsd: typeof data.llm_cost_usd === 'number' ? data.llm_cost_usd : null,
    resolvedAccountId: (data.resolved_account_id as string) ?? null,
    needsAccount: (data.needs_account as boolean) ?? false,
    ledgerCheck: (data.ledger_check as ImportPreviewResult['ledgerCheck']) ?? null,
    instructions: (data.instructions as ImportPreviewResult['instructions']) ?? null,
    luciusPass: (data.lucius_pass as ImportPreviewResult['luciusPass']) ?? null,
  }
}

// ── Teach-a-rule match derivation (SI-66) ─────────────────────────────────────────────

/** Propose the `description_contains` match for a teach-from-row rule: the clean payee
 * when the raw descriptor contains it (a case-insensitive substring match at rule time),
 * else the most merchant-like stable token of the descriptor — longest ALL-CAPS
 * digit-free run first (banks upcase merchant names: `Ecom.EW.SD.240115.POCKETPAY.….CashIn.…`
 * → `POCKETPAY`), else the longest digit-free token. Always shown for edit before saving —
 * this is a proposal, not a verdict. */
export function deriveRuleMatch(
  payee: string | null | undefined,
  description: string | null | undefined,
): string {
  const desc = (description || '').trim()
  const clean = (payee || '').trim()
  if (clean && desc.toLowerCase().includes(clean.toLowerCase())) return clean
  const tokens = desc.split(/[^A-Za-z0-9]+/).filter((t) => t.length >= 3 && !/\d/.test(t))
  const caps = tokens.filter((t) => t === t.toUpperCase())
  const pick = (list: string[]) => list.slice().sort((a, b) => b.length - a.length)[0]
  return pick(caps) || pick(tokens) || clean || desc.slice(0, 24)
}

// ── CSV parsing (mapping-form source only — the booking parse is server-side) ────────

export function parseCSVGrid(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = [],
    cur = '',
    q = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (q) {
      if (c === '"' && text[i + 1] === '"') {
        cur += '"'
        i++
      } else if (c === '"') q = false
      else cur += c
    } else if (c === '"') q = true
    else if (c === ',') {
      row.push(cur)
      cur = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++
      row.push(cur)
      cur = ''
      if (row.some((x) => x.trim() !== '')) rows.push(row)
      row = []
    } else cur += c
  }
  if (cur !== '' || row.length) {
    row.push(cur)
    if (row.some((x) => x.trim() !== '')) rows.push(row)
  }
  return rows.map((r) => r.map((c) => c.trim().replace(/^﻿/, '')))
}

// ── XLSX parsing ──────────────────────────────────────────────────────────

async function unzip(blob: Blob): Promise<Record<string, { method: number; comp: Uint8Array }>> {
  const buf = new Uint8Array(await blob.arrayBuffer()),
    dv = new DataView(buf.buffer)
  let i = buf.length - 22
  for (; i >= 0; i--) if (dv.getUint32(i, true) === 0x06054b50) break
  if (i < 0) throw new Error('not a zip')
  const cdCount = dv.getUint16(i + 10, true)
  let cdOff = dv.getUint32(i + 16, true)
  const entries: Record<string, { method: number; comp: Uint8Array }> = {}
  for (let n = 0; n < cdCount; n++) {
    if (dv.getUint32(cdOff, true) !== 0x02014b50) break
    const method = dv.getUint16(cdOff + 10, true),
      compSize = dv.getUint32(cdOff + 20, true)
    const nameLen = dv.getUint16(cdOff + 28, true),
      extraLen = dv.getUint16(cdOff + 30, true),
      commentLen = dv.getUint16(cdOff + 32, true)
    const localOff = dv.getUint32(cdOff + 42, true)
    const name = new TextDecoder().decode(buf.subarray(cdOff + 46, cdOff + 46 + nameLen))
    const lhNameLen = dv.getUint16(localOff + 26, true),
      lhExtraLen = dv.getUint16(localOff + 28, true)
    const dataStart = localOff + 30 + lhNameLen + lhExtraLen
    entries[name] = { method, comp: buf.subarray(dataStart, dataStart + compSize) }
    cdOff += 46 + nameLen + extraLen + commentLen
  }
  return entries
}

async function inflate(e: { method: number; comp: Uint8Array }): Promise<Uint8Array> {
  if (e.method === 0) return e.comp
  const ds = new DecompressionStream('deflate-raw')
  return new Uint8Array(
    await new Response(new Blob([new Uint8Array(e.comp)]).stream().pipeThrough(ds)).arrayBuffer(),
  )
}

function decodeXml(s: string): string {
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&amp;/g, '&')
}
function colNum(ref: string): number {
  const m = ref.match(/^([A-Z]+)/)
  if (!m) return 0
  let n = 0
  for (const ch of m[1]!) n = n * 26 + (ch.charCodeAt(0) - 64)
  return n - 1
}

export async function parseXlsxGrid(blob: Blob): Promise<string[][]> {
  const e = await unzip(blob)
  const dec = new TextDecoder()
  let shared: string[] = []
  if (e['xl/sharedStrings.xml']) {
    const xml = dec.decode(await inflate(e['xl/sharedStrings.xml']))
    shared = [...xml.matchAll(/<si>([\s\S]*?)<\/si>/g)].map((m) =>
      [...m[1]!.matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((t) => decodeXml(t[1]!)).join(''),
    )
  }
  const key = Object.keys(e).find((k) => /xl\/worksheets\/sheet1\.xml/.test(k)) || ''
  const sheet = e[key]
  if (!sheet) throw new Error('no worksheet found in workbook')
  const sx = dec.decode(await inflate(sheet))
  return [...sx.matchAll(/<row[^>]*>([\s\S]*?)<\/row>/g)].map((rm) => {
    const cells = [...rm[1]!.matchAll(/<c\s+([^>]*?)(\/>|>([\s\S]*?)<\/c>)/g)]
    const row: string[] = []
    for (const c of cells) {
      const attrs = c[1] ?? '',
        inner = c[3] || ''
      const ref = (attrs.match(/r="([A-Z]+\d+)"/) ?? [])[1] ?? ''
      const tt = (attrs.match(/t="([^"]+)"/) ?? [])[1] ?? ''
      const idx = ref ? colNum(ref) : row.length
      const vm = inner.match(/<v>([\s\S]*?)<\/v>/)
      const ism = inner.match(/<is>[\s\S]*?<t[^>]*>([\s\S]*?)<\/t>/)
      let val = ''
      if (tt === 's' && vm) val = shared[+vm[1]!] || ''
      else if (ism) val = decodeXml(ism[1]!)
      else if (vm) val = decodeXml(vm[1]!)
      row[idx] = val ?? ''
    }
    for (let k = 0; k < row.length; k++) if (row[k] == null) row[k] = ''
    return row
  })
}

// ── Structural detection ──────────────────────────────────────────────────

export function looksNumeric(s: string): boolean {
  const t = String(s ?? '').trim()
  return t !== '' && /^[(+\-]?[\d., ]+\)?$/.test(t) && /\d/.test(t)
}

function rowIsData(grid: string[][], r: number): boolean {
  let d = 0,
    n = 0
  for (const c of grid[r] || []) {
    const s = String(c ?? '').trim()
    if (!s) continue
    if (looksNumeric(s)) n++
    else if (parseDate(firstDateToken(s))) d++
  }
  return d >= 1 && n >= 1
}

export function findTable(grid: string[][]): { header: number; start: number; len: number } {
  let bestStart = -1,
    bestLen = 0,
    i = 0
  while (i < grid.length) {
    if (rowIsData(grid, i)) {
      let j = i
      while (j < grid.length && rowIsData(grid, j)) j++
      if (j - i > bestLen) {
        bestLen = j - i
        bestStart = i
      }
      i = j
    } else i++
  }
  return { header: bestStart > 0 ? bestStart - 1 : bestStart, start: bestStart, len: bestLen }
}

export function findHeaderRow(grid: string[][]): number {
  return findTable(grid).header
}

// ── Value parsers (feed the table/header detection above) ─────────────────

const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
function isoDate(y: number, m: number, d: number) {
  return y + '-' + String(m).padStart(2, '0') + '-' + String(d).padStart(2, '0')
}
export function firstDateToken(s: string): string {
  const m = String(s || '').match(
    /\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[\/.\-]\d{1,2}[\/.\-]\d{2,4}|\d{1,2}[-\s][A-Za-z]{3,}[-\s]\d{2,4}/,
  )
  return m ? m[0] : ''
}
export function parseDate(s: string): string | null {
  if (!s) return null
  const t = String(s).trim()
  let m = t.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/)
  if (m) return isoDate(+m[1]!, +m[2]!, +m[3]!)
  m = t.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})/)
  if (m) {
    let y = +m[3]!
    y = y < 100 ? 2000 + y : y
    const a = +m[1]!,
      b = +m[2]!
    return b > 12 && a <= 12 ? isoDate(y, a, b) : isoDate(y, b, a)
  }
  m = t.match(/^(\d{1,2})[-\s]([A-Za-z]{3,})[-\s](\d{2,4})/)
  if (m) {
    const mo = MONTHS.indexOf(m[2]!.slice(0, 3).toLowerCase()) + 1
    let y = +m[3]!
    y = y < 100 ? 2000 + y : y
    if (mo) return isoDate(y, mo, +m[1]!)
  }
  const d = new Date(t)
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10)
}

/** The pre-conversion foreign charge, formatted — or `null` when there is none to show.
 *
 * ONE guard for every import surface, statement side and ledger side alike (owner, 2026-07-29:
 * "ensure in ALL cards/views/surfaces in the import statement flow we show original charge +
 * currency whenever there is one"). Before this, only the statement side rendered it, so an
 * identical foreign charge read as a mismatch: the statement row showed `$8.25 / −S$11.02`
 * while its matched ledger entry showed only `−S$10.65`, hiding that the S$0.37 gap is an
 * FX-timing artifact rather than a real discrepancy.
 *
 * Null (render NOTHING — never a dash, never empty parens) when the subject carries no
 * original, or when the original is already in the currency being displayed. Accepts BOTH
 * spellings: server-enriched drafts and existing-transaction summaries use `original_amount`/
 * `original_currency`; client-mapped drafts use `origAmount`/`origCurrency`. */
export function originalCharge(
  subject: {
    original_amount?: number | null
    original_currency?: string | null
    origAmount?: number | null
    origCurrency?: string | null
  },
  displayCurrency?: string | null,
): string | null {
  const amount = subject.original_amount ?? subject.origAmount
  const currency = subject.original_currency ?? subject.origCurrency
  if (!amount || !currency || currency === displayCurrency) return null
  return fCur(amount, currency, { cents: true })
}

// ── Row-flag display labels ───────────────────────────────────────────────

export const FLAG_LABEL: Record<string, string> = {
  near_duplicate: 'Already in your account?',
  // Formation, not fact (owner decision 2026-07-16): no transfer exists yet — two single-leg
  // rows COULD form one. 'Matches an existing transfer' was a lie for this case; that copy is
  // reserved for transfer_leg_echo below, where a real transfer really does already exist.
  transfer_pair: 'Opposite legs — one transfer?',
  transfer_leg_echo: 'Already booked as a transfer',
  intra_batch_duplicate: 'Repeated in file',
  amount_anomaly: 'Unusual amount',
  no_account_for_currency: 'No account in this currency',
  fx_transfer_pair: 'Transfer between your accounts',
  fx_transfer_leg: 'Second leg — folds into the transfer',
}

// ── Existing-transaction match line (SI-50) ────────────────────────────────

/** Compact "how it got there" phrase for the match sub-line's source field. `source` on the
 * flag's `existing` is already DISPLAY-projected server-side (import_anomalies.py's
 * `_existing_summary` → core.txn_sources.display_source), so every raw feed variant
 * (csv_import/qif_import/api/bank syncs/reconcile/...) has already collapsed onto the handful
 * this switches on. */
export function existingSourcePhrase(source?: string | null): string {
  if (source === 'ai') return 'via Lucius'
  if (source === 'agent') return 'via agent'
  if (source === 'import') return 'from an earlier import'
  return 'added by you'
}

// ── Transfer identity (owner complaint 2026-07-17) ────────────────────────────────────
// A transfer must never render through the expense/income template with an "Uncategorized"
// category — an uncategorized transfer is normal, not needs-sorting. ONE implementation
// feeds all three review surfaces (statement category cell, YOUR LEDGER NOW line,
// AFTER IMPORT projection).

/** Account display name: caller-supplied name → the accounts prop → the raw id (never
 * blank — an unknown id shows as itself). Null only when the id itself is missing. */
function accountName(
  id: string | null | undefined,
  name: string | null | undefined,
  accounts: Array<{ id: string; name: string }>,
): string | null {
  if (name) return name
  if (!id) return null
  return accounts.find((a) => a.id === id)?.name || id
}

/** What `transferLine` accepts, duck-typed: a statement DRAFT is a transfer when `routed`
 * is set (an SI-66 rule or SI-68 Lucius op — the payload carries both legs + best-effort
 * names); an EXISTING-txn summary / ledger-check row is one when `kind === 'transfer'`. */
export interface TransferLineSubject {
  routed?: ImportPreviewDraft['routed']
  amount?: number | null
  kind?: string
  account_id?: string
  to_account_id?: string | null
  category?: string | null
}

/** The transfer identity line, or null for a non-transfer subject (callers keep their own
 * rendering). A set category appends (" · <label>") — legal on transfers (owner decision
 * 2026-07-03); the "Uncategorized" fallback NEVER applies here.
 *
 * - Draft (`routed`), `compact`: the direction-aware counter-account the category cell
 *   shows — money out books statement → routed ("Transfer → <to>"), money in the reverse
 *   ("Transfer ← <from>"); direction follows the amount sign, the server's own convention.
 * - Draft, default: both legs, "Transfer · <from> → <to>".
 * - Existing summary: the wire (`_existing_summary`) ships only the FROM leg's
 *   `account_id`. The to-leg is the statement side's own account (`anchorAccountId`)
 *   whenever the from-leg is elsewhere — a matched existing transfer necessarily touches
 *   the draft's account, so the missing leg IS the anchor. When the from-leg equals the
 *   anchor, `counterHint` (a routed draft's own to-leg) fills it; with neither, the
 *   identity degrades to a bare "Transfer" — honest, never a fabricated arrow. */
export function transferLine(
  subject: TransferLineSubject,
  accounts: Array<{ id: string; name: string }>,
  catMap: Record<string, { label: string }>,
  opts: { anchorAccountId?: string; counterHint?: string; compact?: boolean } = {},
): string | null {
  const withCat = (head: string) =>
    subject.category ? `${head} • ${catMap[subject.category]?.label || subject.category}` : head
  const r = subject.routed
  if (r) {
    const out = (subject.amount ?? 0) < 0
    if (opts.compact) {
      const counter = out
        ? accountName(r.to_account_id, r.to_account_name, accounts)
        : accountName(r.from_account_id, r.from_account_name, accounts)
      return withCat(counter ? `Transfer ${out ? '→' : '←'} ${counter}` : 'Transfer')
    }
    const from = accountName(r.from_account_id, r.from_account_name, accounts)
    const to = accountName(r.to_account_id, r.to_account_name, accounts)
    return withCat(from && to ? `Transfer • ${from} → ${to}` : 'Transfer')
  }
  if (subject.kind !== 'transfer') return null
  const from = accountName(subject.account_id, null, accounts)
  const counterId =
    opts.anchorAccountId && opts.anchorAccountId !== subject.account_id
      ? opts.anchorAccountId
      : opts.counterHint && opts.counterHint !== subject.account_id
        ? opts.counterHint
        : subject.to_account_id && subject.to_account_id !== subject.account_id
          ? subject.to_account_id
          : null
  const to = counterId ? accountName(counterId, null, accounts) : null
  if (opts.compact) {
    // The wire's existing-transfer summary only ever names the FROM leg (`account_id`) — when
    // that leg IS the anchor, the anchor sent money OUT to a (rarely nameable, hint-only) "to"
    // leg; otherwise the anchor is the missing TO leg, so the money moved IN from the FROM leg
    // — which is always nameable. Same "never fabricate an arrow" honesty as the legs form.
    const anchorIsFrom = !!opts.anchorAccountId && subject.account_id === opts.anchorAccountId
    const counter = anchorIsFrom ? to : from
    return withCat(counter ? `Transfer ${anchorIsFrom ? '→' : '←'} ${counter}` : 'Transfer')
  }
  return withCat(from && to ? `Transfer • ${from} → ${to}` : 'Transfer')
}

/** The existing transaction's own quiet, one-line summary rendered under a match headline
 * (both bands — needs-you AND will-link): "— MM-DD · Account · Category · source · ±amount".
 * Degrades gracefully — an unresolvable account is OMITTED, an unresolvable-but-present
 * category falls back to its raw stored value, a blank category reads "Uncategorized"; nothing ever
 * renders "undefined". Pure/testable (no React) — `accounts`/`catMap` are plain lookups the
 * caller already has. A `kind=transfer` row swaps the account+category tokens for its
 * transfer identity ("Transfer · <from> → <to>", `transferLine` + `transferCtx`); the
 * degraded no-counter-leg form keeps the account token so the from side stays visible. */
export function existingTxnLine(
  existing: {
    date?: string
    account_id?: string
    category?: string | null
    source?: string
    amount?: number | null
    currency?: string | null
    kind?: string
    to_account_id?: string | null
    from_amount?: number | null
    to_amount?: number | null
    from_currency?: string | null
    to_currency?: string | null
    original_amount?: number | null
    original_currency?: string | null
  },
  accounts: Array<{ id: string; name: string }>,
  catMap: Record<string, { label: string }>,
  fallbackCurrency: string,
  transferCtx?: { anchorAccountId?: string; counterHint?: string; omitFacts?: boolean },
): string {
  const parts: string[] = []
  // Cmp's own callers already show a date + amount footer (imp3-cmp-facts) right below this
  // line — repeating both here read as duplicated information (owner, 2026-07-27).
  const omitFacts = !!transferCtx?.omitFacts
  if (existing.date && !omitFacts) parts.push(existing.date.slice(5))
  const acct = accounts.find((a) => a.id === existing.account_id)
  const tl = transferLine(existing, accounts, catMap, transferCtx ?? {})
  if (tl) {
    // The legs form names the from account itself; only the degraded form (no " → ")
    // still needs the separate account token.
    if (!tl.includes(' → ') && acct) parts.push(acct.name)
    parts.push(tl)
  } else {
    if (acct) parts.push(acct.name)
    parts.push(
      existing.category ? catMap[existing.category]?.label || existing.category : 'Uncategorized',
    )
  }
  parts.push(existingSourcePhrase(existing.source))
  // Show the leg on the STATEMENT's own account, not the raw from-leg magnitude: a
  // ₫1,600,000 credit linked to a TransferQuick SGD→VinaBank transfer must read ₫1,600,000,
  // never the from-leg "S$79.30" (reported case, 2026-07-18). anchorPerspectiveAmount picks
  // the anchor-side leg + currency for a transfer; non-transfers pass through unchanged.
  const anchor = transferCtx?.anchorAccountId
  const amt =
    existing.kind === 'transfer'
      ? anchorPerspectiveAmount(existing, anchor, existing.amount ?? 0)
      : existing.amount
  const ccy = anchorPerspectiveCurrency(existing, anchor, fallbackCurrency)
  if (typeof amt === 'number' && !omitFacts) {
    // The pre-conversion charge rides right behind the settled figure, same as the statement
    // side — absent entirely when this entry has none (`originalCharge` returns null).
    const original = originalCharge(existing, ccy)
    parts.push(fCur(amt, ccy, { cents: true }) + (original ? ` (${original})` : ''))
  }
  return '— ' + parts.join(' • ')
}

/** The one structural shape BOTH existing-row summaries share — a match flag's `existing`
 * (import_anomalies._existing_summary) and a `LedgerCheckRow` (the same projection). The
 * §2 resolver (`resolveExisting`) and the after-import projection accept either. */
export interface ExistingSummaryLike {
  note?: string | null
  date?: string
  amount?: number | null
  currency?: string | null
  kind?: string
  category?: string | null
  has_category?: boolean
  source?: string
  account_id?: string
  original_amount?: number | null
  original_currency?: string | null
  cleared?: boolean
  // Transfer legs (additive on the wire 2026-07-18) — let the anchor-perspective resolver
  // pick the leg on the statement's own account instead of the raw from-leg.
  to_account_id?: string | null
  from_amount?: number | null
  to_amount?: number | null
  from_currency?: string | null
  to_currency?: string | null
}

// ── Match-line drift wording + after-import projection ────────────────────────────────────

/** Settled-drift %: |statement| vs |ledger|, relative to the ledger's own figure — the ONE
 * drift computation behind every "how far apart are these two figures" phrase on this
 * screen. Null when the ledger amount is unusable. */
export function driftPct(
  statementAmount?: number | null,
  ledgerAmount?: number | null,
): number | null {
  const la = ledgerAmount != null ? Math.abs(ledgerAmount) : null
  if (la == null || la <= 0 || statementAmount == null) return null
  return (Math.abs(Math.abs(statementAmount) - la) / la) * 100
}

/** Client mirror of the server's "same figure" verdict (matching.amounts_match:
 * EXACT_AMOUNT_REL_TOL 0.1% with a ±0.01 absolute floor) — display only, drives the
 * `same amount` vs `X.X% off` suffix below. */
export function sameFigure(a?: number | null, b?: number | null): boolean {
  if (a == null || b == null) return false
  const x = Math.abs(a),
    y = Math.abs(b)
  return Math.abs(x - y) <= Math.max(0.01, 0.001 * Math.max(x, y))
}

/** How far this statement figure sits from a candidate ledger figure: `same amount` or
 * `X.X% off`. Null when the two aren't comparable at all.
 *
 * This is CLIENT-COMPUTED arithmetic over two figures already on screen — deliberately NOT
 * the engine's own `flag.reason`, which is raw matcher diagnostics ("wording differs
 * (review); unique pairing in a complete statement window") and is never rendered anywhere
 * (owner, 2026-07-29). ONE implementation feeds both the weak suggestion line and the match
 * picker's option lines, so the two can never quote different drift for the same pair. */
export function driftPhrase(
  statementAmount?: number | null,
  ledgerAmount?: number | null,
): string | null {
  const pct = driftPct(statementAmount, ledgerAmount)
  if (pct == null) return null
  return sameFigure(statementAmount, ledgerAmount) ? 'same amount' : `${pct.toFixed(1)}% off`
}

/** One match-picker option line: `MM-DD · note · amount (same amount | X.X% off)`.
 * Degrades field-by-field like `existingTxnLine` — never renders "undefined". */
export function matchOptionLine(
  row: Pick<
    ExistingSummaryLike,
    'date' | 'note' | 'amount' | 'currency' | 'original_amount' | 'original_currency'
  >,
  draftAmount: number,
  fallbackCurrency: string,
): string {
  const bits: string[] = []
  if (row.date) bits.push(row.date.slice(5))
  bits.push(row.note || '—')
  if (typeof row.amount === 'number') {
    const ccy = row.currency || fallbackCurrency
    // A candidate's own pre-conversion charge is often the fact that identifies it — two
    // S$14.xx rows are indistinguishable until you see one is the $10.90 charge you want.
    const original = originalCharge(row, ccy)
    bits.push(fCur(row.amount, ccy, { cents: true }) + (original ? ` (${original})` : ''))
  }
  const drift = driftPhrase(draftAmount, row.amount)
  return bits.join(' • ') + (drift ? ` (${drift})` : '')
}

/** Does a merge ADOPT the statement's date/amount onto the existing row? The kind-gate
 * mirror of `apply_import_merge` (facts="statement"), extracted so the after-import
 * projection and the two-question verdict share ONE implementation: a transfer target
 * (leg echo) keeps its legs whole; a transfer_pair / cross-kind merge stamps cleared +
 * reference only; a same-kind expense/income merge adopts. An old summary that omits
 * `kind` is same-kind by construction (near-dup matches). */
export function mergeAdoptsStatementFacts(
  draftKind: string,
  existingKind: string | undefined,
  flagType?: string,
): boolean {
  if (flagType === 'transfer_leg_echo' || existingKind === 'transfer') return false
  if (flagType === 'transfer_pair') return false
  return existingKind
    ? existingKind === draftKind && (existingKind === 'expense' || existingKind === 'income')
    : true
}

// ── The two-question verdict (SI-64) ──────────────────────────────────────────────────

/** One statement row's LEDGER-EFFECT after commit — the verdict's per-row input. The
 * component builds these mirroring `confirmImport`'s decision ladder EXACTLY (dup →
 * unrouted → FX fold → resolved-match action → needs-eyes → pre-reconciled → create), so
 * the verdict describes what commit will actually do, live. */
export interface VerdictRowInput {
  /** dup = already booked (L0) · absorbed = folds into its partner's transfer ·
   *  merge / new = books through the matched row / as its own transaction ·
   *  skip = books nothing (covered only when a resolved match already holds it). */
  effect: 'dup' | 'absorbed' | 'merge' | 'new' | 'skip'
  /** The statement's own signed figure, native currency. */
  amount: number
  currency: string
  /** The resolved existing entry (engine flag, weak twin, or picker override), if any. */
  existing?: { amount?: number | null; currency?: string | null } | null
  /** merge only — false when the kind gate keeps the existing row's own figures. */
  mergeAdopts?: boolean
  /** The existing entry's figure FROM THE STATEMENT ACCOUNT'S PERSPECTIVE (signed, anchor
   *  currency) — set by the caller via `anchorPerspectiveAmount` when the existing is a
   *  TRANSFER: its summary `amount` is the from-leg's unsigned magnitude (possibly another
   *  currency entirely), which false-drifted a perfectly-linked ₫180,000 top-up by ₫350,000
   *  (sign) and a TransferQuick SGD→VinaBank link by ₫1,499,900 (wrong leg + currency) —
   *  reported cases, 2026-07-18. When set, every kept-figure computation prefers it. */
  existingKept?: number | null
  /** The currency `existingKept` is actually expressed in — defaults to `existing.currency`.
   *  Needed because a bad/coincidental match can pair rows in two DIFFERENT currencies (the
   *  normal case assumes a near-duplicate always shares the statement row's own currency);
   *  without this, `projectedAmount - amount` silently subtracts across currencies and
   *  reports the result AS IF it were in the statement's own currency. */
  existingKeptCurrency?: string | null
}

/** The existing entry's amount from the ANCHOR account's perspective: for a TRANSFER, the
 * leg on the statement's own account, signed by direction (to-leg = money in = positive,
 * from-leg = negative), in that leg's own (= the statement's) currency. Non-transfers pass
 * through unchanged. Fallback when the wire lacks legs or the anchor matches neither leg
 * (older cached payloads): the summary magnitude signed like the statement figure — the
 * echo/routed link already guarantees direction agreement, so a sign mismatch there is
 * convention, not evidence. */
export function anchorPerspectiveAmount(
  existing:
    | {
        amount?: number | null
        kind?: string | null
        account_id?: string | null
        to_account_id?: string | null
        from_amount?: number | null
        to_amount?: number | null
      }
    | null
    | undefined,
  anchorAccountId: string | null | undefined,
  statementAmount: number,
): number | null {
  if (!existing || existing.amount == null) return existing?.amount ?? null
  if (existing.kind !== 'transfer') return existing.amount
  if (anchorAccountId) {
    if (existing.to_account_id === anchorAccountId && existing.to_amount != null)
      return Math.abs(existing.to_amount)
    if (existing.account_id === anchorAccountId && existing.from_amount != null)
      return -Math.abs(existing.from_amount)
  }
  return (statementAmount < 0 ? -1 : 1) * Math.abs(existing.amount)
}

/** Currency paired with `anchorPerspectiveAmount`. A transfer summary's top-level currency
 * belongs to its stored from-leg; when the statement is anchored on the to-account, every
 * money-bearing preview must instead use the to-leg currency. */
export function anchorPerspectiveCurrency(
  existing:
    | {
        currency?: string | null
        kind?: string | null
        account_id?: string | null
        to_account_id?: string | null
        from_currency?: string | null
        to_currency?: string | null
      }
    | null
    | undefined,
  anchorAccountId: string | null | undefined,
  fallbackCurrency: string,
): string {
  if (!existing || existing.kind !== 'transfer') return existing?.currency || fallbackCurrency
  if (anchorAccountId && existing.to_account_id === anchorAccountId)
    return existing.to_currency || existing.currency || fallbackCurrency
  if (anchorAccountId && existing.account_id === anchorAccountId)
    return existing.from_currency || existing.currency || fallbackCurrency
  return existing.currency || fallbackCurrency
}

/** Question 1 — "would all statement transactions be in my ledger?" A row is COVERED when
 * something represents it post-commit: merge → the matched entry; new → its own booking;
 * skip WITH a resolved match → the matched entry; dups and absorbed FX legs by construction.
 * A skip with NO match is covered by nothing — it won't be anywhere after import. */
export interface CoverageVerdict {
  ok: boolean
  total: number
  uncovered: number
  linked: number
  imported: number
  dups: number
  absorbed: number
}

export function coverageVerdict(rows: VerdictRowInput[]): CoverageVerdict {
  let uncovered = 0,
    linked = 0,
    imported = 0,
    dups = 0,
    absorbed = 0
  for (const r of rows) {
    if (r.effect === 'dup') dups++
    else if (r.effect === 'absorbed') absorbed++
    else if (r.effect === 'merge') linked++
    else if (r.effect === 'new') imported++
    else if (r.existing)
      linked++ // skip-with-match: the matched entry represents it
    else uncovered++
  }
  return { ok: uncovered === 0, total: rows.length, uncovered, linked, imported, dups, absorbed }
}

/** The post-commit figure a row projects: merge → statement settled, unless the kind gate
 * keeps the existing figures; skip-with-match → the EXISTING entry's amount; everything
 * else books/stays at the statement's own figure. An existing summary with no readable
 * amount falls back to the statement figure (graceful — no fabricated delta). */
function projectedAmount(r: VerdictRowInput): number {
  const kept = r.existingKept ?? r.existing?.amount
  if (r.effect === 'merge' && r.mergeAdopts === false) return kept ?? r.amount
  if (r.effect === 'skip') return kept ?? r.amount
  return r.amount
}

/** The currency `projectedAmount` is actually expressed in — the statement's own currency
 * for a fresh booking, else whatever currency the KEPT figure carries. A near-duplicate
 * match normally shares the statement row's currency (the same real transaction, read
 * twice), but a coincidental/wrong match can pair two different currencies — `totals`
 * must bucket by the figure's REAL currency, never assume it matches `r.currency`. */
function projectedCurrency(r: VerdictRowInput): string {
  const keeps = (r.effect === 'merge' && r.mergeAdopts === false) || r.effect === 'skip'
  if (!keeps) return r.currency
  return r.existingKeptCurrency || r.existing?.currency || r.currency
}

/** One row's linked-amount drift — non-null EXACTLY when the amounts verdict counts the
 * row as "kept at your original figures": a merge the kind gate stops from adopting, or a
 * skip leaving a drifted existing entry standing, past the verdict's own ±0.005 float
 * guard. THE single source of truth for every Off-by surface (owner motivation
 * 2026-07-17 — a kept-figure mismatch must be findable in seconds, not after a 5-hour
 * hunt): `amountsVerdict`'s kept count, the per-row amber marker, and the transient
 * Off-by filter pill all consume this one predicate, so they can never disagree.
 * `diff` = kept − statement, the deltas convention.
 *
 * Null (no drift) when the kept figure is in a DIFFERENT currency than the statement row —
 * subtracting across currencies would fabricate a number and mislabel it with the wrong
 * symbol (reported case: a ₫325,650 statement row matched to an unrelated S$16.75 ledger
 * entry showed "Off by ₫325,670", not a real amount in any currency). The comparison card
 * still shows both figures, correctly labeled, side by side — that's the honest signal. */
export function linkedAmountDrift(
  r: VerdictRowInput,
): { statement: number; kept: number; diff: number } | null {
  const keeps =
    (r.effect === 'merge' && r.mergeAdopts === false) || (r.effect === 'skip' && !!r.existing)
  if (!keeps) return null
  if (projectedCurrency(r) !== r.currency) return null
  const kept = projectedAmount(r)
  const diff = kept - r.amount
  if (Math.abs(diff) <= 0.005) return null
  return { statement: r.amount, kept, diff }
}

/** The Off-by marker's tooltip — states what the CURRENT action does with the two figures,
 * honestly per effect: a skip adopts nothing; a merge that reaches this marker is one the
 * kind gate keeps at your figures (an adopting merge projects the settled amount — no
 * drift, no marker). Null when the row carries no drift. */
export function offByTitle(r: VerdictRowInput): string | null {
  const drift = linkedAmountDrift(r)
  if (!drift) return null
  const stmt = fCur(drift.statement, r.currency, { cents: true })
  // A perspective-resolved kept figure is in the ANCHOR (statement) currency — the raw
  // summary currency may be the transfer's OTHER leg (S$79.30 on a ₫1,600,000 link).
  const yours = fCur(
    drift.kept,
    r.existingKept != null ? r.currency : r.existing?.currency || r.currency,
    { cents: true },
  )
  if (r.effect === 'skip')
    return `Skipped — yours stays ${yours}; the statement’s ${stmt} is not adopted.`
  return `Statement settled ${stmt} • yours says ${yours} — this merge links them but keeps your figure (its legs/kind stay as booked).`
}

/** Question 2 — "would the amounts match?" Projects each REPRESENTED row's post-commit
 * figure (`projectedAmount`) and compares totals per currency against the statement's own
 * activity for the same rows. Uncovered rows are EXCLUDED from both sides — their absence
 * is question 1's message, never double-reported here. `kept` counts exactly the rows
 * `linkedAmountDrift` flags — the shared predicate behind every Off-by surface. */
export interface AmountsVerdict {
  ok: boolean
  /** Rows whose post-commit figure differs from the statement's own (kept estimates). */
  kept: number
  /** Projected ledger total per currency over every represented row. */
  totals: Record<string, number>
  /** projected − statement per currency — only currencies actually off. */
  deltas: Record<string, number>
  represented: number
}

export function amountsVerdict(rows: VerdictRowInput[]): AmountsVerdict {
  const totals: Record<string, number> = {}
  const deltas: Record<string, number> = {}
  let kept = 0,
    represented = 0
  for (const r of rows) {
    if (r.effect === 'skip' && !r.existing) continue // uncovered — question 1's territory
    represented++
    const projected = projectedAmount(r)
    const ccy = projectedCurrency(r)
    if (linkedAmountDrift(r)) kept++ // the ONE drift predicate — markers/pill count the same rows
    // Bucket by the figure's REAL currency (usually r.currency, but a kept figure from a
    // mismatched match can genuinely be a different one) — never assume, or a foreign kept
    // amount silently pollutes another currency's projected total.
    totals[ccy] = (totals[ccy] || 0) + projected
    if (ccy === r.currency) deltas[ccy] = (deltas[ccy] || 0) + (projected - r.amount)
  }
  for (const k of Object.keys(totals)) totals[k] = Math.round(totals[k]! * 100) / 100
  for (const k of Object.keys(deltas)) {
    deltas[k] = Math.round(deltas[k]! * 100) / 100
    if (deltas[k] === 0) delete deltas[k]
  }
  return { ok: kept === 0 && Object.keys(deltas).length === 0, kept, totals, deltas, represented }
}

/** One value in the after-import projection — `old` set only when the commit will CHANGE it
 * (the old→new delta the column accents; unchanged fields stay quiet). */
export interface AfterField {
  value: string
  old?: string
}

/** One ledger record as it will exist after commit. `tag` drives the visual treatment:
 * `kept` (byte-unchanged, quiet) · `updated` (carries deltas) · `new` (a record commit adds). */
export interface AfterRecord {
  tag: 'kept' | 'updated' | 'new'
  title: string
  date?: AfterField
  category: string
  amount?: AfterField
  /** The pre-conversion foreign charge; `added` when the merge gap-fills it onto the row. */
  original?: { value: string; added: boolean }
  badges: string[]
}

export interface AfterProjection {
  records: AfterRecord[]
  count: string | null
}

/** The AFTER IMPORT column model for a MATCHED pair row — mirrors `apply_import_merge`
 * (`facts="statement"`, backend/services/ledger.py) EXACTLY, per action:
 *
 * - `merge` → ONE record: the existing entry's labels (category/notes — never changed) with
 *   the statement's date + settled amount; original amount gap-filled only when the existing
 *   row LACKS one; cleared ✓ + the statement's reference attached. Kind-gate mirror: a
 *   `transfer` target (leg echo) keeps its own date/amount/legs — shown unchanged + linked ✓;
 *   a transfer_pair / cross-kind merge never rewrites date/amount either (cleared + reference
 *   only).
 * - `skip` → ONE record: the existing entry, byte-unchanged ("Yours stays as-is").
 * - `new`  → TWO records, stacked, with the honest "2 records" count — the existing entry
 *   unchanged plus the statement row booked as its own transaction.
 *
 * Pure — unit-testable without React; the caller resolves category labels via `catMap`. */
export function afterImportProjection(args: {
  action: 'skip' | 'merge' | 'new'
  draft: Pick<
    ImportPreviewDraft,
    | 'date'
    | 'payee'
    | 'description'
    | 'amount'
    | 'currency'
    | 'category'
    | 'kind'
    | 'original_amount'
    | 'original_currency'
    | 'origAmount'
    | 'origCurrency'
    | 'routed'
    | 'destId'
    | 'destName'
  >
  existing: ExistingSummaryLike
  flagType?: string
  catMap: Record<string, { label: string }>
  /** Account-name source + the statement side's own account for the transfer identity line
   * ("Transfer · <from> → <to>", see `transferLine`). Optional — non-transfer callers and
   * older tests are untouched; without them a transfer degrades to raw ids / "Transfer". */
  accounts?: Array<{ id: string; name: string }>
  anchorAccountId?: string
}): AfterProjection {
  const { action, draft, existing, flagType, catMap, accounts = [], anchorAccountId } = args
  const fmt = (v: number, ccy?: string | null) => fCur(v, ccy || draft.currency, { cents: true })
  const catLabel = (key?: string | null) => (key ? catMap[key]?.label || key : 'Uncategorized')
  // Transfer identity for the category slot: an existing kind=transfer record (the routed
  // draft's own to-leg is the counter hint when the from-leg IS the anchor) and a routed
  // draft's NEW record both name their legs; "Uncategorized" never applies to a transfer.
  const exTransfer = transferLine(existing, accounts, catMap, {
    anchorAccountId,
    counterHint: draft.routed?.to_account_id,
  })
  const existingAmount =
    existing.kind === 'transfer'
      ? anchorPerspectiveAmount(existing, anchorAccountId, draft.amount)
      : existing.amount
  const existingCurrency = anchorPerspectiveCurrency(existing, anchorAccountId, draft.currency)
  const exOriginal =
    existing.original_amount != null && existing.original_currency
      ? { value: fmt(existing.original_amount, existing.original_currency), added: false }
      : undefined
  const existingRecord: AfterRecord = {
    tag: 'kept',
    title: existing.note || '—',
    ...(existing.date ? { date: { value: existing.date.slice(5) } } : {}),
    category: exTransfer ?? catLabel(existing.category),
    ...(typeof existingAmount === 'number'
      ? { amount: { value: fmt(existingAmount, existingCurrency) } }
      : {}),
    ...(exOriginal ? { original: exOriginal } : {}),
    badges: [],
  }
  if (action === 'skip')
    return { records: [{ ...existingRecord, badges: ['yours stays as-is'] }], count: null }
  if (action === 'new') {
    const oa = draft.original_amount ?? draft.origAmount
    const oc = draft.original_currency ?? draft.origCurrency
    const newRecord: AfterRecord = {
      tag: 'new',
      title: draft.payee || draft.description,
      date: { value: draft.date.slice(5) },
      category: transferLine(draft, accounts, catMap) ?? catLabel(draft.category),
      amount: { value: fmt(draft.amount) },
      ...(oa != null && oc && oc !== draft.currency
        ? { original: { value: fCur(oa, oc, { cents: true }), added: false } }
        : {}),
      badges: [],
    }
    return { records: [existingRecord, newRecord], count: '2 records' }
  }
  // merge — kind-gate mirror of apply_import_merge: a transfer target keeps its legs whole.
  if (flagType === 'transfer_leg_echo' || existing.kind === 'transfer') {
    // The leg on the STATEMENT's own account adopts its settled amount; the other leg stays
    // exactly as booked (ledger.apply_import_merge, facts="statement"). Showing the existing
    // figure here told the owner nothing would change while the merge changed it.
    // Mirrors the backend gate exactly: the leg is re-amounted ONLY when the statement's own
    // account IS one of the transfer's legs. Without that, an unrelated row's figure would be
    // shown as overwriting a transfer the merge never touches.
    const legIsOurs =
      !!anchorAccountId &&
      (anchorAccountId === existing.account_id || anchorAccountId === existing.to_account_id)
    const adopts =
      legIsOurs &&
      typeof existingAmount === 'number' &&
      Math.abs(Math.abs(existingAmount) - Math.abs(draft.amount)) > 0.004
    const record: AfterRecord = adopts
      ? {
          ...existingRecord,
          tag: 'updated',
          amount: {
            value: fmt(draft.amount, draft.currency),
            old: fmt(existingAmount as number, existingCurrency),
          },
        }
      : existingRecord
    return { records: [{ ...record, badges: ['linked ✓'] }], count: null }
  }
  // transfer_pair: NEITHER side is a transfer yet — apply_import_transfer_pair_merge builds
  // ONE, direction-aware (backend/services/ledger.py). Mirror that here rather than falling
  // into the generic "kept at your figures" merge below, which would honestly misrepresent
  // the outcome as a same-kind record when it's really becoming a two-leg transfer.
  if (flagType === 'transfer_pair') {
    const draftIsDebit = draft.kind === 'expense'
    const existingLeg = accountName(existing.account_id, null, accounts)
    const draftLeg = draft.destName || null
    const fromName = draftIsDebit ? draftLeg : existingLeg
    const toName = draftIsDebit ? existingLeg : draftLeg
    const draftAmt = Math.abs(draft.amount)
    const existingAmt = Math.abs(existingAmount ?? draft.amount)
    const debitAmt = draftIsDebit ? draftAmt : existingAmt
    const debitCcy = draftIsDebit ? draft.currency : existingCurrency
    const creditAmt = draftIsDebit ? existingAmt : draftAmt
    const creditCcy = draftIsDebit ? existingCurrency : draft.currency
    // Two DIFFERENT real legs, never one figure standing in for both — a same-currency
    // fee-bearing pair or a genuinely cross-currency one both need both amounts shown, or
    // the number silently implies they're identical when they aren't.
    const sameLeg = debitCcy === creditCcy && Math.abs(debitAmt - creditAmt) <= 0.005
    const dateChanged = !!existing.date && existing.date !== draft.date
    return {
      records: [
        {
          tag: 'updated',
          title:
            fromName && toName
              ? `${fromName} → ${toName}`
              : existing.note || draft.payee || draft.description || '—',
          date: {
            value: draft.date.slice(5),
            ...(dateChanged ? { old: existing.date!.slice(5) } : {}),
          },
          // An internal transfer moves money between YOUR OWN accounts — it isn't a spending
          // category (the title already names the from/to accounts; the old single-leg
          // category, assigned back when this looked like a real expense/income, no longer
          // applies once it's recognized as one movement).
          category: '',
          amount: {
            value: sameLeg
              ? fmt(debitAmt, debitCcy)
              : `${fmt(debitAmt, debitCcy)} → ${fmt(creditAmt, creditCcy)}`,
          },
          badges: ['becomes a transfer', 'cleared ✓'],
        },
      ],
      count: null,
    }
  }
  const oa = draft.original_amount ?? draft.origAmount
  const oc = draft.original_currency ?? draft.origCurrency
  const addsOriginal = oa != null && !!oc && existing.original_amount == null
  const gapFilled = addsOriginal
    ? { original: { value: fCur(oa, oc, { cents: true }), added: true } }
    : {}
  if (!mergeAdoptsStatementFacts(draft.kind, existing.kind, flagType)) {
    return {
      records: [{ ...existingRecord, ...gapFilled, badges: ['cleared ✓', 'reference linked'] }],
      count: null,
    }
  }
  const dateChanged = !!existing.date && existing.date !== draft.date
  const amountChanged =
    typeof existing.amount === 'number' && Math.abs(existing.amount - draft.amount) > 0.004
  return {
    records: [
      {
        tag: 'updated',
        title: existing.note || '—',
        date: {
          value: draft.date.slice(5),
          ...(dateChanged ? { old: existing.date!.slice(5) } : {}),
        },
        category: catLabel(existing.category),
        amount: {
          value: fmt(draft.amount),
          ...(amountChanged ? { old: fmt(existing.amount!, existing.currency) } : {}),
        },
        ...(addsOriginal ? gapFilled : exOriginal ? { original: exOriginal } : {}),
        badges: ['cleared ✓', 'reference attached'],
      },
    ],
    count: null,
  }
}

// ── Manual mapping helpers ────────────────────────────────────────────────

export interface ColumnMap {
  date: string
  description: string
  amount: string
  inflow: string
  outflow: string
  category: string
  status: string
}

export function computeMap(cols: string[]): Partial<ColumnMap> {
  const find = (...n: string[]) =>
    cols.find((c) => n.some((x) => (c || '').toLowerCase().includes(x))) || ''
  const m: Partial<ColumnMap> = {}
  const date = find('date', 'posted')
  if (date) m.date = date
  const desc = find('description', 'memo', 'payee', 'detail', 'narration')
  if (desc) m.description = desc
  const inflow = find('credit', 'deposit', 'money in')
  const outflow = find('debit', 'withdrawal', 'money out')
  const amount = find('amount', 'value')
  if (inflow || outflow) {
    if (inflow) m.inflow = inflow
    if (outflow) m.outflow = outflow
  } else if (amount) m.amount = amount
  const cat = find('category')
  if (cat) m.category = cat
  const status = find('status', 'state', 'result')
  if (status) m.status = status
  return m
}

export function mapComplete(m: Partial<ColumnMap>): boolean {
  return !!m.date && !!m.description && !!(m.amount || m.inflow || m.outflow)
}

// ── Live preview progress (B20) ─────────────────────────────────────────────
// Truthful stage reporting for a statement preview in flight — replaces the fake
// READ_PHASES timer whenever the server actually answers. See
// `backend/services/statements/parse_progress.py` for the source of this shape.

/** The fixed pipeline order the stage bar renders — a stage the file's own route skips
 *  (e.g. `render` for a source that was never a scanned PDF) still counts as PASSED once a
 *  later stage has started, so the bar never stalls on a step that was never going to fire. */
export const PROGRESS_STAGE_ORDER = [
  'render',
  'read',
  'reconcile',
  'match',
  'lucius',
  'categorise',
  'ledger_check',
] as const
export type ProgressStage = (typeof PROGRESS_STAGE_ORDER)[number]

export interface PreviewProgressStage {
  stage: string
  label: string
  started_at: string
  done_at: string | null
  page: number | null
  pages: number | null
  retry: number | null
}

export interface PreviewProgress {
  stages: PreviewProgressStage[]
  started_at: string
  elapsed_s: number
  done: boolean
  error: string | null
  /** B20 async mode: the statement record's id, the moment the server has minted one — even
   *  for an ordinary SYNC upload (no `mode=async`), so "Continue in background" knows which
   *  statements-list row to point at. Null until then. */
  statementId: string | null
}

/** Validate + narrow an untyped JSON reply into `PreviewProgress`, or `null` for anything
 *  that doesn't match — an older server, a network body that isn't JSON, a future reshape.
 *  This is the ONE feature-detect point: a `null` here is what tells the caller to fall back
 *  to today's fake-timer phrasing rather than rendering half a shape. */
export function mapPreviewProgress(data: unknown): PreviewProgress | null {
  if (!data || typeof data !== 'object') return null
  const d = data as Record<string, unknown>
  if (!Array.isArray(d.stages)) return null
  const stages: PreviewProgressStage[] = []
  for (const raw of d.stages) {
    if (!raw || typeof raw !== 'object') return null
    const s = raw as Record<string, unknown>
    if (typeof s.stage !== 'string' || typeof s.label !== 'string') return null
    stages.push({
      stage: s.stage,
      label: s.label,
      started_at: typeof s.started_at === 'string' ? s.started_at : '',
      done_at: typeof s.done_at === 'string' ? s.done_at : null,
      page: typeof s.page === 'number' ? s.page : null,
      pages: typeof s.pages === 'number' ? s.pages : null,
      retry: typeof s.retry === 'number' ? s.retry : null,
    })
  }
  return {
    stages,
    started_at: typeof d.started_at === 'string' ? d.started_at : '',
    elapsed_s: typeof d.elapsed_s === 'number' ? d.elapsed_s : 0,
    done: !!d.done,
    error: typeof d.error === 'string' ? d.error : null,
    statementId: typeof d.statement_id === 'string' ? d.statement_id : null,
  }
}

/** The phase line's own copy — deliberately NOT the server's generic `label` field, which
 *  exists for logs/other consumers: this is the polished narrative wording for the modal. */
export function progressPhaseLabel(
  stage: string,
  page: number | null | undefined,
  pages: number | null | undefined,
  fallbackLabel?: string,
): string {
  switch (stage as ProgressStage) {
    case 'render':
      return 'Rendering the pages'
    case 'read':
      return page && pages ? `Reading page ${page} of ${pages}` : 'Reading the statement'
    case 'reconcile':
      return 'Checking the statement against its own balances'
    case 'match':
      return 'Checking every row against your ledger'
    case 'lucius':
      return 'Lucius is reviewing the read'
    case 'categorise':
      return 'Sorting rows into categories'
    case 'ledger_check':
      return 'Comparing against what’s already booked'
    default:
      return fallbackLabel || 'Reading the statement'
  }
}

export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const m = Math.floor(s / 60)
  const rem = s % 60
  return `${m}:${String(rem).padStart(2, '0')}`
}

/** Segment status for the 7-stage progress bar: everything strictly before the current
 *  stage's position in `PROGRESS_STAGE_ORDER` is done, the current one is "on", the rest are
 *  future — regardless of whether every earlier stage actually reported (a text source never
 *  reports `render`, and still passes it). A finished preview marks every segment done. */
export function progressStageStatus(
  progress: PreviewProgress,
  stage: ProgressStage,
): 'done' | 'on' | 'future' {
  const last = progress.stages[progress.stages.length - 1]
  const lastIndex = last ? PROGRESS_STAGE_ORDER.indexOf(last.stage as ProgressStage) : -1
  const index = PROGRESS_STAGE_ORDER.indexOf(stage)
  if (progress.done || (lastIndex !== -1 && index < lastIndex)) return 'done'
  if (!progress.done && index === lastIndex) return 'on'
  return 'future'
}
