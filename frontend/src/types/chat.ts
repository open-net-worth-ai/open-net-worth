export type MessageRole = 'user' | 'assistant'

// A conversation's row-level status in the sidebar/browser (concurrent-conversations RFC;
// .claude/rfcs/concurrent-conversations-ui-spec.md §1.1). Distinct from chatSessions.ts's
// TurnStatus, which is the LIVE in-flight state of an active fetch — this is the coarser
// summary a conversation LIST shows, including for a conversation that has no local slice
// at all (a run this browser did not start, or a different device's turn).
//
// Written in PRIORITY order, highest first — also the sort-precedence and visual-weight
// order (§5.1):
//   needs_you — a turn is paused on the user's yes/no about a money write (RFC
//               TurnStatus='awaiting_approval'). Outranks working: a merely-streaming turn
//               resolves itself, a turn parked on a confirm card does not move again until
//               the user acts. The one state a finance app cannot afford to render as
//               "nothing to report" (acceptance E6).
//   working   — a turn is in flight right now, for ANY reason
//   done      — a turn finished successfully since last seen, and it isn't the one on screen
//   failed    — a turn ended in error, not yet seen
//   run_failed — an Automation run that FAILED, already seen: still shows "Failed", but is
//               not unread (Mark all read, sorting and announcements ignore it)
//   idle      — nothing to report (the default; renders no affordance at all)
export type ConvRowStatus = 'idle' | 'needs_you' | 'working' | 'done' | 'failed' | 'run_failed'

export interface Attachment {
  ref_id: string
  kind: 'image' | 'text'
  summary: string
  localUrl?: string // object URL for preview
}

// One consumed-lot line in a disposal preview (mirrors backend services/lots.py
// compute_disposal → display.consumptions). Gain is in the proceeds currency; None
// (null) when the lot's cost basis is unknown — rendered as "—", never a fake 0.
export interface DisposalConsumption {
  lot_id?: string
  qty: number
  unit_cost?: number | null
  cost_currency?: string | null
  acquired_at?: string
  term?: 'long' | 'short' | 'unknown'
  realized_gain?: number | null
}

/** An exact {old,new} text edit as rendered on a confirm card (backend core/text_edit.py). */
export interface TextEditDisplay {
  old: string
  new: string
  old_truncated?: boolean
  new_truncated?: boolean
  old_chars?: number
  new_chars?: number
}

export interface AutomationFieldChange {
  old: unknown
  new: unknown
}

export interface AutomationUpdateDisplay {
  id: string
  name?: string
  baseline_updated_at?: string | null
  changes: Record<string, AutomationFieldChange>
}

