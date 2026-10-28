// Settings → Profile. Ported from
// design_handoffs/settings_handoff/prototype/redesign/profile-section.jsx.
//
// Four panels: About you · Income & expenses · FIRE settings · Instructions for Lucius. Everything
// here is fed to the model, which is why the panel subtitles say so — a user who doesn't know
// that leaves the fields empty.
//
// This is the one section with an explicit Save: eight identity fields plus free-text notes,
// where a half-entered state is not a state. A sticky action bar appears while dirty.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { authFetch } from '../../../../api/client'
import { COUNTRIES, lookupByCode, lookupByName } from '../../../../utils/countries'
import { PROFILE_KEY, fetchJson } from '../../../../hooks/serverState'
import {
  invalidateQueryCache,
  setCachedQueryData,
  useCachedFetchState,
} from '../../../../state/queryCache'
import { fBase, fBaseCompact, getCurrencySymbol } from '../../../../utils/format'
import {
  EMPTY_FIRE_DRAFT,
  fireHorizon,
  fireNumberOf,
  fireSwrMatches,
  fireSwrNote,
  fireSwrSuggestion,
  saveFireDraft,
  seedFireDraft,
} from '../../plan/fireSettings'
import type { FireDraft } from '../../plan/fireSettings'
import { Btn, Field, Modal, Panel, Select, useSectionBusy } from '../ds'
import type { SectionProps, Toast } from '../ds'
import { entityRefProps } from '../../../../utils/mentionable'
import { buildCtxFields } from '../../../../lucius/ctxFields'
import { filterByDescriptor } from '../../../../lucius/entityRegistry'
import { CheckboxGroup } from '../../../ui/CheckboxGroup'
import { DateField } from '../../../ui/DateField'
import { ChevronDown } from '../../../../ui/icons'

interface ProfileShape {
  name?: string | null
  nickname?: string | null
  date_of_birth?: string | null
  nationalities?: string[] | null
  countries_of_residence?: string[] | null
  tax_residencies?: string[] | null
  employment_types?: string[] | null
  marital_status?: string | null
  risk_tolerance?: string | null
  base_currency?: string
  monthly_net_income?: number | null
  monthly_expenses?: number | null
  cashflow_low_balance_threshold?: number | null
  notes?: string | null
  fire_monthly_retirement_spending?: number | null
  fire_withdrawal_rate_pct?: number | null
  fire_expected_return_pct?: number | null
  fire_volatility_pct?: number | null
  fire_life_expectancy?: number | null
  retirement_target_age?: number | null
  import_review_default?: string | null
  [k: string]: unknown
}

// The nine scalar identity/planning fields + notes, saved as strings.
const SCALAR_FIELDS = [
  'name',
  'nickname',
  'date_of_birth',
  'marital_status',
  'risk_tolerance',
  'monthly_net_income',
  'monthly_expenses',
  'cashflow_low_balance_threshold',
  'notes',
] as const
type ScalarKey = (typeof SCALAR_FIELDS)[number]

// The four list identity fields — a person can hold dual citizenship, split residence across
// a year, owe tax in more than one country, or combine employment types (e.g. salaried + a
// side business).
const LIST_FIELDS = [
  'nationalities',
  'countries_of_residence',
  'tax_residencies',
  'employment_types',
] as const
type ListKey = (typeof LIST_FIELDS)[number]

// Everything the Save button commits. Anything NOT in here (base currency, formats) commits on
// change in its own section.
const EDITABLE = [...SCALAR_FIELDS, ...LIST_FIELDS] as const
type EditableKey = (typeof EDITABLE)[number]
const LIST_FIELD_SET: ReadonlySet<string> = new Set(LIST_FIELDS)
type Draft = { [K in ScalarKey]: string } & { [K in ListKey]: string[] }

