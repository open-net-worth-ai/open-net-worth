import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Text, FormField, PasswordField, useSecretReveal } from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { ConnectorStatusLine } from './ConnectorStatusLine'

// YNAB connector config over /api/ynab/* (per-provider plane), mirroring LunchMoneySection's
// secret-reveal pattern: the access token is something the operator TYPED into YNAB's own
// Account Settings -> Developer Settings page and pasted here, so it is REVEALABLE through the
// shared eye (GET /api/connectors/ynab/secret/access_token). The GET only ever echoes
// has_access_token; a blank token field on save leaves the saved one unchanged.
//
// The token is ALL this form holds. A token can see several YNAB plans, but which one to read is
// decided where it is used, never here: Lucius names a plan per call (ynab_list_plans →
// plan_id), and the YNAB data sync (Automations) picks its own. Save lives in the modal
// footer — see ./formSave; the card-header enable toggle is the unified /api/connectors PATCH.

interface YnabStatus {
  configured: boolean
  enabled: boolean
  has_access_token: boolean
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

interface YnabForm {
  access_token: string
}

/** The seed for `load()` AND the baseline the footer's Save compares against.
 *  `access_token` never round-trips (write-only), so it always reads blank — mirrors Lunch
 *  Money. */
const BLANK_FORM: YnabForm = { access_token: '' }

export function YnabSection() {
  const [cfg, setCfg] = useState<YnabStatus | null>(null)
  const [form, setForm] = useState<YnabForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    authFetch('/api/ynab/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: YnabStatus) => {
        setCfg(c)
        setForm(BLANK_FORM)
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = { enabled: cfg.configured ? cfg.enabled : true }
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

  // The one credential the connector needs (mirrors `YnabConfigStore.is_configured`). Typed
  // fresh OR already-saved both count as valid, same as Email's password.
  const tokenValid = !form || !!form.access_token.trim() || !!cfg?.has_access_token
  const tokenTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && tokenValid,
    values: form && { ...form, access_token: unrevealed(form.access_token, token) },
    baseline: cfg ? BLANK_FORM : form,
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

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        If you already track spending in{' '}
        <a href="https://www.ynab.com" target="_blank" rel="noreferrer">
          YNAB
        </a>
        , paste a personal access token from its Account Settings → Developer Settings page. Lucius
        can then read any of your plans.
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

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Stack>
  )
}