export interface PositionDisplay {
  // Tool-permission preview metadata.
  tool?: string
  arguments?: Record<string, unknown>
  raw_arguments?: Record<string, unknown>
  preview?: ToolPreview
  symbol?: string
  name?: string
  asset_class?: string
  currency?: string
  venue?: string
  quantity?: number | string
  cost_basis?: number | string | null
  before?: Record<string, unknown>
  after?: Record<string, unknown>
  // Authoring change display. `tools_used`/`allowed_tools` are agent-only (a skill carries no
  // tool list); `changed_fields` is shared by skill_update and agent_update.
  tools_used?: number
  allowed_tools?: string[]
  changed_fields?: string[]
  // Tax-lot change display (lot_acquire / lot_dispose; mirrors backend ai/tools/lots.py).
  position_id?: string
  unit_cost?: number | null
  cost_currency?: string | null
  // Fee-capture sweep 2026-07-13: commissions shown on the confirm card before booking.
  fees?: number | null
  disposal_fees?: number | null
  acquired_at?: string
  kind?: string // acquisition kind: buy | transfer_in | grant
  method?: string // disposal lot-selection: fifo | specific_id
  is_taxable?: boolean
  disposed_at?: string // disposal date the sale books to (drives term + tax period)
  proceeds_per_unit?: number | null
  total_proceeds?: number | null // qty × proceeds/unit (from the draft if present)
  proceeds_currency?: string | null
  // INV1-2: where the net proceeds get deposited (null = not added to any account).
  proceeds_account_id?: string | null
  proceeds_account_name?: string | null
  // A share move names where the units went (resolved by the service; shown as "→ name").
  transfer_to_account_id?: string | null
  transfer_to_account_name?: string | null
  base_currency?: string
  cost_basis_known?: boolean
  consumptions?: DisposalConsumption[]
  realized_gain?: number | null
  realized_gain_base?: number | null
  // Income-record change display (record_income; mirrors backend ai/tools/income.py).
  income_kind?: string // dividend | interest | distribution
  gross_amount?: number | null
  tax_withheld?: number | null
  net_amount?: number | null
  is_drp?: boolean
  base_amount?: number | null
  paid_at?: string
  // Agent-authoring change display (agent_create / agent_update; mirrors backend
  // ai/tools/agents.py _agent_display). `name`/`tools_used`/`allowed_tools`/`changed_fields`
  // are reused from the fields above.
  description?: string
  prompt_preview?: string
  prompt_old_preview?: string
  prompt_old?: string
  // D7 (RFC 2026-07-20 §14.2): when the model supplied an EDIT rather than a whole new
  // prompt/body, the card renders the fragment itself — the payload already IS the delta, so
  // there is nothing to derive and no surrounding body to truncate. Present INSTEAD of
  // prompt_preview on an edit call. `body_edit` is the skill-body twin.
  prompt_edit?: TextEditDisplay
  body_edit?: TextEditDisplay
  model?: string | null
  channels?: string[] | null
  enabled?: boolean
  // Auto-apply grant summary (P3-AG4) — non-null only when the agent is granted an APPLY
  // capability; null/absent ⇒ propose-only (the card says so).
  auto_apply?: {
    capabilities: string[]
    unattended_capabilities?: string[]
    max_writes_per_run?: number | null
    max_amount_base?: number | null
  } | null
  // External-MCP call display (external_mcp_call; mirrors backend mcp/client_manager.py
  // _build_proposed_change). `name` = the external tool name; `server` = the server label.
  server?: string
  args?: Record<string, unknown>
  is_money_movement?: boolean
  // Batch display (`registry.stage_batch` kinds: update_batch, lot_dispose_batch, …): the
  // total `count` and a capped `rows` sample, read by BatchBody.
  count?: number
  rows?: unknown[]
  // Ad-hoc code-execution display (code_run; mirrors backend ai/tools/code.py). `source` is
  // the model-authored script the user approves; `purpose` is the one-line reason.
  source?: string
  purpose?: string
  language?: string
  script_id?: string
  // Planning / budget-op display (assign / move_money / set_target / group ops; mirrors
  // backend ai/tools/planning.py `_proposed` display dicts). `name` is reused from above.
  op?: string
  month?: string
  category?: string
  amount?: number | null
  from_category?: string
  to_category?: string
  target_type?: string | null
  date?: string | null
  snooze_until?: string | null
  group_id?: string
  categories?: string[]
  order?: string[]
}

export interface ToolPreview {
  kind: string
  data?: Record<string, unknown>
  raw_arguments?: Record<string, unknown>
}

// First-class change types that ChangePreview renders with a bespoke body. Any OTHER
// `type` string still renders a non-empty body via the generic fallback (so no proposed
// change is ever a blind header+buttons). Keep in sync with the backend tools' emitted
// `type` strings.
export type ProposedChangeType =
  | 'update'
  | 'add'
  | 'remove'
  | 'skill_create'
  | 'skill_update'
  | 'skill_delete'
  | 'lot_acquire'
  | 'lot_dispose'
  | 'record_income'
  | 'agent_create'
  | 'agent_update'
  | 'external_mcp_call'
  | 'code_run'
  | 'code_skill_create'
  | 'txn_create'
  | 'txn_update'
  | 'txn_bulk'