type PickerKey = ListKey
const PICKER_FIELD_LABELS: Record<PickerKey, string> = {
  nationalities: 'Nationalities',
  countries_of_residence: 'Countries of residence',
  tax_residencies: 'Tax residencies',
  employment_types: 'Employment types',
}
const PICKER_FIELD_HELP: Partial<Record<PickerKey, string>> = {
  tax_residencies:
    'Where you owe tax — often the same as residence, but not always (e.g. US citizens abroad).',
}
// The three country fields (~200 options) get a search box; employment types (5 options)
// doesn't need one.
const PICKER_HAS_SEARCH: Record<PickerKey, boolean> = {
  nationalities: true,
  countries_of_residence: true,
  tax_residencies: true,
  employment_types: false,
}

const EMPLOYMENT: [string, string][] = [
  ['salaried', 'Salaried employee'],
  ['self_employed', 'Self-employed'],
  ['business_owner', 'Business owner'],
  ['retired', 'Retired'],
  ['student', 'Student'],
]
const MARITAL: [string, string][] = [
  ['single', 'Single'],
  ['married', 'Married'],
  ['divorced', 'Divorced'],
  ['widowed', 'Widowed'],
]
const RISK: [string, string][] = [
  ['conservative', 'Conservative'],
  ['moderate', 'Moderate'],
  ['aggressive', 'Aggressive'],
]

const FIRE_FIELDS: {
  k: keyof FireDraft
  label: string
  step: number
  money?: boolean
  tip: string
}[] = [
  {
    k: 'monthlyRetirementSpending',
    label: 'Monthly retirement spending',
    step: 100,
    money: true,
    tip: 'What you expect to spend per month once retired, in today’s dollars. It is annualized only when calculating your FIRE number.',
  },
  {
    k: 'withdrawalRate',
    label: 'Safe withdrawal rate %',
    step: 0.1,
    tip: 'The % of savings withdrawn each year in retirement. The classic 4% rule assumes ~30 years — retiring early stretches it longer, so a lower rate is safer.',
  },
  {
    k: 'expectedReturn',
    label: 'Expected return %',
    step: 0.5,
    tip: 'Roughly how much your investments grow each year, before inflation.',
  },
  {
    k: 'volatility',
    label: 'Return volatility %',
    step: 1,
    tip: 'How much returns swing year to year. ~14–15% is typical for a diversified global portfolio.',
  },
  {
    k: 'targetAge',
    label: 'Target retirement age',
    step: 1,
    tip: 'The age you want to stop working. Projections check whether you’ll reach your FIRE number by then.',
  },
  {
    k: 'lifeExpectancy',
    label: 'Life expectancy',
    step: 1,
    tip: 'How long your money needs to last. A longer horizon lowers the safe withdrawal rate.',
  },
  {
    k: 'monthlySaving',
    label: 'Monthly saving until retire',
    step: 50,
    money: true,
    tip: 'Net amount you add to investments each month until you retire.',
  },
]

function countryCodesToNames(codes: string[] | null | undefined): string {
  return (codes ?? []).map((c) => lookupByCode(c)?.name ?? c).join(', ')
}

// Older rows can carry the full country NAME instead of its code (pre-dating the code-only
// contract, or written by a path that never ran through the backend's own normalizer). The
// picker's checkboxes are keyed by code, so a name-shaped entry silently matched nothing —
// this is what let a genuinely-selected country render unchecked. Resolve either shape to the
// code so matching (and re-saving) is consistent regardless of how it got stored.
function toCountryCode(raw: string): string {
  return lookupByCode(raw)?.code ?? lookupByName(raw)?.code ?? raw
}

function calcAge(dob: string | null | undefined): number | null {
  if (!dob) return null
  const b = new Date(dob)
  if (Number.isNaN(b.getTime())) return null
  const t = new Date()
  let age = t.getFullYear() - b.getFullYear()
  if (t.getMonth() < b.getMonth() || (t.getMonth() === b.getMonth() && t.getDate() < b.getDate()))
    age--
  return age
}

