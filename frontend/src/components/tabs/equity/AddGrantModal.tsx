import { useEffect, useState } from 'react'
import type {
  GrantCreate,
  GrantType,
  TaxEvent,
  TrancheInput,
  TrancheKind,
} from '../../../types/equity'
import {
  Modal,
  FormField,
  NumberField,
  TextField,
  Select,
  Stack,
  Cluster,
  Button,
  Text,
  ErrorState,
  SegmentedControl,
} from '../../ui'
import { authFetch } from '../../../api/client'
import { fDate } from '../../../utils/format'
import { eqLuciusParse, eqGenSchedule, countParsedFields } from './scheduleGen'
import type { Frequency, GeneratedTranche } from './scheduleGen'
import { ACCOUNTS_KEY, fetchJson } from '../../../hooks/serverState'
import { fetchCachedQuery } from '../../../state/queryCache'
import { X, Plus, ChevronDown, ChevronRight } from '../../../ui/icons'

interface Props {
  onClose: () => void
  onDone: () => void
}

interface AccountLite {
  id: string
  name: string
  kind: string
}

interface InstrumentLite {
  id: string
  symbol: string
  quote_currency?: string
}

interface TrancheRow extends TrancheInput {
  _key: number
}

function toLocalISODate(): string {
  const dt = new Date()
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`
}

let _key = 0
function nextKey() {
  return ++_key
}

function emptyTranche(): TrancheRow {
  return { _key: nextKey(), vest_date: '', units: 0, kind: 'time' }
}

// ESOW is functionally an RSU-equivalent on the backend schema (both are kind="rsu").
// We surface "ESOW" as a display-only label and send "rsu" to the API.
function grantTypeApiValue(uiValue: string): GrantType {
  if (uiValue === 'ltip') return 'ltip'
  return 'rsu' // 'rsu' and 'esow' both map to 'rsu'
}

const NEW_ACCOUNT_SENTINEL = '__new__'

type Mode = 'schedule' | 'manual'

export function AddGrantModal({ onClose, onDone }: Props) {
  const [symbol, setSymbol] = useState('')
  const [instrumentId, setInstrumentId] = useState<string | null>(null)
  const [symbolError, setSymbolError] = useState<string | null>(null)
  const [grantDate, setGrantDate] = useState(toLocalISODate())
  const [grantPricePerUnit, setGrantPricePerUnit] = useState<number | null>(null)
  const [grantTypeUi, setGrantTypeUi] = useState('rsu')
  const [taxEvent, setTaxEvent] = useState<TaxEvent>('at_vest')
  // Opt-in: most plans deliver shares the moment they vest, so this defaults off.
  const [releaseRequired, setReleaseRequired] = useState(false)
  // '' = no selection (required), NEW_ACCOUNT_SENTINEL = user wants to type a new name
  const [accountId, setAccountId] = useState('')
  const [newAccountName, setNewAccountName] = useState('')
  const [tranches, setTranches] = useState<TrancheRow[]>([emptyTranche()])

  // Grant text paste-box state
  const [pasteText, setPasteText] = useState('')
  const [parsedNote, setParsedNote] = useState<string | null>(null)

  // Mode: schedule generator vs manual entry
  const [mode, setMode] = useState<Mode>('schedule')

  // Schedule generator inputs
  const [schedTotalUnits, setSchedTotalUnits] = useState('')
  const [schedFreq, setSchedFreq] = useState<Frequency>('quarterly')
  const [schedYears, setSchedYears] = useState('')
  const [schedFirstVest, setSchedFirstVest] = useState('')
  const [schedPreviewOpen, setSchedPreviewOpen] = useState(false)

  // Advanced disclosure
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const [accounts, setAccounts] = useState<AccountLite[]>([])
  const [instruments, setInstruments] = useState<InstrumentLite[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetch accounts and instruments for selectors.
  useEffect(() => {
    let alive = true
    fetchCachedQuery<AccountLite[]>(ACCOUNTS_KEY, () => fetchJson<AccountLite[]>(ACCOUNTS_KEY))
      .then((data) => {
        if (!alive) return
        setAccounts(data.filter((a) => a.kind === 'asset'))
      })
      .catch(() => {})
    authFetch('/api/instruments')
      .then((r) => (r.ok ? r.json() : Promise.resolve([])))
      .then((data: InstrumentLite[]) => {
        if (!alive) return
        setInstruments(Array.isArray(data) ? data : [])
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  function resolveSymbol(raw: string) {
    const upper = raw.trim().toUpperCase()
    if (!upper) {
      setInstrumentId(null)
      setSymbolError(null)
      return
    }
    const match = instruments.find((i) => i.symbol.toUpperCase() === upper)
    if (match) {
      setInstrumentId(match.id)
      setSymbolError(null)
    } else {
      setInstrumentId(null)
      setSymbolError('Unknown instrument — add it under Investments first.')
    }
  }

  function handleFillFromText() {
    const parsed = eqLuciusParse(pasteText)
    const count = countParsedFields(parsed)
    if (count === 0) {
      setParsedNote("Couldn't parse any fields — review and fill manually.")
      return
    }
    if (parsed.symbol) {
      setSymbol(parsed.symbol)
      resolveSymbol(parsed.symbol)
    }
    if (parsed.totalUnits) setSchedTotalUnits(parsed.totalUnits)
    if (parsed.freq) setSchedFreq(parsed.freq)
    if (parsed.years) setSchedYears(parsed.years)
    if (parsed.firstVest) setSchedFirstVest(parsed.firstVest)
    setParsedNote(`Filled in ${count} field${count === 1 ? '' : 's'} — review below.`)
  }

  function updateTranche(key: number, patch: Partial<TrancheRow>) {
    setTranches((prev) => prev.map((t) => (t._key === key ? { ...t, ...patch } : t)))
  }

  function addTranche() {
    setTranches((prev) => [...prev, emptyTranche()])
  }

  function removeTranche(key: number) {
    setTranches((prev) => prev.filter((t) => t._key !== key))
  }

  // Generated schedule (live-computed from generator inputs)
  const generatedTranches: GeneratedTranche[] = (() => {
    const total = parseFloat(schedTotalUnits)
    const years = parseFloat(schedYears)
    if (!total || !years || !schedFirstVest) return []
    return eqGenSchedule(total, schedFreq, years, schedFirstVest)
  })()

  const schedSummary =
    generatedTranches.length > 0
      ? `${generatedTranches.length} tranches of ${generatedTranches[0]!.units}–${generatedTranches[generatedTranches.length - 1]!.units} units • ${fDate(generatedTranches[0]!.vest_date, 'short', 'always')} → ${fDate(generatedTranches[generatedTranches.length - 1]!.vest_date, 'short', 'always')}`
      : null

  function handleEditManually() {
    if (generatedTranches.length > 0) {
      setTranches(
        generatedTranches.map((t) => ({ ...t, _key: nextKey(), kind: 'time' as TrancheKind })),
      )
    }
    setMode('manual')
  }

  // Which tranche list to use for submission
  const effectiveTranches: TrancheInput[] =
    mode === 'schedule'
      ? generatedTranches.map((t) => ({
          vest_date: t.vest_date,
          units: t.units,
          kind: 'time' as TrancheKind,
        }))
      : tranches.map(({ vest_date, units, kind, condition_note }) => ({
          vest_date,
          units,
          kind,
          condition_note,
        }))

  const totalUnits =
    mode === 'schedule'
      ? generatedTranches.reduce((s, t) => s + t.units, 0)
      : tranches.reduce((sum, t) => sum + (t.units || 0), 0)

  // Account is valid when an existing account is selected OR a new name has been typed.
  const accountChosen =
    (accountId !== '' && accountId !== NEW_ACCOUNT_SENTINEL) ||
    (accountId === NEW_ACCOUNT_SENTINEL && newAccountName.trim().length > 0)

  const scheduleReady =
    mode === 'schedule'
      ? generatedTranches.length > 0
      : tranches.length > 0 &&
        totalUnits > 0 &&
        tranches.every((t) => t.vest_date.length === 10 && t.units > 0)

  const isValid =
    symbol.trim().length > 0 &&
    instrumentId != null &&
    grantDate.length === 10 &&
    scheduleReady &&
    accountChosen

  async function handleSave() {
    if (!isValid || submitting) return
    setSubmitting(true)
    setError(null)

    const accountField: { account_id?: string; account_name?: string } =
      accountId === NEW_ACCOUNT_SENTINEL
        ? { account_name: newAccountName.trim() }
        : { account_id: accountId }

    const body: GrantCreate = {
      instrument_id: instrumentId!,
      total_units: totalUnits,
      grant_date: grantDate,
      type: grantTypeApiValue(grantTypeUi),
      tax_event: taxEvent,
      release_required: releaseRequired,
      tranches: effectiveTranches,
      ...accountField,
      ...(grantPricePerUnit != null ? { grant_price_per_unit: grantPricePerUnit } : {}),
    }

    try {
      const res = await authFetch('/api/equity/grants', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error((detail as { detail?: string }).detail || `HTTP ${res.status}`)
      }
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save grant.')
      setSubmitting(false)
    }
  }

  const footer = (
    <Cluster gap="--space-3" justify="between">
      <Button variant="secondary" onClick={onClose} disabled={submitting}>
        Cancel
      </Button>
      <Button variant="primary" onClick={handleSave} disabled={!isValid || submitting}>
        {submitting ? 'Saving…' : 'Save grant'}
      </Button>
    </Cluster>
  )

  return (
    <Modal open onClose={onClose} title="Add equity grant" size="lg" footer={footer}>
      <Stack gap="--space-5">
        {/* ── Grant text paste-box ── */}
        <div className="equity-add-paste">
          <div className="lx-mark lx-mark--sm equity-add-paste__icon" aria-hidden>
            ✦
          </div>
          <p className="equity-add-paste__heading">Paste grant letter or describe it</p>
          <textarea
            className="equity-add-paste__box"
            placeholder="e.g. 1,000 NVDA RSUs, quarterly over 4 years from 2026-09-15"
            rows={3}
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            aria-label="Grant letter text"
          />
          <div className="equity-add-paste__foot">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleFillFromText}
              disabled={!pasteText.trim()}
            >
              Fill from text
            </Button>
            {parsedNote && (
              <Text
                as="span"
                size="sm"
                tone={parsedNote.startsWith("Couldn't") ? 'faint' : undefined}
              >
                {parsedNote}
              </Text>
            )}
          </div>
        </div>

        <div className="equity-add-or" aria-hidden>
          or fill in manually
        </div>

        <FormField
          label="Instrument / ticker"
          htmlFor="grant-symbol"
          required
          error={symbolError ?? undefined}
        >
          <TextField
            id="grant-symbol"
            placeholder="e.g. ONWAI"
            value={symbol}
            onChange={(e) => {
              setSymbol(e.target.value)
              setInstrumentId(null)
              setSymbolError(null)
            }}
            onBlur={(e) => resolveSymbol(e.target.value)}
          />
        </FormField>

        {/* ── Mode segmented control ── */}
        <div>
          <Text as="p" size="sm" weight={600} style={{ marginBottom: 'var(--space-2)' }}>
            Schedule
          </Text>
          <SegmentedControl
            ariaLabel="Schedule entry mode"
            options={[
              { value: 'schedule', label: 'Schedule generator' },
              { value: 'manual', label: 'Manual tranches' },
            ]}
            value={mode}
            onChange={(v) => setMode(v as Mode)}
          />
        </div>

        {/* ── Schedule generator ── */}
        {mode === 'schedule' && (
          <div className="equity-sched">
            <Cluster gap="--space-3" align="end" wrap>
              <FormField label="Total units" htmlFor="sched-total">
                <NumberField
                  id="sched-total"
                  value={schedTotalUnits}
                  min={1}
                  step={1}
                  onChange={(e) => setSchedTotalUnits(e.target.value)}
                />
              </FormField>
              <FormField label="Frequency" htmlFor="sched-freq">
                <Select
                  id="sched-freq"
                  value={schedFreq}
                  onChange={(e) => setSchedFreq(e.target.value as Frequency)}
                  options={[
                    { value: 'monthly', label: 'Monthly' },
                    { value: 'quarterly', label: 'Quarterly' },
                    { value: 'annual', label: 'Annual' },
                  ]}
                />
              </FormField>
              <FormField label="Over (years)" htmlFor="sched-years">
                <NumberField
                  id="sched-years"
                  value={schedYears}
                  min={1}
                  max={20}
                  step={1}
                  onChange={(e) => setSchedYears(e.target.value)}
                />
              </FormField>
              <FormField label="First vest" htmlFor="sched-first-vest">
                <TextField
                  id="sched-first-vest"
                  type="date"
                  value={schedFirstVest}
                  onChange={(e) => setSchedFirstVest(e.target.value)}
                />
              </FormField>
            </Cluster>

            {schedSummary && (
              <div className="equity-sched__summary">
                <Text as="span" size="sm" tone="faint">
                  →{' '}
                </Text>
                <Text as="span" size="sm">
                  {schedSummary}
                </Text>
                <Button variant="ghost" size="sm" onClick={() => setSchedPreviewOpen((o) => !o)}>
                  {schedPreviewOpen ? 'Hide preview' : 'Preview'}
                </Button>
                <Button variant="ghost" size="sm" onClick={handleEditManually}>
                  Edit manually
                </Button>
              </div>
            )}

            {schedPreviewOpen && generatedTranches.length > 0 && (
              <div className="equity-sched__preview">
                {generatedTranches.map((t, i) => (
                  <div key={i} className="equity-sched__row">
                    <Text as="span" size="sm">
                      {fDate(t.vest_date, 'short', 'always')}
                    </Text>
                    <Text as="span" size="sm" tone="faint">
                      {t.units.toLocaleString()} units
                    </Text>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Manual tranche editor ── */}
        {mode === 'manual' && (
          <div className="equity-tranches">
            <Text as="h3" size="sm" weight={600} className="equity-tranches__heading">
              Tranches
            </Text>
            <Stack gap="--space-3">
              {tranches.map((t) => (
                <Cluster key={t._key} gap="--space-2" align="end">
                  <FormField label="Vest date" htmlFor={`tranche-date-${t._key}`}>
                    <TextField
                      id={`tranche-date-${t._key}`}
                      type="date"
                      value={t.vest_date}
                      onChange={(e) => updateTranche(t._key, { vest_date: e.target.value })}
                    />
                  </FormField>
                  <FormField label="Units" htmlFor={`tranche-units-${t._key}`}>
                    <NumberField
                      id={`tranche-units-${t._key}`}
                      value={t.units || ''}
                      min={1}
                      step={1}
                      onChange={(e) =>
                        updateTranche(t._key, { units: parseFloat(e.target.value) || 0 })
                      }
                    />
                  </FormField>
                  <FormField label="Kind" htmlFor={`tranche-kind-${t._key}`}>
                    <Select
                      id={`tranche-kind-${t._key}`}
                      value={t.kind}
                      onChange={(e) =>
                        updateTranche(t._key, { kind: e.target.value as TrancheKind })
                      }
                      options={[
                        { value: 'time', label: 'Standard' },
                        { value: 'performance', label: 'Performance' },
                      ]}
                    />
                  </FormField>
                  {tranches.length > 1 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => removeTranche(t._key)}
                      aria-label="Remove tranche"
                    >
                      <X size={14} aria-hidden="true" />
                    </Button>
                  )}
                </Cluster>
              ))}
            </Stack>
            <Button
              variant="ghost"
              size="sm"
              iconLeft={<Plus size={14} aria-hidden="true" />}
              onClick={addTranche}
              className="equity-tranches__add"
            >
              Add tranche row
            </Button>
          </div>
        )}

        {/* ── Advanced disclosure ── */}
        <div className="equity-add-advanced">
          <button
            type="button"
            className="equity-add-advanced__toggle"
            onClick={() => setAdvancedOpen((o) => !o)}
            aria-expanded={advancedOpen}
          >
            <span className="equity-add-advanced__chevron" aria-hidden>
              {advancedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </span>
            Advanced
          </button>

          {advancedOpen && (
            <Stack gap="--space-4" className="equity-add-advanced__body">
              <FormField label="Grant date" htmlFor="grant-date" required>
                <TextField
                  id="grant-date"
                  type="date"
                  value={grantDate}
                  onChange={(e) => setGrantDate(e.target.value)}
                />
              </FormField>

              <FormField
                label="Price at grant (per unit)"
                htmlFor="grant-price-per-unit"
                help="Optional — for reference and since-grant performance. Not used for tax."
              >
                <TextField
                  id="grant-price-per-unit"
                  type="number"
                  value={grantPricePerUnit ?? ''}
                  min={0}
                  step="any"
                  onChange={(e) => {
                    const v = parseFloat(e.target.value)
                    setGrantPricePerUnit(isNaN(v) ? null : v)
                  }}
                />
              </FormField>

              <FormField label="Grant type" htmlFor="grant-type" required>
                <Select
                  id="grant-type"
                  value={grantTypeUi}
                  onChange={(e) => setGrantTypeUi(e.target.value)}
                  options={[
                    { value: 'rsu', label: 'RSU' },
                    { value: 'esow', label: 'ESOW' },
                    { value: 'ltip', label: 'LTIP (performance)' },
                  ]}
                />
              </FormField>

              <FormField label="Tax event" htmlFor="grant-tax-event" required>
                <Select
                  id="grant-tax-event"
                  value={taxEvent}
                  onChange={(e) => setTaxEvent(e.target.value as TaxEvent)}
                  options={[
                    { value: 'at_vest', label: 'Tax at vest — Singapore / US / Germany' },
                    { value: 'deferred', label: 'Tax deferred — Vietnam and others' },
                  ]}
                />
              </FormField>

              <FormField
                label="Share delivery"
                htmlFor="grant-release-required"
                help="Choose delayed when your plan hands the shares over some time AFTER the vest date — a broker settlement lag, a blackout window, or country delivery rules. Vested units then count in your net worth but stay unsellable until you mark them released."
              >
                <Select
                  id="grant-release-required"
                  value={releaseRequired ? 'delayed' : 'at_vest'}
                  onChange={(e) => setReleaseRequired(e.target.value === 'delayed')}
                  options={[
                    { value: 'at_vest', label: 'Sellable at vest — the usual case' },
                    { value: 'delayed', label: 'Delivered later — released after vest' },
                  ]}
                />
              </FormField>
            </Stack>
          )}
        </div>

        <FormField
          label="Account"
          htmlFor="grant-account"
          required
          help="Custodian or brokerage account where unvested units and vest transactions are tracked."
        >
          <Select
            id="grant-account"
            value={accountId}
            onChange={(e) => {
              setAccountId(e.target.value)
              if (e.target.value !== NEW_ACCOUNT_SENTINEL) setNewAccountName('')
            }}
            options={[
              { value: '', label: 'Select an account…' },
              ...accounts.map((a) => ({ value: a.id, label: a.name })),
              { value: NEW_ACCOUNT_SENTINEL, label: '+ New account…' },
            ]}
          />
          {accountId === NEW_ACCOUNT_SENTINEL && (
            <TextField
              id="grant-new-account-name"
              placeholder="Account name (e.g. JP Morgan)"
              value={newAccountName}
              onChange={(e) => setNewAccountName(e.target.value)}
              style={{ marginTop: 'var(--space-2)' }}
            />
          )}
        </FormField>

        {error && <ErrorState message={error} />}
      </Stack>
    </Modal>
  )
}