export interface ProposedChange {
  // A known type renders a bespoke body; an unrecognized one (e.g. a future tool's change)
  // still renders via the generic fallback — never a blind header. `(string & {})` keeps the
  // known-type autocomplete while accepting any backend-emitted string.
  type: ProposedChangeType | (string & {})
  id?: string // tool_use_id — identifies the change for persistent confirmed state
  // Persisted user response to this card. Once set, ChangePreview renders the
  // disabled ✓/✗ state instead of the Confirm/Dismiss buttons, so a re-render
  // from storage (reload / conversation switch / panel reopen) can't re-fire it.
  status?: 'confirmed' | 'dismissed'
  // permissions_v2 only: WHICH of the three answers the owner gave, stored with the card so a
  // reload (or a conversation switch) still reads "Approved", "Always allowed" or "Denied" —
  // `status` alone cannot tell an approval from an Always allow. Approved runs this call once;
  // Always allowed runs it AND stores the permission (chat: global; Automation run: that
  // Automation's list); Denied refuses this call only and stores nothing.
  resolution?: 'approved' | 'always_allowed' | 'denied'
  // The server REFUSED to redeem this approval (already answered elsewhere, or superseded by a
  // later message) — nothing ran. Set alongside clearing `status`, so a card the user tapped
  // stops claiming "✓ Approved" above the red error (ADR `2026-07-30-approvals-never-expire`).
  // A separate flag rather than a `status` value because ChangePreview's optimistic local tick
  // has to be cleared too, and only an explicitly-changing prop survives React's batching of
  // the tap's own updates. Cleared again on the next tap.
  refused?: boolean
  // The decision never REACHED the server (network drop, server restarting, non-2xx), so the
  // staged call is still pending there and the card must offer its buttons again — the
  // opposite of `refused`. A flag of its own for the same batching reason. Cleared on the next tap.
  undelivered?: boolean
  position_id?: string // update / remove target
  instrument_id?: string // add
  account_id?: string
  account_name?: string
  quantity?: number
  cost_basis?: number | null
  disposed_at?: string // top-level on a lot_dispose change (spread from the apply payload)
  changes?: Record<string, unknown>
  reason?: string
  display?: PositionDisplay
  confirmed?: boolean
  // Skill-authoring change payload (mirrors backend ai/tools/skills.py proposed_change)
  skill?: Record<string, unknown> // skill_create
  skill_id?: string // skill_update / skill_delete
  // Agent-authoring change payload (mirrors backend ai/tools/agents.py proposed_change)
  agent?: Record<string, unknown> // agent_create
  agent_id?: string // agent_update
  // External-MCP call payload (external_mcp_call; mirrors backend mcp/client_manager.py).
  // Resolved by `id` (the tool_use_id above), NOT by a nonce — migrated 2026-08-22 off
  // apply_external_mcp_call + its nonce store onto the SAME generic resolve path
  // txn_create/txn_update/txn_bulk use (RFC 2026-07-22). server_id/tool/args are
  // DISPLAY-ONLY; the backend re-derives what actually fires from its own stored entry,
  // never from what this dict carries.
  //
  // There is NO `nonce` field. It was the second confirm mechanism — a token the MODEL had
  // to send back — and its last user, code_run's apply_run_code twin, was deleted on
  // 2026-08-25 (ADR one-confirm-mechanism-for-every-tool). Every card now resolves by `id`
  // (the tool_use_id), server-side. Do not reintroduce it.
  server_id?: string
  tool?: string
  args?: Record<string, unknown>
  is_money_movement?: boolean
  // Ad-hoc code-execution (code_run): the conversation this card belongs to, so the
  // frontend can route its response without prop-drilling. Not a secret, not a grant target.
  session_id?: string
  // Save-as-skill bridge (code_skill_create): the {id, source} script payload echoed back to
  // apply_code_skill on confirm (the model passes it through verbatim).
  script?: Record<string, unknown>
  // Ledger write-confirm payload (txn_create / txn_update / txn_bulk; mirrors backend
  // ai/tools/ledger.py proposed_change). Resolution (RFC 2026-07-22) is bound to `id`
  // (the tool_use_id above), NOT to any field below — the backend executes the STORED args
  // server-side and ignores whatever a client re-supplies, so what's rendered here is
  // provably what commits. There is no `confirm_token` any more: the model is never
  // involved in the approve→execute path.
  txn_kind?: 'expense' | 'income' | 'transfer' | 'investment' | 'adjustment'
  transaction_id?: string // txn_update target
  date?: string // ISO yyyy-mm-dd
  description?: string // txn_create: the drafted description; txn_update: the EXISTING row's
  memo?: string | null
  category?: TxnCategoryDisplay
  rule?: TxnRuleRef | null // the spend rule that FIRED, if any
  // What backend/services/ledger_preview.py actually emits since rules became Automations;
  // `rule` / `rules_applied` are the older names, still read for cards staged before that.
  automation?: TxnRuleRef | null
  payee?: TxnPayeeDisplay | null
  from?: TxnLegDisplay | null
  to?: TxnLegDisplay | null
  base?: TxnBaseDisplay | null
  splits?: TxnSplitLine[] | null
  tags?: string[]
  warnings?: Array<string | { type: string; account_id?: string; message: string }>
  fields?: TxnFieldDiff[] // txn_update: resolved before/after
  count?: number // txn_bulk: TOTAL rows in the batch (rows[] is a capped sample)
  import_description?: string | null
  rows?: TxnBulkRow[]
  truncated?: boolean // txn_bulk: true when rows.length < count
  totals?: TxnBulkTotals | null
  rules_applied?: TxnRuleApplied[]
  automations_applied?: TxnRuleApplied[]
  account_names?: string[] // txn_bulk: every account the WHOLE batch touches, not the sample
  // ── Tool-permission gate (RFC 2026-07-20 approval-and-review-model §13.1) ──────────────
  // Present ONLY when this card is asking permission to RUN A WRITE TOOL. Its presence is
  // the whole contract: the card swaps its bare Confirm/Dismiss for the three-way
  // [Allow once] [Always allow] [Deny], because those are meaningless on a card that is not
  // gating a tool.
  //
  // The CANONICAL tool name at the moment the card was raised — a grant is keyed on it and
  // deliberately does not survive a rename (§15.1). Deliberately NOT named `tool`: that key
  // is already taken by external_mcp_call's display-only tool name, and a permission grant
  // silently keying off an external server's tool would be exactly the kind of collision
  // this codebase has been burned by.
  grant_tool?: string
  // permissions_v2 only: present when the call belongs to an Automation run — the Automation's
  // id. It is what makes the card's Always allow write to THIS Automation's own list (routed
  // server-side from `session_id`), never to the global table.
  automation_id?: string
}

