// PayeesModal.tsx — Payees manager (master-detail).
// PMSelect imported from ./ds. PMConfirmDialog is local only.
import { useState, useEffect, useRef } from 'react'
import { Modal, Button, TextField, Select, Checkbox } from '../../ui'
import { EntityRow } from '../../../lucius/EntityRow'
import { useIsMobile } from '../../../hooks/useIsMobile'
import type { Category, Payee } from './types'
import { PMSelect } from './ds'
import { fDate } from '../../../utils/format'
import { AddCategoryField } from './pickers'
import { usePayeeUsage } from './usePayeeUsage'
import { Plus, ChevronRight, ChevronLeft, X } from '../../../ui/icons'

// Format ISO date for display; returns '' for null/undefined.
function fmtDate(s: string | null | undefined): string {
  return s ? fDate(s, 'short', 'always') : ''
}

interface PMConfirmData {
  title: string
  body: string
  confirmLabel: string
  danger?: boolean
  onConfirm: () => void
}

function PMConfirmDialog({ data, onClose }: { data: PMConfirmData | null; onClose: () => void }) {
  if (!data) return null
  return (
    <Modal
      open
      onClose={onClose}
      title={data.title}
      size="sm"
      className="pm-confirm"
      footer={
        <div className="pm-confirm-actions">
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant={data.danger ? 'danger' : 'primary'}
            onClick={() => {
              data.onConfirm()
              onClose()
            }}
          >
            {data.confirmLabel}
          </Button>
        </div>
      }
    >
      <div className="pm-confirm-body">{data.body}</div>
    </Modal>
  )
}

// ── Props ─────────────────────────────────────────────────────────────────

export interface PayeesModalProps {
  open: boolean
  payees: Payee[]
  categories: Category[]
  onClose: () => void
  onChange: (id: string, patch: Partial<Payee>) => void
  onMerge: (fromId: string, toId: string) => void
  onView: (payeeName: string) => void // open register filtered to payee
  onAdd: (name: string) => string // creates payee, returns new id
  onDelete: (id: string, reassignTo?: string) => void
  onApplyRules?: () => void
  onAddRule?: (payeeId: string, matchType: string, value: string) => void
  onRemoveRule?: (payeeId: string, index: number) => void
  onApplyCategory?: (payeeId: string) => void
  onApplyPayeeRules?: (payeeId: string) => void
  onRemoveAlias?: (payeeId: string, alias: string) => void
}

// ── Component ─────────────────────────────────────────────────────────────

