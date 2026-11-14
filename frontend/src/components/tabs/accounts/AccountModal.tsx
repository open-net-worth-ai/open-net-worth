// AccountModal.tsx — compact account creation + essentials-first account editing
//
// The opening balance is a real "Starting Balance" ledger row. Quick create records it today;
// later corrections happen by editing that transaction in the register.
import { useState, useEffect, useRef } from 'react'
import { useRouter } from '@tanstack/react-router'
import { LinkBankAccountDialog } from './LinkBankAccountDialog'
import { AccIcon, InstitutionPicker, CurrencyPicker } from './ds'
import type { InstitutionOption } from './ds'
import { InstitutionManager } from './InstitutionManager'
import type { InstitutionDeleteResult } from './InstitutionManager'
import {
  Modal,
  Button,
  FormField,
  TextField,
  DateField,
  NumberField,
  Stack,
  Text,
  RadioGroup,
} from '../../ui'
import { fCur } from './format'
import { authFetch } from '../../../api/client'
import { fDate, getBaseCurrency } from '../../../utils/format'
import { useFlag } from '../../../hooks/useFlag'
import { isWaiting } from './valuation'
import type {
  Account,
  AccountCreate,
  AccountUpdate,
  AccountPrefill,
  ExternalId,
  ValueSource,
} from './types'
import {
  ACCOUNT_TYPES,
  TYPE_LABELS,
  TYPE_MEANING,
  TYPE_ICON,
  TYPE_COLOR,
  LIABILITY_TYPES,
} from './categories'
import type { AccountType } from './categories'
import { ChevronRight, Link2 } from '../../../ui/icons'

export interface AccountModalProps {
  open: boolean
  mode: 'add' | 'edit'
  account?: Account | null
  institutions: InstitutionOption[] | string[]
  onRenameInstitution?: (id: string, name: string) => Promise<InstitutionOption>
  onDeleteInstitution?: (id: string) => Promise<InstitutionDeleteResult>
  onClose: (action?: string) => void
  onSave: (form: AccountCreate | AccountUpdate, id?: string) => Promise<Account> | void
  // Jump to this account's Starting Balance transaction in the register. Absent = the account
  // has no opening at all (no tracking start), so the line renders without a link.
  onEditOpening?: () => void
  /** Connector-specific seed for quick create. */
  prefill?: AccountPrefill
}

// Liabilities are stored raw-NEGATIVE (db/account_store.py: "signed; liabilities raw-negative")
// and the AI surface already passes them that way. The form speaks "amount you owe" — a plain
// positive number — so the sign is applied here, at the one boundary between the two.
function signedOpening(raw: string, isAsset: boolean): number {
  const v = parseFloat(raw) || 0
  return isAsset ? v : -Math.abs(v)
}
function displayOpening(value: number, isAsset: boolean): string {
  return String(isAsset ? value : Math.abs(value))
}

// Seven-type vocab (ADR 2026-09-07-seven-account-types-one-axis) — one row per type, in
// TYPE_ORDER; the label + meaning are the shared, final copy from categories.ts.
const ICON = TYPE_ICON
const TYPE_ROWS = ACCOUNT_TYPES.map((t) => ({
  t,
  ic: ICON[t],
  lbl: TYPE_LABELS[t],
  meaning: TYPE_MEANING[t],
}))
// Search-only synonyms — the row's own label/meaning already carries the primary vocabulary.
const TYPE_ALIASES: Partial<Record<AccountType, string>> = {
  fixed_deposit: 'fd time deposit',
  retirement: 'cpf srs epf 401k pension',
  credit: 'card bnpl line of credit',
  loan: 'mortgage car student personal margin',
  investment: 'brokerage exchange crypto ibkr binance',
  cash: 'bank checking savings ewallet wallet',
}
interface AccountForm {
  name: string
  type: AccountType
  institution: string
  currency: string
  initial: string
  asOf: string
  lastFour: string
  note: string
  closeDay: string
  dueDay: string
  creditLimit: string
  apr: string
  principal: string
  payoffDate: string
  minPayment: string
  apy: string
}
function blankForm(): AccountForm {
  return {
    name: '',
    type: 'cash',
    institution: '',
    currency: getBaseCurrency(),
    initial: '0',
    asOf: '',
    lastFour: '',
    note: '',
    closeDay: '',
    dueDay: '',
    creditLimit: '',
    apr: '',
    principal: '',
    payoffDate: '',
    minPayment: '',
    apy: '',
  }
}