// Ledger write-confirm cards (txn_create / txn_update / txn_bulk; mirrors backend
// ai/tools/ledger.py proposed_change — tool_use_id-keyed like external_mcp_call since
// 2026-08-22; code_run is the one remaining confirm-token/nonce instance). The whole point
// of these types is to make the
// SILENT resolution inside create_transaction (category precedence explicit > rule >
// payee-default > None; spend rules) visible before the write commits — see the incident
// that motivated this: 17% of update_transaction calls were a redo of an edit the user
// only discovered was wrong after it had already booked.
export type TxnCategorySource = 'explicit' | 'rule' | 'payee_default' | 'none' | 'split'

export interface TxnCategoryDisplay {
  id: string | null // resolved stored category id; null = Uncategorized
  label: string // human label, or 'Uncategorized'
  source: TxnCategorySource
}

export interface TxnRuleRef {
  id: string
  name: string
}

export interface TxnPayeeDisplay {
  id: string | null
  name: string
  is_new: boolean
}

// One leg of a transaction (from XOR to for expense/income/investment; both for transfer).
export interface TxnLegDisplay {
  account_id: string | null
  account_name: string
  amount: number | null
  currency: string | null
}

// The historical-rate stamp (ADR 2026-07-09) — base_amount/base_currency/fx_rate as
// recorded at write time, never revalued at the current market rate.
export interface TxnBaseDisplay {
  amount: number | null
  currency: string | null
  fx_rate: number | null
}