function Tip({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        onBlur={() => setOpen(false)}
        aria-label="Help"
        style={{
          width: 15,
          height: 15,
          borderRadius: 999,
          border: '1px solid var(--line-strong)',
          background: 'var(--surface2)',
          color: 'var(--faint)',
          fontSize: 9.5,
          fontWeight: 700,
          cursor: 'pointer',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 0,
        }}
      >
        ?
      </button>
      {open && (
        <span
          style={{
            position: 'absolute',
            left: '50%',
            bottom: 22,
            transform: 'translateX(-50%)',
            zIndex: 60,
            width: 250,
            background: 'var(--modal-bg)',
            border: '1px solid var(--modal-border)',
            borderRadius: 10,
            boxShadow: 'var(--shadow-lg)',
            padding: '10px 12px',
            fontSize: 11.5,
            lineHeight: 1.55,
            color: 'var(--muted)',
            fontWeight: 400,
            textTransform: 'none',
            letterSpacing: 0,
          }}
        >
          {text}
        </span>
      )}
    </span>
  )
}

function FireModal({
  profile,
  onClose,
  onSaved,
  toast,
}: {
  profile: ProfileShape
  onClose: () => void
  onSaved: () => void
  toast: Toast
}) {
  const [s, setS] = useState<FireDraft>(() => seedFireDraft(profile) ?? EMPTY_FIRE_DRAFT)
  const [saving, setSaving] = useState(false)
  const set = (k: keyof FireDraft, v: string) =>
    setS((prev) => ({ ...prev, [k]: v === '' ? '' : Number(v) }))

  const horizon = fireHorizon(s)
  const swrSuggest = fireSwrSuggestion(s)
  const swrMatches = fireSwrMatches(s)
  const fireNum = fireNumberOf(s)
  const currency = (profile.base_currency || 'SGD').toUpperCase()

  async function save() {
    setSaving(true)
    await saveFireDraft(s, profile)
    setSaving(false)
    onSaved()
    toast('FIRE settings saved — projections updated')
    onClose()
  }

  return (
    <Modal
      title="FIRE Settings"
      onClose={onClose}
      footer={
        <>
          <Btn onClick={onClose}>Cancel</Btn>
          <Btn variant="primary" disabled={saving} onClick={() => void save()}>
            {saving ? 'Saving…' : 'Save settings'}
          </Btn>
        </>
      }
    >
      <span className="rd-help">
        The same settings the Plan tab edits — they drive both Projections and Retirement.
      </span>
      {fireNum !== null && (
        <div className="rd-callout">
          <span className="rd-callout-k">FIRE number</span>
          <span className="rd-callout-calc">
            {fBase(Number(s.monthlyRetirementSpending))} × 12 ÷ {s.withdrawalRate}%
          </span>
          <span className="rd-callout-eq">=</span>
          <b className="rd-callout-v">{fBaseCompact(fireNum)}</b>
        </div>
      )}
      <div className="rd-formgrid">
        {FIRE_FIELDS.map(({ k, label, step, money, tip }) => (
          <Field
            key={k}
            label={
              <span>
                {money ? `${label} (${currency})` : label} <Tip text={tip} />
              </span>
            }
            help={
              k === 'withdrawalRate' && swrSuggest !== null
                ? swrMatches
                  ? `Matches a ${horizon}-yr retirement`
                  : fireSwrNote(s)
                : undefined
            }
          >
            <input
              className="rd-input"
              type="number"
              step={step}
              value={s[k] === '' ? '' : String(s[k])}
              onChange={(e) => set(k, e.target.value)}
            />
          </Field>
        ))}
      </div>
    </Modal>
  )
}

