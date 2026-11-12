import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { enableBankingReturnUrl, openBankPageTab } from './bankPage'
import {
  Stack,
  Cluster,
  Text,
  Button,
  FormField,
  TextField,
  PasswordField,
  TextArea,
  Select,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'

// Enable Banking (EU PSD2 open banking) connector config over /api/enable-banking/* (per-
// provider plane), mirroring PlaidSection. A blank private-key field leaves the saved creds
// unchanged and the GET echoes only `has_credentials` — the saved key is read back through
// the field's own eye (GET /api/connectors/enable_banking/secret/private_key).
// The bank LINK flow opens an Enable-Banking-returned consent URL in a new tab where the user
// authenticates at their bank; the post-consent authorization code is then pasted back here to
// complete the session. This form owns the config fields (creds / bank / enable) + Link +
// Complete; fetching transactions is now Lucius's job via a template (see
// the connector page's Automate button) — the unified /api/connectors PATCH owns the card-header enable
// toggle. Save lives in the modal footer — see ./formSave.

interface EnableBankingStatus {
  configured: boolean
  enabled: boolean
  has_credentials: boolean
  application_id: string
  aspsp: string
  country: string
  has_session: boolean
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

interface Institution {
  id: string
  name: string
  country: string | null
  logo: string | null
}

interface EnableBankingForm {
  application_id: string
  private_key: string
}

/** The form as the server last returned it — both the seed for `load()` and the baseline
 *  the footer's Save compares against, so the two can never drift apart. */
function enableBankingFormOf(c: EnableBankingStatus): EnableBankingForm {
  return {
    application_id: c.application_id || '',
    private_key: '',
  }
}

export function EnableBankingSection({ initialCountry }: { initialCountry?: string }) {
  const [cfg, setCfg] = useState<EnableBankingStatus | null>(null)
  const [form, setForm] = useState<EnableBankingForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  // Bank-link flow state.
  const [country, setCountry] = useState((initialCountry || 'FI').toUpperCase())
  const [institutions, setInstitutions] = useState<Institution[]>([])
  const [aspsp, setAspsp] = useState('')
  const [loadingBanks, setLoadingBanks] = useState(false)
  const [linkErr, setLinkErr] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)
  // After Link bank: whether the bank will bring the user back here (the app's URL is
  // registered), or to the old https://localhost/connectors page they copy the code from.
  const [returnsToApp, setReturnsToApp] = useState<boolean | null>(null)
  // Post-consent code → session exchange.
  const [authCode, setAuthCode] = useState('')
  const [completing, setCompleting] = useState(false)

  function load() {
    setError(false)
    authFetch('/api/enable-banking/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: EnableBankingStatus) => {
        setCfg(c)
        setForm(enableBankingFormOf(c))
        if (c.aspsp) setAspsp(c.aspsp)
        if (c.country) setCountry(c.country)
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
        application_id: form.application_id.trim(),
      }
      if (form.private_key.trim()) body.private_key = form.private_key.trim()
      const r = await authFetch('/api/enable-banking/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the credentials.')
        return false
      }
      setForm((f) => f && { ...f, private_key: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  async function loadBanks() {
    setLoadingBanks(true)
    setLinkErr(null)
    setInstitutions([])
    try {
      const r = await authFetch(
        `/api/enable-banking/institutions?country=${encodeURIComponent(country)}`,
      )
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setLinkErr(d.detail || 'Could not list banks. Save your credentials first.')
        return
      }
      const data = (await r.json()) as { institutions: Institution[] }
      setInstitutions(data.institutions || [])
      const first = data.institutions?.[0]
      if (first) setAspsp(first.id)
    } finally {
      setLoadingBanks(false)
    }
  }

  async function linkBank() {
    if (!aspsp) return
    const tab = openBankPageTab()
    let opened = false
    setLinking(true)
    setLinkErr(null)
    try {
      const r = await authFetch('/api/enable-banking/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aspsp, country, redirect: enableBankingReturnUrl() }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setLinkErr(d.detail || 'Could not start the bank link.')
        return
      }
      const data = (await r.json()) as { url: string; returns_to_app?: boolean }
      setReturnsToApp(data.returns_to_app === true)
      if (data.url) {
        opened = tab.go(data.url)
        if (!opened) window.open(data.url, '_blank', 'noopener,noreferrer')
      }
      load()
    } finally {
      if (!opened) tab.cancel()
      setLinking(false)
    }
  }

  async function completeLink() {
    if (!authCode.trim()) return
    setCompleting(true)
    setLinkErr(null)
    try {
      const r = await authFetch('/api/enable-banking/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: authCode.trim() }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setLinkErr(d.detail || 'Could not complete the bank link.')
        return
      }
      setAuthCode('')
      load()
    } finally {
      setCompleting(false)
    }
  }

  const setPrivateKey = useCallback(
    (v: string) => setForm((f) => f && { ...f, private_key: v }),
    [],
  )
  const privateKeyReveal = useSecretReveal({
    saved: !!cfg?.has_credentials,
    value: form?.private_key ?? '',
    onChange: setPrivateKey,
    reveal: useCallback(() => revealConnectorSecret('enable_banking', 'private_key'), []),
  })

  const applicationIdValid = !form || !!form.application_id.trim()
  const privateKeyValid = !form || !!form.private_key.trim() || !!cfg?.has_credentials
  const applicationIdTouched = useTouched()
  const privateKeyTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && applicationIdValid && privateKeyValid,
    values: form && { ...form, private_key: unrevealed(form.private_key, privateKeyReveal) },
    baseline: cfg ? enableBankingFormOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Enable Banking connector status.
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

  const set = <K extends keyof EnableBankingForm>(k: K, v: EnableBankingForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Register an application with{' '}
        <a href="https://enablebanking.com" target="_blank" rel="noreferrer">
          Enable Banking
        </a>{' '}
        to get an application ID and RSA private key. Then link a bank: you&apos;ll authorise access
        at the bank&apos;s own site and paste the code it returns into the field below.
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
        label="Application ID"
        htmlFor="conn-eb-id"
        required
        help="Your Enable Banking application id (the JWT key id)."
        error={
          applicationIdTouched.touched && !applicationIdValid
            ? 'An application ID is required.'
            : undefined
        }
      >
        <TextField
          id="conn-eb-id"
          value={form.application_id}
          placeholder="00000000-0000-0000-0000-000000000000"
          onChange={(e) => set('application_id', e.target.value)}
          onBlur={applicationIdTouched.onBlur}
        />
      </FormField>

      <FormField
        label="Private key (PEM)"
        htmlFor="conn-eb-key"
        required
        help={privateKeyReveal.caption || 'Your application RSA private key, PEM format.'}
        error={
          privateKeyReveal.error ||
          (privateKeyTouched.touched && !privateKeyValid ? 'A private key is required.' : undefined)
        }
      >
        {cfg.has_credentials ? (
          // The eye belongs on this branch only — it's the one with a saved value to reveal.
          // A revealed PEM key is multi-line; that's acceptable in this single-line input (it
          // scrolls), and switching to a TextArea on reveal would remount the field and drop
          // focus.
          <PasswordField
            id="conn-eb-key"
            value={form.private_key}
            placeholder="••••••••"
            onChange={(e) => set('private_key', e.target.value)}
            onBlur={privateKeyTouched.onBlur}
            {...privateKeyReveal.fieldProps}
          />
        ) : (
          <TextArea
            id="conn-eb-key"
            rows={4}
            value={form.private_key}
            placeholder="-----BEGIN PRIVATE KEY-----&#10;…&#10;-----END PRIVATE KEY-----"
            onChange={(e) => set('private_key', e.target.value)}
            onBlur={privateKeyTouched.onBlur}
          />
        )}
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      {/* ── Bank link flow ── */}
      <Stack gap="--space-3">
        <Text size="sm">{cfg.has_session ? 'Linked bank' : 'Link a bank'}</Text>
        <Cluster gap="--space-2" align="end" wrap>
          <FormField label="Country" htmlFor="conn-eb-country">
            <TextField
              id="conn-eb-country"
              value={country}
              placeholder="FI"
              maxLength={2}
              style={{ width: '5rem' }}
              onChange={(e) => setCountry(e.target.value.toUpperCase())}
            />
          </FormField>
          <Button
            size="sm"
            variant="secondary"
            loading={loadingBanks}
            disabled={!cfg.has_credentials}
            onClick={loadBanks}
          >
            List banks
          </Button>
        </Cluster>

        {institutions.length > 0 && (
          <FormField label="Bank" htmlFor="conn-eb-bank">
            <Select
              id="conn-eb-bank"
              value={aspsp}
              options={institutions.map((i) => ({ value: i.id, label: i.name }))}
              onChange={(e) => setAspsp(e.target.value)}
            />
          </FormField>
        )}

        {linkErr && (
          <Text size="xs" tone="bad">
            {linkErr}
          </Text>
        )}

        <Cluster gap="--space-3" align="center">
          <Button size="sm" loading={linking} disabled={!aspsp} onClick={linkBank}>
            Link bank
          </Button>
        </Cluster>

        {returnsToApp === true && (
          <Text size="xs" tone="muted">
            After you approve access, your bank brings you back here and the link finishes by
            itself.
          </Text>
        )}
        {returnsToApp === false && (
          <Text size="xs" tone="muted">
            Your bank will send you to a page that won&apos;t load — copy the code from its address
            and paste it below. To come back here automatically instead, add{' '}
            <code>{enableBankingReturnUrl()}</code> to your Enable Banking application&apos;s
            allowed redirect URLs.
          </Text>
        )}

        <FormField
          label="Authorization code"
          htmlFor="conn-eb-code"
          help="Only needed if your bank didn't bring you back here: paste the code from the address it sent you to, then Complete."
        >
          <TextField
            id="conn-eb-code"
            value={authCode}
            placeholder="paste the returned code"
            onChange={(e) => setAuthCode(e.target.value)}
          />
        </FormField>

        <Cluster gap="--space-3" align="center">
          <Button
            size="sm"
            loading={completing}
            disabled={!authCode.trim() || !cfg.has_credentials}
            onClick={completeLink}
          >
            Complete link
          </Button>
        </Cluster>
      </Stack>

      <ConnectorAccountsList provider="enable_banking" providerLabel="Enable Banking" />
    </Stack>
  )
}