export interface TxnSplitLine {
  category_id: string | null
  category_label: string
  amount: number
  memo?: string | null
}

// One row of a txn_update's resolved before/after diff (already human-labeled by the
// backend — the field key is a fallback only).
export interface TxnFieldDiff {
  field: string
  label: string
  before: unknown
  after: unknown
}

// One row of a txn_bulk display sample (<= 12 of the batch — NOT necessarily all of it).
export interface TxnBulkRow {
  date: string
  description: string
  category_label: string
  category_source: string
  amount: number | null
  currency: string | null
  base_amount: number | null
  account_name: string
}

export interface TxnBulkTotals {
  base_currency: string
  inflow_base: number
  outflow_base: number
}

export interface TxnRuleApplied {
  id: string
  name: string
  count: number
}

export interface SearchResult {
  title: string
  url: string
}

export interface SearchCall {
  query: string
  results: SearchResult[]
  loading: boolean
}

export type ToolStatus = 'streaming' | 'executing' | 'done'

/**
 * What a tool call SAYS, resolved by the server (`backend/ai/tools/display.py`).
 *
 * The client used to answer this itself, from a 15-entry label table against a 290-tool
 * registry plus an unbounded set of external MCP tools — everything else rendered its raw
 * name (`mcp__playwright__browser_navigate`). Drawing one row also meant joining three
 * unrelated client-side sources: the tool name, the connectors read, and the brand catalog.
 *
 * It arrives on the live `tool_use_start` event AND on a rehydrated transcript's tool block,
 * because the server labels on READ — so a conversation recorded before any of this existed
 * renders exactly like one recorded after it, with no legacy path here to keep alive.
 *
 * Optional only for the gap where a client is newer than the backend it is talking to; the
 * row falls back to the raw name, which is what it always used to show.
 */
export interface ToolDisplay {
  /** The human sentence. Already sanitized and capped server-side. */
  label: string
  /** Closed-set class — keys the icon (`toolActivity/icons.ts`) and half the grouping key. */
  verb: string
  /** What was acted on. The other half of the grouping key, and what the summary counts. */
  noun: string
  /** `plaid` / `web_search:tavily` / `mcp:<server>` / `core`. */
  owner_id: string
  /** Breadcrumb name, or '' when it would add nothing (an unscoped `core` tool). */
  owner_name: string
  /** One input value worth showing beside the label, or ''. */
  detail: string
}

export interface PotentialDuplicate {
  id: string
  date: string
  description: string
  amount: number
  direction?: string
  from_account_id?: string | null
  to_account_id?: string | null
  account_id?: string | null
  duplicates_count: number
}

/** What `create_artifact` can author. html/svg render in the sandboxed iframe; pdf/xlsx/csv are
 * FILES — see utils/artifactFormats.ts. */
export type ArtifactType = 'html' | 'svg' | 'pdf' | 'xlsx' | 'csv'

/** Compact reference to a stored chat artifact (HTML/SVG/PDF/XLSX/CSV document Lucius authored via
 * create_artifact). Mirrors backend assistant._artifact_event_payload — never carries the
 * document bytes; ArtifactCard fetches those from GET /api/artifacts/{id} on demand. */
export interface ArtifactRef {
  id: string
  title: string
  filename: string
  artifactType: ArtifactType
  sizeBytes?: number
}