export function PayeesModal({
  open,
  payees: listedPayees,
  categories,
  onClose,
  onChange,
  onMerge,
  onView,
  onAdd,
  onDelete,
  onApplyRules,
  onAddRule,
  onRemoveRule,
  onApplyCategory,
  onApplyPayeeRules,
  onRemoveAlias,
}: PayeesModalProps) {
  const { payees, status: usageStatus } = usePayeeUsage(listedPayees)
  const usageKnown = usageStatus === 'ready'
  const isMobile = useIsMobile()
  const [q, setQ] = useState('')
  const [selId, setSelId] = useState<string | null>(payees[0]?.id ?? null)
  const [mobileView, setMobileView] = useState<'list' | 'detail'>('list')
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [rulesById, setRulesById] = useState<
    Record<string, Array<{ type: string; value: string }>>
  >({})
  const [ruleType, setRuleType] = useState('contains'),
    [ruleVal, setRuleVal] = useState(''),
    [ruleErr, setRuleErr] = useState('')
  const [mergeTo, setMergeTo] = useState(''),
    [reassignTo, setReassignTo] = useState('')
  const [createErr, setCreateErr] = useState(''),
    [nameDraft, setNameDraft] = useState(''),
    [nameErr, setNameErr] = useState('')
  const [applyRulesMsg, setApplyRulesMsg] = useState(''),
    [applyCatMsg, setApplyCatMsg] = useState('')
  const [confirm, setConfirm] = useState<PMConfirmData | null>(null)
  const [fmode, setFmode] = useState('all'),
    [sortBy, setSortBy] = useState('count'),
    [historyMsg, setHistoryMsg] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  const catLabel = (k: string | null | undefined) =>
    (categories.find((c) => c.id === k) || {}).label || ''
  const nameTaken = (name: string, exceptId?: string) =>
    payees.some(
      (p) => p.id !== exceptId && p.name.trim().toLowerCase() === name.trim().toLowerCase(),
    )

  useEffect(() => {
    if (!payees.length) {
      setSelId(null)
      return
    }
    if (!payees.some((p) => p.id === selId)) setSelId(payees[0]!.id)
  }, [payees]) // eslint-disable-line

  useEffect(() => {
    if (!isMobile) setMobileView('list')
  }, [isMobile])

  useEffect(() => {
    setMergeTo('')
    setReassignTo('')
    setRuleVal('')
    setNameErr('')
    setApplyRulesMsg('')
    setApplyCatMsg('')
    setConfirm(null)
    setRuleErr('')
    setNameDraft((payees.find((p) => p.id === selId) || {}).name || '')
  }, [selId]) // eslint-disable-line

  function moveSel(id: string) {
    setSelId(id)
    requestAnimationFrame(() => {
      listRef.current?.querySelector<HTMLElement>('[data-pid="' + id + '"]')?.focus()
    })
  }

  function selectPayee(id: string) {
    setSelId(id)
    if (isMobile) setMobileView('detail')
  }

  let visible = payees.filter((p) => p.name.toLowerCase().includes(q.toLowerCase().trim()))
  if (fmode === 'uncat') visible = visible.filter((p) => !p.defaultCategories?.expense)
  else if (fmode === 'hidden') visible = visible.filter((p) => p.hidden)
  visible = visible
    .slice()
    .sort((a, b) => (sortBy === 'name' ? a.name.localeCompare(b.name) : b.txnCount - a.txnCount))

  const sel = payees.find((p) => p.id === selId) || null
  const others = payees.filter((p) => sel && p.id !== sel.id)
  const totalTxns = payees.reduce((a, p) => a + (p.txnCount || 0), 0)
  // Per-kind default: options for one transaction kind — its own categories first, then the
  // cross-kind labels that are still legal to pick (income refunds carry an expense category;
  // a transfer/investment may carry any label). Grouped by kind so the natural picks lead.
  const KIND_LABEL: Record<string, string> = {
    expense: 'Expense',
    income: 'Income',
    transfer: 'Transfer',
    investment: 'Investment',
  }
  const optsForKind = (kind: string) => {
    const order =
      kind === 'expense'
        ? ['expense']
        : kind === 'income'
          ? ['income', 'expense']
          : [kind, 'expense', 'income']
    const out: Array<Category & { group: string }> = [
      {
        id: '',
        label: 'None',
        color: 'transparent',
        group: KIND_LABEL[kind] || 'Other',
        kind: kind as Category['kind'],
      },
    ]
    for (const g of order)
      for (const c of categories) if (c.kind === g) out.push({ ...c, group: KIND_LABEL[g] ?? g })
    return out
  }
  const DEFAULT_KINDS: Array<{ kind: string; label: string }> = [
    { kind: 'expense', label: 'Expense' },
    { kind: 'income', label: 'Income' },
    { kind: 'transfer', label: 'Transfer' },
    { kind: 'investment', label: 'Investment' },
  ]
  // The current per-kind map.
  const perKindMap = (p: Payee): Record<string, string> => ({ ...(p.defaultCategories || {}) })
  const setPerKind = (p: Payee, kind: string, catId: string) => {
    // An explicit null tells the server "Uncategorized on purpose", so it stops learning that
    // kind's default from new transactions; the server stores the map without it.
    const m: Record<string, string | null> = perKindMap(p)
    m[kind] = catId || null
    onChange(p.id, { defaultCategories: m as Payee['defaultCategories'] })
  }
  const aliasesFor = (p: Payee) => (p.aliases.length > 0 ? p.aliases : [p.name.toLowerCase()])
  const rules = sel ? rulesById[sel.id] || [] : []

  function createPayee() {
    const name = newName.trim()
    if (!name) {
      setCreating(false)
      return
    }
    if (nameTaken(name)) {
      setCreateErr('A payee named "' + name + '" already exists.')
      return
    }
    const id = onAdd(name)
    setCreating(false)
    setNewName('')
    setCreateErr('')
    if (id) setSelId(id)
  }

  function editName(v: string) {
    setNameDraft(v)
    const t = v.trim()
    if (!t) {
      setNameErr('Name cannot be empty.')
      return
    }
    if (nameTaken(t, sel!.id)) {
      setNameErr('Another payee already uses this name.')
      return
    }
    setNameErr('')
    onChange(sel!.id, { name: t })
  }

  // Hydrate rules from the server-loaded payees (re-syncs after every add/remove → refresh).
  useEffect(() => {
    const m: Record<string, Array<{ type: string; value: string }>> = {}
    for (const p of payees)
      if (p.rules?.length) m[p.id] = p.rules.map((r) => ({ type: r.match_type, value: r.value }))
    setRulesById(m)
  }, [payees])

  function addRule() {
    const v = ruleVal.trim()
    if (!v || !sel) return
    if (ruleType === 'regex') {
      try {
        new RegExp(v)
      } catch {
        setRuleErr('Invalid regular expression.')
        return
      }
    }
    setRuleErr('')
    setRulesById((m) => ({
      ...m,
      [sel.id]: (m[sel.id] || []).concat([{ type: ruleType, value: v }]),
    })) // optimistic
    onAddRule?.(sel.id, ruleType, v) // persist (server refresh re-hydrates)
    setRuleVal('')
  }

  function removeRule(i: number) {
    setRulesById((m) => ({ ...m, [sel!.id]: (m[sel!.id] || []).filter((_, j) => j !== i) })) // optimistic
    onRemoveRule?.(sel!.id, i) // persist
  }

  function removeAlias(alias: string) {
    onRemoveAlias?.(sel!.id, alias) // persist (server refresh re-hydrates the chip list)
  }

  function doDelete() {
    if (!sel) return
    const nextId = (payees.find((p) => p.id !== sel.id) || {}).id || null
    onDelete(sel.id, sel.txnCount > 0 ? reassignTo || undefined : undefined)
    setSelId(nextId)
  }

  function requestDelete() {
    if (!sel) return
    setConfirm({
      title: 'Delete payee?',
      body:
        sel.txnCount > 0
          ? `${sel.name}'s ${sel.txnCount} transaction${sel.txnCount !== 1 ? 's' : ''} will be reassigned to ${(others.find((o) => o.id === reassignTo) || {}).name}, then ${sel.name} is permanently deleted. This can't be undone.`
          : `${sel.name} has no transactions and will be permanently deleted. This can't be undone.`,
      confirmLabel: 'Delete payee',
      danger: true,
      onConfirm: doDelete,
    })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title="Manage payees"
      footer={
        <Button size="sm" variant="primary" className="pm-done" onClick={onClose}>
          Done
        </Button>
      }
    >
      <div className={`pm-body${isMobile ? ` pm-mobile pm-mobile--${mobileView}` : ''}`}>
        <div
          className="pm-listcol"
          aria-hidden={isMobile && mobileView !== 'list' ? true : undefined}
        >
          <div className="pm-tools">
            <TextField
              placeholder="Search payees…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Button
              size="sm"
              variant="secondary"
              className="pm-newbtn"
              onClick={() => {
                setCreating((c) => !c)
                setNewName('')
              }}
            >
              <Plus size={13} aria-hidden="true" /> New
            </Button>
          </div>
          {creating && (
            <div className="pm-create">
              <TextField
                autoFocus
                invalid={!!createErr}
                placeholder="Payee name…"
                value={newName}
                onChange={(e) => {
                  setNewName(e.target.value)
                  setCreateErr('')
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') createPayee()
                  if (e.key === 'Escape') {
                    setCreating(false)
                    setCreateErr('')
                  }
                }}
              />
              {createErr && <div className="pm-err">{createErr}</div>}
              <div className="pm-create-row">
                <Button size="sm" variant="primary" onClick={createPayee}>
                  Create
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setCreating(false)
                    setCreateErr('')
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
          <div className="pm-filterbar">
            <div className="pm-seg">
              <button
                type="button"
                className={'pm-seg-btn' + (fmode === 'all' ? ' on' : '')}
                onClick={() => setFmode('all')}
              >
                All
              </button>
              <button
                type="button"
                className={'pm-seg-btn' + (fmode === 'uncat' ? ' on' : '')}
                onClick={() => setFmode('uncat')}
              >
                {isMobile ? 'Uncategorized' : 'Uncat.'}
              </button>
              <button
                type="button"
                className={'pm-seg-btn' + (fmode === 'hidden' ? ' on' : '')}
                onClick={() => setFmode('hidden')}
              >
                Hidden
              </button>
            </div>
            <PMSelect
              compact
              className="pm-sort"
              options={[
                { value: 'count', label: 'Most used' },
                { value: 'name', label: 'A–Z' },
              ]}
              value={sortBy}
              onChange={(v) => setSortBy(v)}
            />
          </div>
          <div className="pm-list" ref={listRef}>
            {visible.map((p) => (
              <EntityRow
                key={p.id}
                as="button"
                htmlType="button"
                data-pid={p.id}
                type="payee"
                id={p.id}
                label={p.name}
                search={p.name}
                item={p}
                toFields={(it) => ({
                  name: it.name,
                  default_category: catLabel(it.defaultCategories?.expense) || null,
                  txn_count: it.txnCount,
                  hidden: it.hidden ? 'Hidden' : null,
                })}
                className={'pm-item' + (p.id === selId ? ' sel' : '') + (p.hidden ? ' hid' : '')}
                onClick={() => selectPayee(p.id)}
                onKeyDown={(e) => {
                  const i = visible.findIndex((x) => x.id === p.id)
                  if (e.key === 'ArrowDown') {
                    e.preventDefault()
                    const nx = visible[Math.min(i + 1, visible.length - 1)]
                    if (nx) moveSel(nx.id)
                  } else if (e.key === 'ArrowUp') {
                    e.preventDefault()
                    const pv = visible[Math.max(i - 1, 0)]
                    if (pv) moveSel(pv.id)
                  }
                }}
              >
                <span className="pm-item-tx">
                  <span className="pm-item-nm">{p.name}</span>
                  <span className="pm-item-sub">
                    {catLabel(p.defaultCategories?.expense) || 'Uncategorized'}
                    {p.hidden ? ' • hidden' : ''}
                  </span>
                </span>
                <span className="pm-item-ct">{usageKnown ? p.txnCount : '–'}</span>
                {isMobile && (
                  <span className="pm-item-chev" aria-hidden="true">
                    <ChevronRight size={16} />
                  </span>
                )}
              </EntityRow>
            ))}
            {visible.length === 0 && (
              <div className="pm-empty">
                {payees.length === 0 ? 'No payees yet.' : 'No payees match.'}
              </div>
            )}
          </div>
          {historyMsg && <div className="pm-foot-msg">{historyMsg}</div>}
          <div className="pm-foot">
            <span>
              {payees.length} payee{payees.length !== 1 ? 's' : ''}
            </span>
            {onApplyRules && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  setConfirm({
                    title: 'Apply all rules to history?',
                    body: `Re-runs every payee's renaming rules across all ${totalTxns} transactions and re-assigns payees on matches. Manual payee assignments may be overwritten.`,
                    confirmLabel: 'Apply to history',
                    onConfirm: () => {
                      onApplyRules!()
                      setHistoryMsg(`Rules applied across all ${totalTxns} transactions.`)
                      setTimeout(() => setHistoryMsg(''), 4000)
                    },
                  })
                }
              >
                Apply rules to history
              </Button>
            )}
          </div>
        </div>

        <div
          className="pm-main"
          aria-hidden={isMobile && mobileView !== 'detail' ? true : undefined}
        >
          {isMobile && (
            <div className="pm-mobile-nav">
              <Button
                size="sm"
                variant="ghost"
                className="pm-mobile-back"
                onClick={() => setMobileView('list')}
                aria-label="Back to payees"
              >
                <ChevronLeft size={14} aria-hidden="true" /> Payees
              </Button>
            </div>
          )}
          {!sel ? (
            payees.length === 0 ? (
              <div className="pm-emptybig">
                <span className="pm-emptybig-ic">
                  <svg
                    width="22"
                    height="22"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                    <line x1="7" y1="7" x2="7.01" y2="7" />
                  </svg>
                </span>
                <div className="pm-emptybig-title">No payees yet</div>
                <div className="pm-emptybig-sub">
                  Payees appear here automatically as you tag transactions — or add one manually.
                </div>
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => {
                    setCreating(true)
                    setNewName('')
                  }}
                >
                  <Plus size={14} aria-hidden="true" /> New payee
                </Button>
              </div>
            ) : (
              <div className="pm-editor-empty">Select a payee to edit its details.</div>
            )
          ) : (
            <div key={sel.id}>
              <div className="pm-ed-head">
                <div className="pm-ed-headtext">
                  <h3 className="pm-ed-title">{sel.name}</h3>
                  <div className="pm-ed-sub">
                    {!usageKnown
                      ? usageStatus === 'error'
                        ? 'Usage unavailable'
                        : 'Counting transactions…'
                      : fmtDate(sel.lastUsed)
                        ? `Last used ${fmtDate(sel.lastUsed)}`
                        : 'Never used'}
                  </div>
                </div>
                {!usageKnown ? null : sel.txnCount > 0 ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onView(sel.name)}
                  >{`Show ${sel.txnCount} transaction${sel.txnCount !== 1 ? 's' : ''}`}</Button>
                ) : (
                  <span className="pm-ed-count">{`${sel.txnCount} txns`}</span>
                )}
              </div>

              <div className="pm-field">
                <label className="pm-label">Display name</label>
                <TextField
                  invalid={!!nameErr}
                  value={nameDraft}
                  onChange={(e) => editName(e.target.value)}
                />
                {nameErr && <div className="pm-err">{nameErr}</div>}
              </div>

              <div className="pm-field">
                <label className="pm-label">Default category by kind</label>
                <p className="pm-sec-hint">
                  A default is applied to new transactions of its own kind only — an expense default
                  never lands on income, a transfer, or an investment.
                </p>
                {DEFAULT_KINDS.map(({ kind, label }) => (
                  <div className="pm-kindrow" key={kind}>
                    <span className="pm-kindlabel">{label}</span>
                    {typeof AddCategoryField !== 'undefined' ? (
                      <AddCategoryField
                        value={perKindMap(sel)[kind] || ''}
                        options={optsForKind(kind)}
                        advance={() => {}}
                        onChange={(k: string) => setPerKind(sel, kind, k)}
                      />
                    ) : (
                      <Select
                        options={optsForKind(kind).map((o) => ({ value: o.id, label: o.label }))}
                        value={perKindMap(sel)[kind] || ''}
                        onChange={(e) => setPerKind(sel, kind, e.target.value)}
                      />
                    )}
                  </div>
                ))}
              </div>

              <div className="pm-checks">
                <Checkbox
                  label="Apply default categories to new transactions"
                  checked={sel.autocategorize !== false}
                  onChange={(e) => onChange(sel.id, { autocategorize: e.target.checked })}
                />
                <Checkbox
                  label="Hide from payee suggestions"
                  checked={!!sel.hidden}
                  onChange={(e) => onChange(sel.id, { hidden: e.target.checked })}
                />
              </div>
              <div className="pm-autohint">Changes above save automatically.</div>

              {Object.keys(perKindMap(sel)).length > 0 && sel.txnCount > 0 && (
                <div className="pm-subaction">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      setConfirm({
                        title: 'Apply defaults to existing transactions?',
                        body: `Each of ${sel.name}'s transactions will be re-categorized to that kind's default (e.g. expenses → "${catLabel(perKindMap(sel).expense) || '—'}") — this overwrites their current category.`,
                        confirmLabel: 'Apply defaults',
                        onConfirm: () => {
                          onApplyCategory?.(sel.id)
                          setApplyCatMsg(
                            'Re-categorized existing transactions to their per-kind defaults.',
                          )
                        },
                      })
                    }
                  >
                    Apply defaults to existing transactions
                  </Button>
                  {applyCatMsg && <span className="pm-ok">{applyCatMsg}</span>}
                </div>
              )}

              <hr className="pm-divider" />

              <div className="pm-section">
                <div className="pm-sec-title">Auto-matched descriptions</div>
                <p className="pm-sec-hint">
                  These normalized strings already route to this payee automatically — built up as
                  transactions were imported. Remove any that were folded in by mistake; to add new
                  patterns, use <strong>Renaming rules</strong> below.
                </p>
                <div className="pm-chips">
                  {aliasesFor(sel).map((a) => (
                    <span className="pm-chip" key={a}>
                      {a}
                      {sel.aliases.length > 0 && (
                        <button
                          className="pm-chip-x"
                          aria-label={`Remove auto-match ${a}`}
                          onClick={() => removeAlias(a)}
                        >
                          <X size={11} aria-hidden="true" />
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              </div>

              <div className="pm-section">
                <div className="pm-sec-title">Renaming rules</div>
                <p className="pm-sec-hint">
                  Imported descriptions that match any rule are routed to this payee — before
                  normalization. Use this to handle processor prefixes or reference IDs (e.g. "Grab*
                  A-9BEIP3LGWFBJAV" → contains "grab").
                </p>
                <div className="pm-chips">
                  {rules.length === 0 ? (
                    <span className="pm-chips-empty">No rules yet.</span>
                  ) : (
                    rules.map((r, i) => (
                      <span className="pm-chip pm-chip-rule" key={i}>
                        <span className="pm-chip-type">{r.type}</span>
                        {r.value}
                        <button
                          className="pm-chip-x"
                          aria-label="Remove rule"
                          onClick={() => removeRule(i)}
                        >
                          <X size={11} aria-hidden="true" />
                        </button>
                      </span>
                    ))
                  )}
                </div>
                <div className="pm-ruleadd">
                  <PMSelect
                    className="pm-ruletype"
                    options={[
                      { value: 'contains', label: 'contains' },
                      { value: 'is', label: 'is' },
                      { value: 'regex', label: 'regex' },
                    ]}
                    value={ruleType}
                    onChange={(v) => setRuleType(v)}
                  />
                  <TextField
                    invalid={!!ruleErr}
                    placeholder="Pattern to match…"
                    value={ruleVal}
                    onChange={(e) => {
                      setRuleVal(e.target.value)
                      setRuleErr('')
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') addRule()
                    }}
                  />
                  <Button size="sm" variant="secondary" onClick={addRule}>
                    Add
                  </Button>
                </div>
                {ruleErr && <div className="pm-err">{ruleErr}</div>}
                {rules.length > 0 && (
                  <div className="pm-subaction">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() =>
                        setConfirm({
                          title: 'Apply rules to existing transactions?',
                          body: `Scans all ${totalTxns} transactions and re-points any that match ${sel.name}'s renaming rules to this payee. Existing assignments on matches will change.`,
                          confirmLabel: 'Apply rules',
                          onConfirm: () => {
                            onApplyPayeeRules?.(sel.id)
                            setApplyRulesMsg(
                              'Re-pointed existing transactions matching ' +
                                sel.name +
                                "'s rules to this payee.",
                            )
                          },
                        })
                      }
                    >
                      Apply rules to existing transactions
                    </Button>
                    {applyRulesMsg && <span className="pm-ok">{applyRulesMsg}</span>}
                  </div>
                )}
              </div>

              <div className="pm-danger">
                <div className="pm-danger-head">Danger zone</div>
                <div className="pm-danger-body">
                  <div>
                    <div className="pm-danger-lbl">Merge into another payee</div>
                    <p className="pm-sec-hint" style={{ margin: 0 }}>
                      Folds all aliases and renaming rules from this payee into the target and
                      re-points all transactions. This payee is permanently deleted.
                    </p>
                    <div className="pm-danger-row">
                      <PMSelect
                        searchable
                        options={[
                          { value: '', label: 'Pick target payee…' },
                          ...others.map((o) => ({
                            value: o.id,
                            label: o.name + ' (' + o.txnCount + ' txns)',
                          })),
                        ]}
                        value={mergeTo}
                        onChange={(v) => setMergeTo(v)}
                      />
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={!mergeTo}
                        onClick={() => {
                          const tgt = others.find((o) => o.id === mergeTo)
                          if (!tgt) return
                          setConfirm({
                            title: 'Merge payee?',
                            body: `${sel.name}'s aliases, renaming rules, and ${sel.txnCount} transaction${sel.txnCount !== 1 ? 's' : ''} will move to ${tgt.name}. ${sel.name} will be permanently deleted — this can't be undone.`,
                            confirmLabel: 'Merge',
                            danger: true,
                            onConfirm: () => {
                              onMerge(sel.id, mergeTo)
                              setSelId(mergeTo)
                              setMergeTo('')
                            },
                          })
                        }}
                      >
                        Merge
                      </Button>
                    </div>
                  </div>
                  <div>
                    <div className="pm-danger-lbl">Delete payee</div>
                    <p className="pm-sec-hint" style={{ margin: 0 }}>
                      {!usageKnown
                        ? usageStatus === 'error'
                          ? "Couldn't count this payee's transactions — close and reopen to try again."
                          : 'Counting transactions…'
                        : sel.txnCount > 0
                          ? `Has ${sel.txnCount} transaction${sel.txnCount !== 1 ? 's' : ''} — pick a payee to reassign them to, then delete.`
                          : 'No transactions; this payee will be permanently removed.'}
                    </p>
                    {!usageKnown ? (
                      <div className="pm-danger-row">
                        <Button size="sm" variant="danger" disabled>
                          Delete payee
                        </Button>
                      </div>
                    ) : sel.txnCount > 0 ? (
                      <div className="pm-danger-row">
                        <PMSelect
                          searchable
                          options={[
                            { value: '', label: 'Reassign transactions to…' },
                            ...others.map((o) => ({ value: o.id, label: o.name })),
                          ]}
                          value={reassignTo}
                          onChange={(v) => setReassignTo(v)}
                        />
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={!reassignTo}
                          onClick={requestDelete}
                        >
                          Delete payee
                        </Button>
                      </div>
                    ) : (
                      <div className="pm-danger-row">
                        <Button size="sm" variant="danger" onClick={requestDelete}>
                          Delete payee
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      <PMConfirmDialog data={confirm} onClose={() => setConfirm(null)} />
    </Modal>
  )
}