export default function AccountModal({
  open,
  mode,
  account,
  institutions,
  onRenameInstitution,
  onDeleteInstitution,
  onClose,
  onSave,
  onEditOpening,
  prefill,
}: AccountModalProps) {
  const [step, setStep] = useState<'quick' | 'type' | 'details'>(
    mode === 'edit' ? 'details' : 'quick',
  )
  const [form, setForm] = useState<AccountForm>(blankForm)
  const [typeQ, setTypeQ] = useState('')
  // Name validates on BLUR, not on first keystroke — an empty field the user
  // hasn't reached yet is not an error, it's just empty.
  const [nameTouched, setNameTouched] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState('')
  const [manageInstitutions, setManageInstitutions] = useState(false)
  // The "Linked to" list — seeded from `account.externalIds`, kept as its own state so an
  // Unlink click can update it in place without waiting on the parent's full save/close cycle
  // (`onSave` here would close the whole modal and toast "Saved …", which Unlink must not).
  const [externalIds, setExternalIds] = useState<ExternalId[]>([])
  const [linkingBank, setLinkingBank] = useState(false)
  // Optional: the modal also renders outside the router (tests).
  const router = useRouter({ warn: false }) as ReturnType<typeof useRouter> | undefined
  const [unlinkingIdx, setUnlinkingIdx] = useState<number | null>(null)
  const [confirmUnlink, setConfirmUnlink] = useState<number | null>(null)
  const [unlinkErr, setUnlinkErr] = useState<string | null>(null)
  const [scrollbarActive, setScrollbarActive] = useState(false)
  const scrollbarIdleTimer = useRef<number | null>(null)
  // How an investment/retirement account is valued (ADR 2026-11-10) — edit mode only; a new
  // account starts as Brokerage total, which the server decides.
  const valuation = mode === 'edit' ? (account?.valuation ?? null) : null
  const valueFlag = useFlag('allow_brokerage_total_valuation') === true
  const [valueSource, setValueSource] = useState<ValueSource>('total')
  const [switchPreview, setSwitchPreview] = useState<SwitchPreview | null>(null)
  const money = (n: number) => fCur(n, form.currency, { cents: true })
  const institutionOptions: InstitutionOption[] = institutions.map((institution) =>
    typeof institution === 'string'
      ? { id: institution.toLowerCase(), name: institution, accountCount: 0 }
      : institution,
  )

  useEffect(
    () => () => {
      if (scrollbarIdleTimer.current !== null) {
        window.clearTimeout(scrollbarIdleTimer.current)
        scrollbarIdleTimer.current = null
      }
    },
    [],
  )

  function revealEditScrollbar() {
    setScrollbarActive(true)
    if (scrollbarIdleTimer.current !== null) window.clearTimeout(scrollbarIdleTimer.current)
    scrollbarIdleTimer.current = window.setTimeout(() => {
      scrollbarIdleTimer.current = null
      setScrollbarActive(false)
    }, 700)
  }

  // Which opening the form was filled for (mode + account). The form fills when the dialog
  // opens, or opens on another account — never because a caller re-rendered: every write
  // refreshes open screens, and a rebuilt `prefill` or re-read `account` wiped a name being
  // typed (2026-11-13).
  const filledFor = useRef<string | null>(null)

  useEffect(() => {
    const opening = open ? `${mode}:${account?.id ?? ''}` : null
    if (opening !== null && opening === filledFor.current) return
    filledFor.current = opening
    if (scrollbarIdleTimer.current !== null) {
      window.clearTimeout(scrollbarIdleTimer.current)
      scrollbarIdleTimer.current = null
    }
    // A newly opened modal is a new editing session; discard transient state from the last one.
    setScrollbarActive(false)
    if (!open) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNameTouched(false)
    setSaving(false)
    setSaveErr('')
    setUnlinkErr(null)
    setConfirmUnlink(null)
    if (mode === 'edit' && account) {
      setStep('details')
      setExternalIds(account.externalIds ?? [])
      if (account.valuation) setValueSource(account.valuation.source)
      setSwitchPreview(null)
      const type = (ACCOUNT_TYPES as readonly string[]).includes(account.type)
        ? (account.type as AccountType)
        : 'cash'
      setForm({
        name: account.name,
        type,
        institution: account.institution ?? '',
        currency: account.currency,
        initial: displayOpening(account.initialBalance, !LIABILITY_TYPES.has(type)),
        asOf: account.initialBalanceDate ?? '',
        lastFour: account.lastFour ?? '',
        note: account.note ?? '',
        closeDay: account.statementCloseDay != null ? String(account.statementCloseDay) : '',
        dueDay: account.paymentDueDay != null ? String(account.paymentDueDay) : '',
        creditLimit: account.creditLimit != null ? String(account.creditLimit) : '',
        apr: account.apr != null ? String(account.apr) : '',
        principal: account.principal != null ? String(account.principal) : '',
        payoffDate: account.payoffDate ?? '',
        minPayment: account.minPayment != null ? String(account.minPayment) : '',
        apy: account.apy != null ? String(account.apy) : '',
      })
    } else {
      setExternalIds([])
      if (prefill) {
        setStep('quick')
        setForm({
          ...blankForm(),
          type: prefill.type ?? 'cash',
          name: prefill.name ?? '',
          currency: prefill.currency ?? getBaseCurrency(),
          lastFour: prefill.lastFour ?? '',
          institution: prefill.institution ?? '',
          initial: prefill.initial ?? '0',
        })
      } else {
        setStep('quick')
        setForm(blankForm())
      }
    }
  }, [open, mode, account, prefill])

  async function unlink(index: number) {
    if (!account) return
    const next = externalIds.filter((_, i) => i !== index)
    setUnlinkingIdx(index)
    setUnlinkErr(null)
    try {
      const res = await authFetch(`/api/accounts/${account.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          external_ids: next.map(({ source, ref, name, mask, linkedAt }) => ({
            source,
            ref,
            name,
            mask,
            linked_at: linkedAt,
          })),
        }),
      })
      if (!res.ok) throw new Error()
      setExternalIds(next)
    } catch {
      setUnlinkErr('Could not unlink — try again.')
    } finally {
      setUnlinkingIdx(null)
    }
  }

  function set<K extends keyof AccountForm>(k: K, v: AccountForm[K]) {
    setForm((f) => ({ ...f, [k]: v }))
  }
  function pickType(t: AccountType) {
    setForm((f) => ({ ...f, type: t }))
    setTypeQ('')
    setStep(mode === 'add' ? 'quick' : 'details')
  }
  async function submit() {
    if (saving) return
    if (!form.name.trim()) {
      setNameTouched(true)
      return
    }
    const parsedOpening = Number(form.initial)
    if (!Number.isFinite(parsedOpening)) {
      setSaveErr('Enter a valid current balance.')
      return
    }
    setSaving(true)
    setSaveErr('')
    if (mode === 'add') {
      const payload: AccountCreate = {
        name: form.name.trim(),
        type: form.type,
        currency: form.currency,
        initial_balance: signedOpening(form.initial, isAsset),
        ...(form.institution ? { institution: form.institution } : {}),
        ...(form.lastFour ? { last_four: form.lastFour } : {}),
      }
      try {
        await onSave(payload)
      } catch (err) {
        setSaveErr(serverReason(err) ?? 'We couldn’t add this account. Try again.')
      } finally {
        setSaving(false)
      }
      return
    }
    const payload: AccountCreate = {
      name: form.name.trim(),
      type: form.type,
      institution: form.institution,
      currency: form.currency,
      initial_balance: signedOpening(form.initial, isAsset),
      initial_balance_date: form.asOf || null,
      credit_limit: form.creditLimit ? parseFloat(form.creditLimit) || null : null,
      statement_close_day: form.closeDay ? parseInt(form.closeDay) || null : null,
      payment_due_day: form.dueDay ? parseInt(form.dueDay) || null : null,
      apr: form.apr ? parseFloat(form.apr) || null : null,
      principal: form.principal ? parseFloat(form.principal) || null : null,
      payoff_date: form.payoffDate || null,
      min_payment: form.minPayment ? parseFloat(form.minPayment) || null : null,
      apy: form.apy ? parseFloat(form.apy) || null : null,
      last_four: form.lastFour || null,
      note: form.note,
    }
    if (mode === 'edit' && account?.id) {
      // The opening is the Starting Balance TRANSACTION's business now — edit mode shows it
      // read-only, so it must not ride along on an unrelated rename/colour save and quietly
      // re-book the row (or drag the tracking-start cutoff with it).
      const {
        type: _type,
        currency: _currency,
        initial_balance: _initialBalance,
        initial_balance_date: _initialBalanceDate,
        ...rest
      }: AccountCreate = payload
      const update: AccountUpdate = { ...(rest as AccountUpdate) }
      if (valuation && valueSource !== valuation.source) update.value_source = valueSource
      try {
        await onSave(update, account.id)
      } catch (err) {
        setSaveErr(serverReason(err) ?? 'We couldn’t save these details. Try again.')
      } finally {
        setSaving(false)
      }
    }
  }

  const isCredit = form.type === 'credit'
  const isLoan = form.type === 'loan'
  const isYield = form.type === 'cash' || form.type === 'fixed_deposit'
  const isAsset = !LIABILITY_TYPES.has(form.type)
  // An investment account's typed balance is its total, holdings included (ADR 2026-11-10).
  const isInvestment = valueFlag && (form.type === 'investment' || form.type === 'retirement')
  const apyProjection = (() => {
    const bal = parseFloat(form.initial.replace(/[^0-9.-]/g, '')),
      rate = parseFloat(form.apy)
    if (!isYield || !bal || !rate) return ''
    const yearly = (bal * rate) / 100,
      fmt = (n: number) => form.currency + ' ' + Math.round(n).toLocaleString('en-US')
    return '≈ ' + fmt(yearly / 12) + '/mo • ' + fmt(yearly) + '/yr at this balance'
  })()
  const nameInvalid = nameTouched && !form.name.trim()
  const titleEl =
    mode === 'edit' ? (
      'Edit account'
    ) : step === 'type' ? (
      <button
        type="button"
        className="acct-modal-back"
        onClick={() => setStep(mode === 'add' ? 'quick' : 'details')}
      >
        <span className="acct-modal-back-ic">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </span>
        What kind of account?
      </button>
    ) : (
      'Add account'
    )
  const footer =
    step === 'quick' ? (
      <>
        <Button size="sm" variant="ghost" onClick={() => onClose()} disabled={saving}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="primary"
          onClick={() => void submit()}
          disabled={!form.name.trim() || saving}
        >
          {saving ? 'Adding…' : 'Add account'}
        </Button>
      </>
    ) : step === 'details' ? (
      <>
        <span />
        <Button
          size="sm"
          variant="primary"
          onClick={() => void submit()}
          disabled={!form.name.trim() || saving}
        >
          {saving ? 'Saving…' : 'Save changes'}
        </Button>
      </>
    ) : undefined

  return (
    <>
      <Modal
        open={open}
        onClose={() => {
          if (!saving) onClose()
        }}
        size={step === 'quick' ? 'md' : 'xl'}
        title={titleEl}
        footer={footer}
        className={step === 'details' ? 'acct-account-modal--edit' : undefined}
      >
        <div
          className={
            step === 'quick'
              ? undefined
              : `acct-wizard${step === 'details' ? ` acct-wizard--edit${scrollbarActive ? ' is-scrolling' : ''}` : ''}`
          }
          onScroll={step === 'details' ? revealEditScrollbar : undefined}
        >
          {step === 'quick' && (
            <div className="acct-quick">
              <FormField
                label="Name"
                htmlFor="f-name"
                required
                error={nameInvalid ? 'Name is required' : undefined}
                reserveMessageSpace
              >
                <TextField
                  id="f-name"
                  autoFocus
                  value={form.name}
                  placeholder="e.g. Everyday account"
                  onChange={(e) => set('name', e.target.value)}
                  onBlur={() => setNameTouched(true)}
                />
              </FormField>
              <FormField label="Account type" required reserveMessageSpace>
                <button
                  type="button"
                  className="acct-type-chosen"
                  onClick={() => {
                    setTypeQ('')
                    setStep('type')
                  }}
                  aria-label={`Account type: ${TYPE_LABELS[form.type]}. Change`}
                >
                  <span className="ti">
                    <AccIcon name={ICON[form.type]} size={18} />
                  </span>
                  <span className="acct-type-chosen-l">
                    <span className="acct-type-chosen-lbl">{TYPE_LABELS[form.type]}</span>
                    <span className="acct-type-chosen-grp">{TYPE_MEANING[form.type]}</span>
                  </span>
                  <span className="acct-type-chosen-change">
                    <ChevronRight size={16} aria-hidden="true" />
                  </span>
                </button>
              </FormField>
              <FormField label="Currency" htmlFor="f-ccy" required reserveMessageSpace>
                <CurrencyPicker value={form.currency} onChange={(c) => set('currency', c)} />
              </FormField>
              <FormField
                label={
                  isInvestment
                    ? 'Total value'
                    : isAsset
                      ? 'Current balance'
                      : 'Amount currently owed'
                }
                htmlFor="f-balance"
                required
                help={
                  isInvestment
                    ? 'Holdings and cash together, as your brokerage shows it. You can add holdings later.'
                    : 'We’ll record this as today’s Starting Balance transaction. You can edit it later.'
                }
              >
                <div className="acct-balance-inline">
                  <NumberField
                    id="f-balance"
                    aria-describedby="f-balance-help"
                    value={form.initial}
                    step="any"
                    placeholder="0"
                    onChange={(e) => set('initial', e.target.value)}
                  />
                </div>
              </FormField>
              {saveErr && (
                <div role="alert">
                  <Text size="xs" tone="bad">
                    {saveErr}
                  </Text>
                </div>
              )}
            </div>
          )}
          {/* Account type REPLACES the form, with a back arrow in the title — same dialog, same
          frame (see `.acct-wizard`), so choosing a type never resizes the window. */}
          {step === 'type' && (
            <div className="acct-form">
              <div className="full">
                <div className="acct-typesearch">
                  <svg
                    className="acct-typesearch-ic"
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  >
                    <circle cx="11" cy="11" r="7" />
                    <path d="M21 21l-4-4" />
                  </svg>
                  <input
                    className="acct-typesearch-in"
                    autoFocus
                    placeholder="Search — e.g. 401k, CPF, mortgage, fixed deposit…"
                    value={typeQ}
                    onChange={(e) => setTypeQ(e.target.value)}
                  />
                </div>
                <div className="acct-typepick">
                  {(() => {
                    const q = typeQ.trim().toLowerCase()
                    const rows = TYPE_ROWS.filter(
                      ({ t, lbl, meaning }) =>
                        !q ||
                        lbl.toLowerCase().includes(q) ||
                        meaning.toLowerCase().includes(q) ||
                        (TYPE_ALIASES[t] ?? '').includes(q),
                    )
                    if (!rows.length)
                      return (
                        <div className="acct-typesearch-empty">
                          No account type matches "{typeQ}". Try a simpler word.
                        </div>
                      )
                    return rows.map(({ t, ic, lbl, meaning }) => (
                      <button
                        key={t}
                        type="button"
                        className={`acct-type${form.type === t ? ' on' : ''}`}
                        onClick={() => pickType(t)}
                      >
                        <span className="ti">
                          <AccIcon name={ic} size={18} />
                        </span>
                        <span className="acct-type-txt">
                          <span className="acct-type-lbl">{lbl}</span>
                          <span className="acct-type-meaning">{meaning}</span>
                        </span>
                      </button>
                    ))
                  })()}
                </div>
              </div>
            </div>
          )}
          {step === 'details' && (
            <div className="acct-form2">
              <div className="acct-layout">
                <div className="acct-identity-header">
                  <div className="acct-avatar-row">
                    <div
                      className="acct-avatar"
                      style={{
                        color: TYPE_COLOR[form.type],
                        background: `color-mix(in srgb, ${TYPE_COLOR[form.type]} 12%, var(--surface))`,
                        borderColor: `color-mix(in srgb, ${TYPE_COLOR[form.type]} 30%, var(--line))`,
                      }}
                    >
                      <AccIcon name={ICON[form.type]} size={24} />
                    </div>
                    <div
                      className="acct-identity-meta"
                      aria-label={`Account type: ${TYPE_LABELS[form.type]}. Currency: ${form.currency}. Both are set when this account is created.`}
                    >
                      <span>{TYPE_LABELS[form.type]}</span>
                      <span>{form.currency}</span>
                    </div>
                  </div>
                  <div className="acct-identity-fields">
                    <FormField
                      label="Name"
                      htmlFor="f-name"
                      required
                      error={nameInvalid ? 'Name is required' : undefined}
                      reserveMessageSpace
                    >
                      <TextField
                        id="f-name"
                        autoFocus={mode !== 'edit'}
                        value={form.name}
                        placeholder="e.g. Meridian Bank Multiplier"
                        onChange={(e) => set('name', e.target.value)}
                        onBlur={() => setNameTouched(true)}
                      />
                    </FormField>
                    {saveErr && (
                      <div role="alert">
                        <Text size="xs" tone="bad">
                          {saveErr}
                        </Text>
                      </div>
                    )}
                  </div>
                </div>

                <div className="acct-form-main">
                  <section>
                    <div className="acct-grid">
                      <FormField
                        label="Institution"
                        help="Groups accounts by bank or broker."
                        htmlFor="f-inst"
                      >
                        <InstitutionPicker
                          id="f-inst"
                          value={form.institution}
                          onChange={(v) => set('institution', v)}
                          options={institutionOptions}
                          onManage={
                            onRenameInstitution && onDeleteInstitution
                              ? () => setManageInstitutions(true)
                              : undefined
                          }
                        />
                      </FormField>
                    </div>
                  </section>
                  <section>
                    <FormField
                      label="Note"
                      htmlFor="f-note"
                      help="Anything else about this account for you and AI to know."
                    >
                      <textarea
                        id="f-note"
                        className="ui-textarea acct-note-area"
                        rows={3}
                        value={form.note}
                        placeholder="e.g. Joint account with Alex • used mainly for rent and bills"
                        onChange={(e) => set('note', e.target.value)}
                      />
                    </FormField>
                  </section>
                  <section>
                    <div className="acct-section-label">Account details</div>
                    <FormField
                      label="Last 4 digits"
                      help="Helps AI identify the right account in chat, automations, or transaction imports"
                      htmlFor="f-l4"
                    >
                      <TextField
                        id="f-l4"
                        value={form.lastFour}
                        placeholder="1234"
                        maxLength={4}
                        onChange={(e) => set('lastFour', e.target.value.replace(/\D/g, ''))}
                      />
                    </FormField>
                  </section>
                  {/* The opening lives on the Starting Balance transaction, so it is REPORTED
                  here, not edited here — one number, one place to change it. Create asks for
                  it on its own step instead. */}
                  {isCredit && (
                    <section>
                      <div className="acct-section-label">Credit details</div>
                      <div className="acct-grid acct-grid--2">
                        <FormField label="Interest rate (APR, %)" htmlFor="f-apr">
                          <TextField
                            id="f-apr"
                            inputMode="decimal"
                            value={form.apr}
                            placeholder="3.5"
                            onChange={(e) => set('apr', e.target.value)}
                          />
                        </FormField>
                        <FormField
                          label="Credit limit"
                          htmlFor="f-limit"
                          help="Total available credit"
                        >
                          <TextField
                            id="f-limit"
                            inputMode="decimal"
                            value={form.creditLimit}
                            placeholder="10,000"
                            onChange={(e) => set('creditLimit', e.target.value)}
                          />
                        </FormField>
                        <FormField
                          label="Statement closes (day)"
                          htmlFor="f-close"
                          help="Day of month the cycle closes"
                        >
                          <NumberField
                            id="f-close"
                            value={form.closeDay}
                            min={1}
                            max={31}
                            placeholder="25"
                            onChange={(e) => set('closeDay', e.target.value)}
                          />
                        </FormField>
                        <FormField
                          label="Payment due (day)"
                          htmlFor="f-due"
                          help="Day of month payment is due"
                        >
                          <NumberField
                            id="f-due"
                            value={form.dueDay}
                            min={1}
                            max={31}
                            placeholder="15"
                            onChange={(e) => set('dueDay', e.target.value)}
                          />
                        </FormField>
                      </div>
                    </section>
                  )}

                  {isLoan && (
                    <section>
                      <div className="acct-section-label">Loan details</div>
                      <div className="acct-grid acct-grid--2">
                        <FormField label="Interest rate (APR, %)" htmlFor="f-apr">
                          <TextField
                            id="f-apr"
                            inputMode="decimal"
                            value={form.apr}
                            placeholder="3.5"
                            onChange={(e) => set('apr', e.target.value)}
                          />
                        </FormField>
                        <FormField
                          label="Original principal"
                          htmlFor="f-principal"
                          help="Amount originally borrowed"
                        >
                          <TextField
                            id="f-principal"
                            inputMode="decimal"
                            value={form.principal}
                            placeholder="650,000"
                            onChange={(e) => set('principal', e.target.value)}
                          />
                        </FormField>
                        <FormField
                          label="Payoff date"
                          htmlFor="f-payoff"
                          help="When the balance reaches zero"
                        >
                          <DateField
                            id="f-payoff"
                            className="acct-date-input"
                            value={form.payoffDate}
                            onChange={(e) => set('payoffDate', e.target.value)}
                          />
                        </FormField>
                        <FormField
                          label="Minimum payment"
                          htmlFor="f-minpay"
                          help="Required payment each period"
                        >
                          <TextField
                            id="f-minpay"
                            inputMode="decimal"
                            value={form.minPayment}
                            placeholder="2,400"
                            onChange={(e) => set('minPayment', e.target.value)}
                          />
                        </FormField>
                      </div>
                    </section>
                  )}

                  {isYield && (
                    <section>
                      <div className="acct-grid acct-grid--2">
                        <FormField label="Interest rate (APY, %)" htmlFor="f-apy">
                          <TextField
                            id="f-apy"
                            inputMode="decimal"
                            value={form.apy}
                            placeholder="3.5"
                            onChange={(e) => set('apy', e.target.value)}
                          />
                          {apyProjection && (
                            <div className="acct-resolved good">{apyProjection}</div>
                          )}
                        </FormField>
                      </div>
                    </section>
                  )}

                  {mode === 'edit' && account && (
                    <section>
                      <div className="acct-section-label">Linked to</div>
                      <Stack gap="--space-2">
                        {externalIds.map((entry, i) => (
                          <div key={`${entry.source}:${entry.ref}`} className="acct-link-row">
                            <Link2 size={16} strokeWidth={1.75} aria-hidden="true" />
                            <div className="acct-link-main">
                              <div className="acct-link-name">
                                {entry.name}
                                {entry.mask ? ` ••${entry.mask}` : ''}
                              </div>
                              <div className="acct-link-sub">
                                {confirmUnlink === i
                                  ? `Unlink from ${entry.providerLabel}? ${
                                      valuation?.source === 'total'
                                        ? 'The balance stops updating and keeps its current value.'
                                        : 'Nothing booked so far is removed.'
                                    }`
                                  : `Through ${entry.providerLabel}${
                                      entry.linkedAt
                                        ? ` · linked ${fDate(new Date(entry.linkedAt))}`
                                        : ''
                                    }`}
                              </div>
                            </div>
                            {confirmUnlink === i ? (
                              <>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => setConfirmUnlink(null)}
                                >
                                  Cancel
                                </Button>
                                <Button
                                  size="sm"
                                  variant="danger"
                                  loading={unlinkingIdx === i}
                                  onClick={() => {
                                    setConfirmUnlink(null)
                                    void unlink(i)
                                  }}
                                >
                                  Unlink
                                </Button>
                              </>
                            ) : (
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => setConfirmUnlink(i)}
                              >
                                Unlink
                              </Button>
                            )}
                          </div>
                        ))}
                        {unlinkErr && (
                          <Text size="xs" tone="bad">
                            {unlinkErr}
                          </Text>
                        )}
                        <div>
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => setLinkingBank(true)}
                          >
                            {externalIds.length
                              ? 'Link another bank account'
                              : 'Link a bank account'}
                          </Button>
                        </div>
                      </Stack>
                      {linkingBank && (
                        <LinkBankAccountDialog
                          account={{ ...account, externalIds }}
                          onClose={() => setLinkingBank(false)}
                          onLinked={(link) => setExternalIds((ids) => [...ids, link])}
                          onConnectBank={() => {
                            setLinkingBank(false)
                            if (router) void router.navigate({ to: '/connectors' as never })
                            else window.location.assign('/connectors')
                          }}
                        />
                      )}
                    </section>
                  )}
                  {valuation && account && (
                    <ValueSourceSection
                      account={account}
                      valuation={valuation}
                      value={valueSource}
                      preview={switchPreview}
                      money={money}
                      onChange={(next) => {
                        setValueSource(next)
                        setSwitchPreview(null)
                        if (next !== valuation.source)
                          void fetchSwitchPreview(account.id, next).then(setSwitchPreview)
                      }}
                    />
                  )}
                  {mode === 'edit' && (
                    <div className="acct-opening-compact">
                      <span className="acct-opening-label">Starting balance</span>
                      <span className="acct-opening-amt">
                        {form.currency}{' '}
                        {Math.abs(account?.initialBalance ?? 0).toLocaleString('en-US', {
                          maximumFractionDigits: 2,
                        })}
                        {!isAsset && (account?.initialBalance ?? 0) !== 0 ? ' owed' : ''}
                      </span>
                      <span className="acct-opening-meta">
                        {form.asOf
                          ? `as of ${fDate(form.asOf, 'short', 'always')}`
                          : 'tracked from the first transaction'}
                      </span>
                      {onEditOpening && (
                        <button type="button" className="acct-opening-link" onClick={onEditOpening}>
                          Open
                        </button>
                      )}
                      <span className="acct-opening-explanation">
                        Starting balance is what this account held when you began tracking it.
                        Transactions before this date are excluded, so your ledger starts from this
                        balance.
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </Modal>
      {onRenameInstitution && onDeleteInstitution && (
        <InstitutionManager
          open={manageInstitutions}
          institutions={institutionOptions}
          onClose={() => setManageInstitutions(false)}
          onRename={onRenameInstitution}
          onDelete={onDeleteInstitution}
          onRenamed={(previous, renamed) => {
            if (form.institution.trim().toLowerCase() === previous.name.toLowerCase()) {
              set('institution', renamed.name)
            }
          }}
          onDeleted={(result) => {
            const deleted = institutionOptions.find((institution) => institution.id === result.id)
            if (deleted && form.institution.trim().toLowerCase() === deleted.name.toLowerCase()) {
              set('institution', '')
            }
          }}
        />
      )}
    </>
  )
}

interface SwitchPreview {
  holdings: number
  untracked: number
  total: number
  adjustment: number
  value_after: number
}

/** What switching the value source would do — the server's figures, nothing written. */
async function fetchSwitchPreview(id: string, to: ValueSource): Promise<SwitchPreview | null> {
  try {
    const res = await authFetch(`/api/accounts/${id}/value-switch?to=${to}`)
    return res.ok ? ((await res.json()) as SwitchPreview) : null
  } catch {
    return null
  }
}

interface ValueSourceSectionProps {
  account: Account
  valuation: NonNullable<Account['valuation']>
  value: ValueSource
  preview: SwitchPreview | null
  money: (n: number) => string
  onChange: (next: ValueSource) => void
}

/** "Value this account by": Brokerage total or Live holdings (ADR 2026-11-10). */
function ValueSourceSection({
  account,
  valuation,
  value,
  preview,
  money,
  onChange,
}: ValueSourceSectionProps) {
  const syncing = !!valuation.sync && valuation.linkStatus === 'connected'
  const waiting = isWaiting(valuation)
  const siblings = valuation.siblings
  return (
    <section>
      <div className="acct-section-label">Value this account by</div>
      <Stack gap="--space-2">
        <RadioGroup
          name={`acct-src-${account.id}`}
          ariaLabel="Value this account by"
          value={value}
          onChange={(next) => onChange(next as ValueSource)}
          disabled={waiting}
          options={[
            {
              value: 'total',
              label: 'Brokerage total',
              description: syncing
                ? `The total your brokerage shows, synced via ${valuation.provider}. Holdings you add are part of it.`
                : 'The total on your brokerage statement. Holdings you add are part of it.',
            },
            {
              value: 'holdings',
              label: 'Live holdings',
              description:
                'Each holding at live market prices, plus cash. Needs every holding added.',
            },
          ]}
        />
        {valuation.sync && siblings.length > 0 && value === 'total' && (
          <Text size="xs" tone="bad">
            Counted twice: this total already includes{' '}
            {siblings.length === 1 ? siblings[0] : `${siblings.length} of your other accounts`}.
          </Text>
        )}
        {waiting && (
          <Text size="xs" tone="faint">
            You can choose once the first total arrives.
          </Text>
        )}
        {value !== valuation.source && preview && (
          <Text size="xs" tone={preview.adjustment !== 0 ? 'bad' : 'faint'}>
            {value === 'total'
              ? `The value stays ${money(preview.total)}. Your holdings become a breakdown of that total${
                  syncing ? `, and ${valuation.provider}’s next total replaces it` : ''
                }.`
              : preview.adjustment === 0
                ? preview.untracked !== 0
                  ? `The ${money(preview.untracked)} that’s untracked will count as cash in this account.`
                  : `The value stays ${money(preview.total)}.`
                : `This account will be worth ${money(preview.value_after)}, because that’s all the holdings you’ve added. Add your holdings first, or keep Brokerage total.`}
          </Text>
        )}
      </Stack>
    </section>
  )
}

/** The server's own reason for refusing a save — callers throw the response body, whose
 *  `detail` says what was wrong (an exchange rate it can't get, a name already taken). A
 *  generic "check the details" hid it while the details were fine. */
function serverReason(err: unknown): string | null {
  const text = err instanceof Error ? err.message : ''
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) return detail.trim()
    const message = (detail as { message?: unknown } | null)?.message
    if (typeof message === 'string' && message.trim()) return message.trim()
  } catch {
    // not a JSON body
  }
  return null
}