/** A mention row as the server hydrates it (backend: services/mentions/labels.py). Same
 * shape the @-picker emits, so ONE chip renderer serves both surfaces. */
export interface ReceiptMention {
  type: string
  id: string
  label: string
  sublabel: string
  value: string
  icon: string
  surface: string
  color?: string
  /** The entity could not be read back (hard-deleted, or its store failed). The row is
   *  still emitted — dropping it would shrink a receipt whose counts say otherwise. */
  stale?: boolean
}

/** One written row on a write-receipt chip (backend: services/write_receipt.py).
 *
 * Any ENTITY TYPE, not just transactions: a turn that books a transaction, assigns an
 * envelope and records a vest produces one chip with three rows of three types. The
 * receipt carries only the entity identity and operation. */
export interface WriteReceiptItem {
  mention: ReceiptMention
  op: 'create' | 'update' | 'delete'
}

/** One per-type head group — `{type, count, ops}`, most rows first. This is what lets the
 * head say WHICH thing changed ("1 envelope edited") instead of a bare "1 edited". */
export interface WriteReceiptGroup {
  type: string
  count: number
  ops: string[]
}

/**
 * What ONE turn wrote — the write-then-verify chip (ADR 2026-07-20).
 *
 * The write has already committed by the time this arrives; the chip exists so the user can
 * verify it, not approve it. `items` is a SAMPLE (`showing`) while every count is exact over
 * the whole write. There is no Undo here (owner, 2026-07-20) — the writes stay recoverable
 * via the turn checkpoint, that just is not this component's job.
 */
export interface WriteReceipt {
  created: number
  updated: number
  deleted: number
  total: number
  showing: number
  groups: WriteReceiptGroup[]
  items: WriteReceiptItem[]
  checkpoint_id?: string
}

export type MessageBlock =
  | { type: 'text'; content: string }
  /**
   * One round's reasoning, in the position it happened.
   *
   * NOT the same thing as `Message.thinking`, which concatenates every round's thinking into
   * one string and is rendered above the whole bubble. A turn that thinks, calls a tool, then
   * thinks again produced one blob sitting above the first tool row — including the half that
   * came after it. The ordering was never lost, only discarded: each provider round appends
   * its own thinking block to the transcript, interleaved with that round's tool calls.
   *
   * `Message.thinking` is still populated, as the fallback for a transcript recorded before
   * this existed.
   */
  | { type: 'thinking'; content: string }
  | {
      type: 'skill'
      id: string
      skillId: string
      name: string
      status: 'loading' | 'loaded' | 'failed'
      source: 'user' | 'model'
      error?: string
    }
  | {
      type: 'tool'
      name: string
      id: string
      status: ToolStatus
      /** Server-resolved label/owner/grouping — see `ToolDisplay`. */
      display?: ToolDisplay
      input?: Record<string, unknown>
      result?: string
      /** Set (to the original char count) when the transcript endpoint stripped an
       *  oversized result/input from the list payload — tap fetches the full block
       *  from GET /api/sessions/{id}/tool-results/{tool_use_id}. Distinct from a
       *  result that never existed (a pending approval). */
      resultElided?: number
      inputElided?: number
    }
  | { type: 'search'; query: string; results: SearchResult[]; loading: boolean }
  | { type: 'fetch'; url: string }
  | { type: 'change'; change: ProposedChange }
  | { type: 'duplicate_review'; duplicates: PotentialDuplicate[]; batchId?: string }
  | { type: 'artifact'; artifact: ArtifactRef }
  | { type: 'receipt'; receipt: WriteReceipt }
  | {
      type: 'screen_command'
      command: { name: string; args: Record<string, unknown> }
      open?: { route?: string }
    }

export interface TurnUsage {
  input_tokens?: number
  cache_creation_tokens?: number
  cache_read_tokens?: number
  output_tokens?: number
  cost_usd: number
  /** The model that actually answered this turn. */
  model?: string
  /** False when no price is known for the model — its $0 means unmeasured, not free. */
  price_known?: boolean
}

