// Settings → Currency & format. Ported from the CurrencySection in
// design_handoffs/settings_handoff/prototype/redesign/settings.jsx.
//
// Base currency is the ONE select in Settings that does not commit on change: balances and
// transactions carry a native currency and convert correctly, but amounts typed in by hand do
// not — the same numbers are simply re-read as the new currency. The confirm modal names
// exactly which ones and where each lives.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { PROFILE_KEY, fetchJson } from '../../../../hooks/serverState'
import {
  invalidateQueryCache,
  setCachedQueryData,
  useCachedFetchState,
} from '../../../../state/queryCache'
import { browserTimeZone } from '../../../../hooks/useTimezoneCapture'
import { fBase, setBaseCurrency, setDisplayFormats } from '../../../../utils/format'
import { Btn, Field, Modal, Panel, Select, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
// One currency list for the whole app — this section and first run read the same module, so
// they cannot drift. The 14-code array that used to live here could not serve CHF/KRW/BRL/AED.
import { CURRENCY_OPTIONS } from '../../../firstrun/currencies'

interface ProfileShape {
  base_currency?: string
  date_format?: string | null
  number_format?: string | null
  timezone?: string | null
  timezone_source?: 'auto' | 'user' | null
  [k: string]: unknown
}

interface PreviewField {
  label: string
  value: number
}

// Where each hand-typed amount is edited, so the modal can say where to go and fix it.
// The labels are the backend's (services/profile.currency_sensitive_fields).
function whereItLives(label: string): 'profile' | 'plan' {
  return label.startsWith('FIRE annual') ? 'plan' : 'profile'
}

// Every zone the browser knows; older engines lack `supportedValuesOf`, so fall back to the two
// we can always name.
function timeZoneOptions(extra: Array<string | null | undefined>): string[] {
  let zones: string[]
  try {
    zones = Intl.supportedValuesOf('timeZone')
  } catch {
    zones = ['UTC']
  }
  const all = new Set(zones)
  for (const z of extra) if (z) all.add(z)
  return [...all].sort()
}

export function CurrencySection({ toast }: SectionProps) {
  const {
    data: profile,
    initialLoading,
    refresh,
  } = useCachedFetchState<ProfileShape>(PROFILE_KEY, () => fetchJson<ProfileShape>(PROFILE_KEY), {
    staleTime: 0,
  })
  useSectionBusy(initialLoading && !profile)
  const [pending, setPending] = useState<string | null>(null)
  const [preview, setPreview] = useState<PreviewField[] | null>(null)

  const ccy = (profile?.base_currency || 'SGD').toUpperCase()
  const browserZone = browserTimeZone()
  const zone = profile?.timezone || browserZone || 'UTC'

  async function saveTimezone(zone: string) {
    try {
      const res = await authFetch(`${PROFILE_KEY}/timezone`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timezone: zone }),
      })
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as { detail?: string } | null
        toast(body?.detail || `Could not save (HTTP ${res.status})`)
        return
      }
      const state = (await res.json()) as Pick<ProfileShape, 'timezone' | 'timezone_source'>
      setCachedQueryData<ProfileShape>(PROFILE_KEY, (prev) => ({ ...(prev ?? {}), ...state }))
      toast('Time zone updated')
    } catch {
      toast('Could not reach the server. Try again.')
      void refresh()
    }
  }

  async function save(patch: Partial<ProfileShape>, message: string) {
    try {
      const res = await authFetch(PROFILE_KEY, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as { detail?: string } | null
        toast(body?.detail || `Could not save (HTTP ${res.status})`)
        return
      }
      // PUT /api/profile answers with an envelope, not the bare profile.
      const { profile: saved } = (await res.json()) as { profile: ProfileShape }
      setCachedQueryData(PROFILE_KEY, saved)
      if (patch.base_currency) setBaseCurrency(saved.base_currency || 'SGD')
      setDisplayFormats(saved)
      invalidateQueryCache('/api/fire')
      toast(message)
    } catch {
      toast('Could not reach the server. Try again.')
      void refresh()
    }
  }

  // Ask the server which manual amounts would NOT convert — the same computation it stamps
  // after the change, so the warning can never drift from the consequence.
  async function openConfirm(next: string) {
    setPending(next)
    setPreview(null)
    try {
      const r = await authFetch('/api/profile/base-currency-preview')
      if (!r.ok) return
      const body = (await r.json()) as { fields: PreviewField[] }
      setPreview(body.fields || [])
    } catch {
      /* the modal still explains the consequence, just without the itemised list */
    }
  }

  return (
    <>
      <Panel
        title="Base currency"
        sub="Every balance and total is converted to this and displayed in it"
      >
        <div className="rd-formgrid">
          <Field
            label="Base currency"
            help="Accounts keep their own native currency — this is the display and reporting unit"
          >
            <Select
              value={ccy}
              onChange={(e) => {
                if (e.target.value !== ccy) void openConfirm(e.target.value)
              }}
            >
              {(CURRENCY_OPTIONS.some((o) => o.value === ccy)
                ? CURRENCY_OPTIONS
                : [{ value: ccy, label: ccy }, ...CURRENCY_OPTIONS]
              ).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Panel>

      <Panel
        title="Number & date format"
        sub="How amounts and dates are written throughout the app"
      >
        <div className="rd-formgrid">
          <Field label="Date format">
            <Select
              value={(profile?.date_format as string) || ''}
              onChange={(e) => void save({ date_format: e.target.value }, 'Date format updated')}
            >
              <option value="">Browser default</option>
              <option value="dmy">31/12/2026</option>
              <option value="mdy">12/31/2026</option>
              <option value="iso">2026-12-31</option>
            </Select>
          </Field>
          <Field label="Number format">
            <Select
              value={(profile?.number_format as string) || ''}
              onChange={(e) =>
                void save({ number_format: e.target.value }, 'Number format updated')
              }
            >
              <option value="">Browser default</option>
              <option value="comma">1,234,567.89</option>
              <option value="space">1 234 567,89</option>
              <option value="dot">1.234.567,89</option>
            </Select>
          </Field>
        </div>
      </Panel>

      <Panel
        title="Time zone"
        sub="Decides what 'today' is and what time Lucius thinks it is — and when daily digests arrive"
      >
        <div className="rd-formgrid">
          <Field
            label="Time zone"
            help={
              <>
                <span>
                  {profile?.timezone_source === 'user'
                    ? 'Set by you'
                    : profile?.timezone
                      ? 'Detected from this browser — change it if that is wrong'
                      : 'Not set yet — the server clock is used until one is saved'}
                </span>
                {browserZone && profile?.timezone && profile.timezone !== browserZone && (
                  <span className="rd-help-action">
                    <Btn size="xs" onClick={() => void saveTimezone(browserZone)}>
                      Use this browser's ({browserZone})
                    </Btn>
                  </span>
                )}
              </>
            }
          >
            <Select value={zone} onChange={(e) => void saveTimezone(e.target.value)}>
              {timeZoneOptions([zone]).map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Panel>

      {pending && (
        <Modal
          title={`Switch base currency to ${pending}?`}
          onClose={() => setPending(null)}
          footer={
            <>
              <Btn onClick={() => setPending(null)}>Cancel</Btn>
              <Btn
                variant="primary"
                onClick={() => {
                  const next = pending
                  setPending(null)
                  void save(
                    { base_currency: next },
                    'Base currency is now ' + next + ' — re-check your manual amounts',
                  )
                }}
              >
                Switch to {pending}
              </Btn>
            </>
          }
        >
          <p style={{ margin: 0 }}>
            Account balances and transactions <b>do</b> convert — they carry a native currency.
            Amounts you typed in by hand will <b>not</b>: the same numbers will simply be read as{' '}
            {pending}. You'll want to re-enter these afterwards.
          </p>
          {preview === null ? (
            <span className="rd-help">Checking which amounts are affected…</span>
          ) : preview.length ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {preview.map((f) => (
                <div
                  key={f.label}
                  style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5 }}
                >
                  <span>{f.label}</span>
                  <b className="rd-mono" style={{ color: 'var(--ink)' }}>
                    {fBase(f.value)}
                  </b>
                </div>
              ))}
            </div>
          ) : (
            <span className="rd-help">
              You have no hand-typed amounts, so nothing needs re-entering.
            </span>
          )}
          {preview?.some((f) => whereItLives(f.label) === 'profile') ||
          preview?.some((f) => whereItLives(f.label) === 'plan') ? (
            <span className="rd-help">
              Income, expenses and the low-balance threshold live on <b>Profile</b>; retirement
              spend lives in <b>Plan → FIRE Settings</b>.
            </span>
          ) : null}
        </Modal>
      )}
    </>
  )
}
