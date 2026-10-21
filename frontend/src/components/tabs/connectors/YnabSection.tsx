import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  Stack,
  Cluster,
  Text,
  Button,
  FormField,
  PasswordField,
  Select,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'
import { YnabImportLink } from './YnabImportLink'

// YNAB connector config over /api/ynab/* (per-provider plane), mirroring LunchMoneySection's
// secret-reveal pattern: the access token is something the operator TYPED into YNAB's own
// Account Settings -> Developer Settings page and pasted here, so it is REVEALABLE through the
// shared eye (GET /api/connectors/ynab/secret/access_token). The GET only ever echoes
// has_access_token; a blank token field on save leaves the saved one unchanged.
//
// The ONE thing SimpleFIN/Lunch Money don't have: a token can see MULTIPLE budgets, so this
// form also owns a budget picker (fetched from GET /api/ynab/budgets once a token is saved).
// With exactly one budget the fetch tool auto-picks it — the picker still shows it so the
// operator can see which one; with several, picking one here is required before a fetch can do
// anything useful (see docs/adr/2026-08-17-ynab-multiple-budgets-selection.md). This form owns
// the config fields (token / budget / enable); fetching transactions is now Lucius's job via a
// template (see the connector page's Automate button) — the unified /api/connectors PATCH owns the
// card-header enable toggle. Save lives in the modal footer — see ./formSave.

interface YnabBudget {
  id: string
  name: string
  currency: string | null
}

interface YnabStatus {
  configured: boolean
  enabled: boolean
  has_access_token: boolean
  budget_id: string | null
  budget_name: string | null
  budget_currency: string | null
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

interface YnabForm {
  access_token: string
  budget_id: string
}

/** The server's values as a form — the seed for `load()` AND the baseline the footer's
 *  Save compares against. `access_token` never round-trips (write-only), so the baseline
 *  always reads it as blank — mirrors Lunch Money. */
function ynabFormOf(c: YnabStatus): YnabForm {
  return {
    access_token: '',
    budget_id: c.budget_id || '',
  }
}

export function YnabSection() {
  const [cfg, setCfg] = useState<YnabStatus | null>(null)
  const [form, setForm] = useState<YnabForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  const [budgets, setBudgets] = useState<YnabBudget[] | null>(null)
  const [loadingBudgets, setLoadingBudgets] = useState(false)
  const [budgetsErr, setBudgetsErr] = useState<string | null>(null)

  async function loadBudgets() {
    setLoadingBudgets(true)
    setBudgetsErr(null)
    try {
      const r = await authFetch('/api/ynab/budgets')
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setBudgetsErr(d.detail || 'Could not list budgets for this token.')
        return
      }
      const data = (await r.json()) as { budgets: YnabBudget[] }
      setBudgets(data.budgets || [])
    } catch {
      setBudgetsErr('Could not reach the server.')
    } finally {
      setLoadingBudgets(false)
    }
  }

  function load() {
    setError(false)
    authFetch('/api/ynab/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: YnabStatus) => {
        setCfg(c)
        setForm(ynabFormOf(c))
        if (c.configured) loadBudgets()
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = {
        enabled: cfg.configured ? cfg.enabled : true,
        budget_id: form.budget_id,
      }
      if (form.access_token.trim()) body.access_token = form.access_token.trim()
      const r = await authFetch('/api/ynab/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save.')
        return false
      }
      setForm((f) => f && { ...f, access_token: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const setToken = useCallback((v: string) => setForm((f) => f && { ...f, access_token: v }), [])
  const token = useSecretReveal({
    saved: !!cfg?.has_access_token,
    value: form?.access_token ?? '',
    onChange: setToken,
    reveal: useCallback(() => revealConnectorSecret('ynab', 'access_token'), []),
  })

  // The one credential the connector needs to talk to YNAB at all (mirrors
  // `YnabConfigStore.is_configured` — deliberately NOT gated on `budget_id`, which is a
  // runtime auto-detect-if-one decision, not a config requirement). Typed fresh OR
  // already-saved both count as valid, same as Email's password.
  const tokenValid = !form || !!form.access_token.trim() || !!cfg?.has_access_token
  const tokenTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && tokenValid,
    values: form && { ...form, access_token: unrevealed(form.access_token, token) },
    baseline: cfg ? ynabFormOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the YNAB connector status.
      </Text>
    )
  }
  if (!cfg || !form) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }

  const set = <K extends keyof YnabForm>(k: K, v: YnabForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  const budgetOptions = (
    budgets && budgets.length > 0
      ? budgets
      : cfg.budget_id
        ? [
            {
              id: cfg.budget_id,
              name: cfg.budget_name || cfg.budget_id,
              currency: cfg.budget_currency,
            },
          ]
        : []
  ).map((b) => ({
    value: b.id,
    label: b.currency ? `${b.name} (${b.currency})` : b.name,
  }))

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        If you already track spending in{' '}
        <a href="https://www.ynab.com" target="_blank" rel="noreferrer">
          YNAB
        </a>
        , paste a personal access token from its Account Settings → Developer Settings page.
        Fetching and booking transactions is Lucius's job — automate it with a template below.
      </Text>

      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Currently paused — saving here won&apos;t resume it. Use Resume on the connector card to
          turn it back on.
        </Text>
      )}

      <ConnectorStatusLine lastSyncedAt={cfg.last_synced_at} lastStatus={cfg.last_status} />
      {cfg.last_error && (
        <Text size="xs" tone="bad">
          Last error: {cfg.last_error}
        </Text>
      )}

      <FormField
        label="Access token"
        htmlFor="conn-ynab-token"
        required
        help={
          token.caption ||
          'Your YNAB personal access token (Account Settings → Developer Settings).'
        }
        error={
          token.error ||
          (tokenTouched.touched && !tokenValid ? 'An access token is required.' : undefined)
        }
      >
        <PasswordField
          id="conn-ynab-token"
          value={form.access_token}
          placeholder={cfg.has_access_token ? '••••••••' : 'a1b2c3d4e5f6…'}
          onChange={(e) => set('access_token', e.target.value)}
          onBlur={tokenTouched.onBlur}
          {...token.fieldProps}
        />
      </FormField>

      {cfg.configured && (
        <FormField
          label="Budget"
          htmlFor="conn-ynab-budget"
          help={
            budgetsErr ||
            'A token that can see more than one budget needs one picked here; with only one, it is used automatically.'
          }
        >
          <Cluster gap="--space-2" align="center" wrap>
            <Select
              id="conn-ynab-budget"
              value={form.budget_id}
              placeholder={loadingBudgets ? 'Loading budgets…' : 'Select a budget…'}
              options={budgetOptions}
              disabled={loadingBudgets}
              onChange={(e) => set('budget_id', e.target.value)}
            />
            <Button size="sm" variant="secondary" loading={loadingBudgets} onClick={loadBudgets}>
              Refresh
            </Button>
          </Cluster>
        </FormField>
      )}

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      <ConnectorAccountsList provider="ynab" providerLabel="YNAB" />

      <YnabImportLink />
    </Stack>
  )
}