export interface CheckpointFileChange {
  file: string
  current_count: number
  restored_count: number
  delta: number
}

export interface CrossSessionChange {
  session_label: string // "Telegram", "web", …
  message_preview: string
  created_at: string
}

export interface Checkpoint {
  id: string
  session_id?: string
  created_at?: string
  message_preview?: string
  files: string[]
  status?: string
  kind?: string
}

/** A cited source rendered as a chip under an assistant answer. Mirrors the prototype's
 * CitationChips data shape (app.jsx:537–549). `url` is optional — no-op click if absent. */
export interface Citation {
  n: number
  title?: string
  source?: string
  url?: string
}

export interface Message {
  id: string
  role: MessageRole
  text: string
  thinking?: string
  streaming?: boolean
  toolCalls?: string[]
  searches?: SearchCall[]
  fetches?: string[]
  blocks?: MessageBlock[]
  proposedChanges?: ProposedChange[]
  attachments?: Attachment[]
  cites?: Citation[]
  error?: string
  setupHint?: boolean // true when the error is a "no/invalid AI key" setup issue
  // The turn was CANCELLED, not failed — a Stop, or a server restart taking down whatever
  // was in flight. Server-set (assistant.rehydrate_messages), and it rides the same `error`
  // field because the text is the explanation either way; only the styling differs. Red
  // would be wrong: nothing broke, and on a restart nobody even pressed anything.
  notice?: boolean
  usage?: TurnUsage
  // The turn PAUSED on a confirm/permission card rather than finishing (RFC 2026-07-22): the
  // model is still mid-task and resumes server-side the moment the card is answered. Rendered
  // as "not done yet" — no cost chip — because a chip is the visual full stop of a turn, and
  // showing one here reads as "finished" while the work is only waiting on the user.
  awaitingApproval?: boolean
  // The Automation whose instruction sheet this message is — set by the server on the
  // opening message of an Automation run's conversation (assistant.rehydrate_messages).
  // The panel renders it as a one-line "Ran <name>" header instead of the full brief, and
  // links it through to the run when the ids resolved. Absent on every ordinary message.
  // A conversation cached by an older build carries the bare NAME here — read it through
  // `runBrief()`, never off the field directly.
  automationBrief?: string | { name: string; automationId?: string; runId?: string }
  checkpointId?: string // set when this user turn created a restorable checkpoint
  restored?: boolean // set after this checkpoint has been restored
  undoCheckpointId?: string // pre-restore safety checkpoint — lets the user undo the restore
  // A restore of an EARLIER message rewound past this turn: no Restore while that restore
  // stands; undoing it clears the flag (the checkpoint is kept for exactly that).
  checkpointSuperseded?: boolean
  // Set the instant sendMessage() queues this (user) message into an already-busy
  // conversation, instead of opening a fresh turn (RFC mid-turn injection, acceptance C1/
  // C2). 'queued' is the only value today — there is no error variant (C3: a turn that
  // dies before draining the queue resends automatically, it never just drops the
  // message). Cleared the instant the server's message_delivered event names this id —
  // which, for a message that was pending, is also the moment it moves out of
  // chatSessions.ConvSlice.queuedMessages into `messages` (see `ts` below).
  pending?: 'queued'
  // Epoch ms — when this message actually entered the true timeline. For a user message
  // sent while idle, or any assistant message, that is "now" at creation time. For a
  // message that was QUEUED (sent while busy), it is deliberately NOT stamped at send
  // time — it is stamped the instant it is actually delivered/processed
  // (chatReducer.deliverQueuedMessage), which is its true chronological position. Two
  // messages delivered in the same event-loop tick are still guaranteed strictly
  // increasing (see `nextTimestamp`), so display order never ties.
  ts?: number
}