// A ~200-option checklist inlined three times over made the panel unusable — this puts every
// list field behind a button that shows what's already selected, matching the ask: the row
// itself must surface the current values, and the full list only opens on demand. Employment
// types (5 options) gets the same modal for a consistent editing model, minus the search box.
function MultiSelectModal({
  label,
  help,
  options,
  showSearch,
  normalizeValue,
  value,
  onChange,
  onClose,
  loading,
}: {
  label: string
  help?: string
  options: [string, string][]
  showSearch: boolean
  normalizeValue: (raw: string) => string
  value: string[]
  onChange: (next: string[]) => void
  onClose: () => void
  loading: boolean
}) {
  const [query, setQuery] = useState('')
  // Checkbox options are keyed by code — normalize before anything compares against them, or a
  // name-shaped legacy country entry (see toCountryCode) silently matches no option.
  const normalizedValue = useMemo(() => value.map(normalizeValue), [value, normalizeValue])
  // Pinned to the selection AS OF OPENING, not the live value — sorting by the live value
  // would yank a row to the top the instant you check it, right out from under the cursor.
  const [pinned] = useState(() => new Set(normalizedValue))
  const filtered =
    showSearch && query.trim()
      ? options.filter(([, name]) => name.toLowerCase().includes(query.trim().toLowerCase()))
      : options
  const sorted = useMemo(
    () =>
      [...filtered].sort((a, b) => {
        const aPinned = pinned.has(a[0])
        const bPinned = pinned.has(b[0])
        if (aPinned !== bPinned) return aPinned ? -1 : 1
        return a[1].localeCompare(b[1])
      }),
    [filtered, pinned],
  )

  return (
    <Modal
      title={label}
      onClose={onClose}
      footer={
        <Btn variant="primary" onClick={onClose}>
          Done
        </Btn>
      }
    >
      {help ? <span className="rd-help">{help}</span> : null}
      <div className="rd-countryselect">
        {showSearch && (
          <input
            className="rd-input"
            type="text"
            placeholder="Search countries…"
            value={query}
            disabled={loading}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
        )}
        <CheckboxGroup
          options={sorted.map(([v, n]) => ({ value: v, label: n }))}
          value={normalizedValue}
          onChange={onChange}
          emptyText="No matches"
          maxHeight={360}
        />
      </div>
    </Modal>
  )
}

export function ProfileSection({
  toast,
  onReviewDefaultChange,
  actionBarSlot,
}: SectionProps & { onReviewDefaultChange?: (v: 'skip' | 'merge') => void }) {
  const { data, initialLoading, refresh } = useCachedFetchState<ProfileShape>(
    PROFILE_KEY,
    () => fetchJson<ProfileShape>(PROFILE_KEY),
    { staleTime: 0 },
  )
  // Only the FIRST fetch, and only while nothing is cached. A background revalidation must not
  // blank out fields the user is already looking at.
  const loading = initialLoading && !data
  useSectionBusy(loading)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saved, setSaved] = useState<Draft | null>(null)
  // `saved` is read inside the re-baseline effect below without being a dependency of it —
  // depending on it would re-run that effect on its own setState.
  const savedRef = useRef<Draft | null>(null)
  savedRef.current = saved
  const [fireOpen, setFireOpen] = useState(false)
  const [pickerOpen, setPickerOpen] = useState<PickerKey | null>(null)
  const [saving, setSaving] = useState(false)

  // Re-baseline when the server view changes (our own save, or a Lucius update_profile).
  //
  // Guarded on the VALUES, not on `data`'s identity: every revalidation parses fresh JSON, so
  // the object is new each time even when nothing changed. Re-baselining on identity would
  // both wipe whatever the user was typing during a background refetch and, because setting
  // state re-renders, feed a loop.
  useEffect(() => {
    if (!data) return
    const next = {} as Draft
    for (const k of SCALAR_FIELDS) {
      const v = data[k]
      next[k] = v === null || v === undefined ? '' : String(v)
    }
    for (const k of LIST_FIELDS) {
      const v = data[k]
      next[k] = Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
    }
    const serialized = JSON.stringify(next)
    setSaved((prev) => (prev && JSON.stringify(prev) === serialized ? prev : next))
    // An external change (a Lucius update_profile) refreshes the form — but never on top of
    // an edit in progress, which the user has not been asked about.
    setDraft((prev) => {
      if (prev === null) return next
      if (JSON.stringify(prev) === serialized) return prev
      const clean =
        savedRef.current === null || JSON.stringify(prev) === JSON.stringify(savedRef.current)
      return clean ? next : prev
    })
  }, [data])

  const dirty = useMemo(
    () => Boolean(draft && saved && JSON.stringify(draft) !== JSON.stringify(saved)),
    [draft, saved],
  )

  const set = <K extends EditableKey>(k: K, v: Draft[K]) =>
    setDraft((prev) => (prev ? { ...prev, [k]: v } : prev))
  const currency = (data?.base_currency || 'SGD').toUpperCase()
  const age = calcAge(draft?.date_of_birth)

  // The profile stores the ISO code (shorter for the model, unambiguous) — the picker's
  // VALUE is the code, its LABEL is the name a human actually reads.
  const countries: [string, string][] = useMemo(() => COUNTRIES.map((c) => [c.code, c.name]), [])

  async function save() {
    if (!draft) return
    setSaving(true)
    // Numeric fields go back as numbers; a cleared field goes back as null, which the server
    // reads as "unset" rather than zero.
    const body: Record<string, unknown> = {}
    for (const k of EDITABLE) {
      const raw = draft[k]
      if (LIST_FIELD_SET.has(k)) {
        body[k] = raw
        continue
      }
      const numeric =
        k === 'monthly_net_income' ||
        k === 'monthly_expenses' ||
        k === 'cashflow_low_balance_threshold'
      body[k] = numeric ? (raw === '' ? null : Number(raw as string)) : raw
    }
    try {
      const res = await authFetch(PROFILE_KEY, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        // FastAPI's `detail` is a plain string for a raised HTTPException, but a LIST of
        // {msg, loc, ...} objects for a 422 Pydantic validation error — passing that array
        // straight to toast() renders raw objects as React children and crashes the pane.
        const b = (await res.json().catch(() => null)) as {
          detail?: string | Array<{ msg?: string }>
        } | null
        const detail = b?.detail
        const message = Array.isArray(detail)
          ? detail
              .map((d) => d?.msg)
              .filter(Boolean)
              .join('; ')
          : detail
        toast(message || `Could not save (HTTP ${res.status})`)
        return
      }
      const { profile } = (await res.json()) as { profile: ProfileShape }
      setCachedQueryData(PROFILE_KEY, profile)
      invalidateQueryCache('/api/cashflow')
      invalidateQueryCache('/api/fire')
      setSaved(draft)
      onReviewDefaultChange?.(profile.import_review_default === 'skip' ? 'skip' : 'merge')
      toast('Profile saved')
    } catch {
      toast('Could not reach the server. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const input = (k: ScalarKey, type?: string, extra?: Record<string, unknown>) => (
    <input
      className="rd-input"
      type={type || 'text'}
      value={draft?.[k] ?? ''}
      disabled={loading}
      onChange={(e) => set(k, e.target.value)}
      {...(extra || {})}
    />
  )

  // iOS Safari's native <input type="date"> ignores height/overflow constraints applied to
  // the input ITSELF outright (a real platform limit, confirmed against an actual device) —
  // its own internal chrome paints taller than any box we give it and bleeds onto the next
  // field. An earlier attempt hid the real input entirely (opacity: 0) behind a fake display
  // box, which fixed the height but broke tap-to-open — a same-origin invisible replaced
  // element does not reliably forward a tap into the OS's native picker on every platform,
  // confirmed live: it landed on nothing. The real input stays fully visible and is what's
  // actually tapped, so native behavior (the picker, the cursor, focus) is untouched; the
  // WRAPPER clips it instead, via `overflow: hidden` on an ancestor — a containment the
  // input's own internal rendering cannot escape, unlike a CSS height/overflow declared on
  // the input itself, which iOS is free to ignore.
  const dateInput = (k: ScalarKey) => (
    <DateField
      unstyled
      className="rd-datefield-input"
      wrapperClassName="rd-datefield"
      value={draft?.[k] ?? ''}
      disabled={loading}
      onChange={(e) => set(k, e.target.value)}
    />
  )
  // Money inputs carry their currency INSIDE the field. `type="number"` cannot hold a formatted
  // value (a grouped "9,000" is an invalid number), so the amount stays raw — but the symbol
  // adornment removes the ambiguity at the point of entry, where every other screen shows
  // "S$9,000" and the box showed a bare 9000.
  const moneyInput = (k: ScalarKey, extra?: Record<string, unknown>) => (
    <div className="rd-moneyfield">
      <span className="rd-moneysym" aria-hidden="true">
        {getCurrencySymbol(currency)}
      </span>
      <input
        className="rd-input"
        type="number"
        inputMode="decimal"
        value={draft?.[k] ?? ''}
        disabled={loading}
        onChange={(e) => set(k, e.target.value)}
        {...(extra || {})}
      />
    </div>
  )

  const select = (k: ScalarKey, opts: [string, string][]) => (
    <Select value={draft?.[k] ?? ''} disabled={loading} onChange={(e) => set(k, e.target.value)}>
      <option value="">—</option>
      {opts.map(([v, l]) => (
        <option key={v} value={v}>
          {l}
        </option>
      ))}
    </Select>
  )

  // Every list field shows a button with the CURRENT selection — the row itself must say
  // what's already picked — and opens the full checklist (search included, for the three
  // ~200-option country fields) in a modal rather than inlining all four at once.
  const pickerOptionsFor = (k: PickerKey): [string, string][] =>
    k === 'employment_types' ? EMPLOYMENT : countries
  const pickerField = (k: PickerKey) => {
    const opts = pickerOptionsFor(k)
    const byCode = new Map(opts)
    const summary = (draft?.[k] ?? []).map((c) => byCode.get(c) ?? c).join(', ')
    return (
      <Field key={k} label={PICKER_FIELD_LABELS[k]} help={PICKER_FIELD_HELP[k]}>
        <span className="rd-select-wrap">
          <button
            type="button"
            className="rd-select"
            disabled={loading}
            onClick={() => setPickerOpen(k)}
          >
            {summary || 'None selected'}
          </button>
          <ChevronDown className="rd-select-ic" size={14} strokeWidth={1.8} aria-hidden="true" />
        </span>
      </Field>
    )
  }

  // The FIRE panel is a READ-ONLY mirror: the facts live on the profile and are edited by the
  // same editor the Plan tab opens.
  const fireDraft = data ? seedFireDraft(data) : EMPTY_FIRE_DRAFT
  const fireNumber = fireNumberOf(fireDraft)
  const money = (n: number | '') => (n === '' ? '—' : fBase(Math.round(Number(n))))
  const pct = (n: number | '') => (n === '' ? '—' : n + '%')

  const actionBar = (
    <div className="rd-actionbar">
      <span className="rd-actionbar-note">Unsaved changes</span>
      <Btn onClick={() => setDraft(saved)}>Discard</Btn>
      <Btn variant="primary" disabled={saving} onClick={() => void save()}>
        {saving ? 'Saving…' : 'Save profile'}
      </Btn>
    </div>
  )

  return (
    <div
      className="rd-secwrap"
      aria-busy={loading || undefined}
      {...entityRefProps(
        'profile',
        'profile',
        'Profile',
        'profile settings assumptions',
        data?.base_currency ?? '',
      )}
      data-ctx=""
      data-ctx-type="profile"
      data-ctx-fields={buildCtxFields(
        filterByDescriptor('profile', {
          base_currency: data?.base_currency,
          nationalities: countryCodesToNames(data?.nationalities) || undefined,
          countries_of_residence: countryCodesToNames(data?.countries_of_residence) || undefined,
          tax_residencies: countryCodesToNames(data?.tax_residencies) || undefined,
          employment_types: (data?.employment_types ?? []).join(', ') || undefined,
          retirement_target_age: data?.retirement_target_age,
        }),
      )}
      draggable
    >
      <Panel title="About you" sub="Fed directly to Lucius for personalised, goal-aware advice">
        <div className="rd-formgrid">
          <Field label="Full name">{input('name')}</Field>
          <Field label="What should Lucius call you?">{input('nickname')}</Field>
          <Field label="Date of birth">{dateInput('date_of_birth')}</Field>
          <Field label="Marital status">{select('marital_status', MARITAL)}</Field>
          <Field label="Risk tolerance">{select('risk_tolerance', RISK)}</Field>
          {pickerField('employment_types')}
          {pickerField('nationalities')}
          {pickerField('countries_of_residence')}
          {pickerField('tax_residencies')}
        </div>
      </Panel>

      <Panel
        title="Income & expenses"
        sub="Planning estimates for cash-flow and FIRE projections — actual spending is tracked on the Plan tab"
      >
        <div className="rd-formgrid">
          <Field label={`Monthly net income (${currency})`}>
            {moneyInput('monthly_net_income', { min: 0 })}
          </Field>
          <Field label={`Monthly expenses (${currency})`}>
            {moneyInput('monthly_expenses', { min: 0 })}
          </Field>
          <Field
            label={
              <span>
                Low balance threshold ({currency}){' '}
                <Tip text="Cash-flow alerts fire when any checking account is projected to dip below this." />
              </span>
            }
          >
            {moneyInput('cashflow_low_balance_threshold', { min: 0 })}
          </Field>
        </div>
      </Panel>

      <Panel
        title="FIRE settings"
        sub="Drives your retirement projections"
        actions={
          <Btn size="xs" onClick={() => setFireOpen(true)}>
            Edit
          </Btn>
        }
      >
        <div className="rd-readout">
          {(
            [
              ['Monthly retirement spending', money(fireDraft.monthlyRetirementSpending) + ' / mo'],
              ['FIRE number', fireNumber !== null ? fBaseCompact(fireNumber) : '—'],
              ['Withdrawal rate', pct(fireDraft.withdrawalRate)],
              ['Expected return', pct(fireDraft.expectedReturn)],
              ['Return volatility', pct(fireDraft.volatility)],
              ['Monthly saving', money(fireDraft.monthlySaving)],
              [
                'Target retirement age',
                fireDraft.targetAge === '' ? '—' : String(fireDraft.targetAge),
                age !== null && fireDraft.targetAge !== ''
                  ? 'in ' + (Number(fireDraft.targetAge) - age) + ' yrs'
                  : null,
              ],
              [
                'Life expectancy',
                fireDraft.lifeExpectancy === '' ? '—' : String(fireDraft.lifeExpectancy),
              ],
              // Exactly the eight the design specifies. A derived "Progress" row was tempting
              // here and is deliberately NOT added: this panel is a read-only MIRROR of the
              // FIRE facts, and progress-against-target is the Plan tab's job.
            ] as [string, string, string?][]
          ).map(([k, v, note]) => (
            <div className="rd-readout-item" key={k}>
              <span className="rd-readout-k">{k}</span>
              <span className="rd-readout-v rd-mono">
                {v}
                {note ? <em>{note}</em> : null}
              </span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title="Instructions for Lucius"
        sub="Standing rules for how Lucius should work with you. Lucius can add to them too — you’ll see each change before it’s saved."
      >
        <textarea
          className="rd-textarea"
          rows={4}
          placeholder="e.g. Keep answers short. Always show amounts in SGD."
          value={draft?.notes ?? ''}
          disabled={loading}
          onChange={(e) => set('notes', e.target.value)}
        />
      </Panel>

      {dirty &&
        (actionBarSlot
          ? createPortal(actionBar, actionBarSlot)
          : // The shell hasn't mounted the slot yet (first paint) — render inline this once
            // rather than lose the bar entirely; it re-parents into the slot the moment it's
            // ready, which is one render later in practice.
            actionBar)}

      {fireOpen && data && (
        <FireModal
          profile={data}
          toast={toast}
          onClose={() => setFireOpen(false)}
          onSaved={() => void refresh()}
        />
      )}

      {pickerOpen && (
        <MultiSelectModal
          label={PICKER_FIELD_LABELS[pickerOpen]}
          help={PICKER_FIELD_HELP[pickerOpen]}
          options={pickerOptionsFor(pickerOpen)}
          showSearch={PICKER_HAS_SEARCH[pickerOpen]}
          normalizeValue={pickerOpen === 'employment_types' ? (raw) => raw : toCountryCode}
          value={draft?.[pickerOpen] ?? []}
          onChange={(next) => set(pickerOpen, next)}
          onClose={() => setPickerOpen(null)}
          loading={loading}
        />
      )}
    </div>
  )
}
