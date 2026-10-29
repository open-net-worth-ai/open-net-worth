import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  Stack,
  Cluster,
  Text,
  Button,
  FormField,
  TextField,
  PasswordField,
  Select,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'

// Plaid connector config over /api/plaid/* (per-provider plane), mirroring EnableBankingSection.
// The secret is read back through its own eye (GET /api/connectors/plaid/secret/secret) — the
// access token stays write-only. The GET echoes only `has_secret` / `has_access_token`; a blank
// secret field leaves the saved value unchanged. The bank LINK flow uses Plaid HOSTED LINK (no
// frontend Link-SDK): "Link bank" opens a Plaid-returned URL in a new tab where the user
// authenticates at their bank. Hosted Link ends on Plaid's own "you can close this window" page
// and never shows a public token, so this form polls POST /api/plaid/link/complete — the server
// reads the session's public token back from Plaid and exchanges it for an access token. This form owns the config fields (creds /
// environment / enable) + Link; fetching/booking transactions is now Lucius's job via a
// template (see the connector page's Automate button) — the unified /api/connectors PATCH owns the
// card-header enable toggle. Save lives in the modal footer — see ./formSave.

interface PlaidStatus {
  configured: boolean
  enabled: boolean
  environment: string
  has_secret: boolean
  client_id: string
  has_access_token: boolean
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

interface PlaidForm {
  client_id: string
  secret: string
  environment: string
}

/** The server's values as a form — the seed for `load()` AND the baseline the footer's
 *  Save compares against, so the two can never drift apart. */
function plaidFormOf(c: PlaidStatus): PlaidForm {
  return {
    client_id: c.client_id || '',
    secret: '',
    environment: c.environment || 'sandbox',
  }
}

// How often, and for how long, to ask the server whether the Hosted Link session finished.
const LINK_POLL_MS = 3000
const LINK_POLL_LIMIT_MS = 15 * 60 * 1000

const ENV_OPTIONS = [
  { value: 'sandbox', label: 'Sandbox (test banks)' },
  { value: 'production', label: 'Production (live)' },
]

export function PlaidSection() {
  const [cfg, setCfg] = useState<PlaidStatus | null>(null)
  const [form, setForm] = useState<PlaidForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  // Bank-link flow state.
  const [linking, setLinking] = useState(false)
  const [linkErr, setLinkErr] = useState<string | null>(null)
  const [waiting, setWaiting] = useState(false)
  const [checking, setChecking] = useState(false)
  const [linkedNote, setLinkedNote] = useState(false)
  // Bumped after a successful link so the Accounts list re-fetches the bank's accounts.
  const [accountsKey, setAccountsKey] = useState(0)
  const pollStarted = useRef(0)

  function load() {
    setError(false)
    authFetch('/api/plaid/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: PlaidStatus) => {
        setCfg(c)
        setForm(plaidFormOf(c))
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
        client_id: form.client_id.trim(),
        environment: form.environment,
      }
      if (form.secret.trim()) body.secret = form.secret.trim()
      const r = await authFetch('/api/plaid/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the credentials.')
        return false
      }
      setForm((f) => f && { ...f, secret: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  async function linkBank() {
    setLinking(true)
    setLinkErr(null)
    try {
      const r = await authFetch('/api/plaid/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setLinkErr(d.detail || 'Could not start the bank link. Save your credentials first.')
        return
      }
      const data = (await r.json()) as { link: string }
      if (data.link) {
        window.open(data.link, '_blank', 'noopener,noreferrer')
        setLinkedNote(false)
        pollStarted.current = Date.now()
        setWaiting(true)
      }
    } finally {
      setLinking(false)
    }
  }

  const checkLink = useCallback(async (manual: boolean) => {
    if (manual) setChecking(true)
    try {
      const r = await authFetch('/api/plaid/link/complete', { method: 'POST' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setLinkErr(d.detail || 'Could not complete the bank link.')
        setWaiting(false)
        return
      }
      const d = (await r.json()) as { status: string }
      if (d.status === 'linked') {
        setWaiting(false)
        setLinkErr(null)
        setLinkedNote(true)
        setAccountsKey((k) => k + 1)
        load()
      } else if (manual) {
        setLinkErr('Plaid has not finished yet — complete the sign-in in the Plaid tab first.')
      }
    } catch {
      if (manual) setLinkErr('Could not reach the server.')
    } finally {
      if (manual) setChecking(false)
    }
  }, [])

  useEffect(() => {
    if (!waiting) return
    const id = window.setInterval(() => {
      if (Date.now() - pollStarted.current > LINK_POLL_LIMIT_MS) {
        setWaiting(false)
        return
      }
      void checkLink(false)
    }, LINK_POLL_MS)
    return () => window.clearInterval(id)
  }, [waiting, checkLink])

  const setSecret = useCallback((v: string) => setForm((f) => f && { ...f, secret: v }), [])
  const secret = useSecretReveal({
    saved: !!cfg?.has_secret,
    value: form?.secret ?? '',
    onChange: setSecret,
    reveal: useCallback(() => revealConnectorSecret('plaid', 'secret'), []),
  })

  const clientIdValid = !form || !!form.client_id.trim()
  const secretValid = !form || !!form.secret.trim() || !!cfg?.has_secret
  const secretTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && clientIdValid && secretValid,
    values: form && { ...form, secret: unrevealed(form.secret, secret) },
    baseline: cfg ? plaidFormOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Plaid connector status.
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

  const set = <K extends keyof PlaidForm>(k: K, v: PlaidForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Add your{' '}
        <a href="https://plaid.com/docs/" target="_blank" rel="noreferrer">
          Plaid
        </a>{' '}
        API credentials, then link a bank — you&apos;ll sign in at your own bank in a new tab, and
        the link completes here on its own once Plaid says you can close that window. Fetching and
        booking transactions is Lucius's job — automate it with a template below.
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
        label="Client ID"
        htmlFor="conn-plaid-id"
        required
        help="Your Plaid client_id (from the Plaid dashboard)."
      >
        <TextField
          id="conn-plaid-id"
          value={form.client_id}
          placeholder="000000000000000000000000"
          onChange={(e) => set('client_id', e.target.value)}
        />
      </FormField>

      <FormField
        label="Secret"
        htmlFor="conn-plaid-secret"
        required
        help={secret.caption || 'Your Plaid secret for the selected environment.'}
        error={
          secret.error ||
          (secretTouched.touched && !secretValid ? 'A Plaid secret is required.' : undefined)
        }
      >
        <PasswordField
          id="conn-plaid-secret"
          value={form.secret}
          placeholder={cfg.has_secret ? '••••••••' : 'secret'}
          onChange={(e) => set('secret', e.target.value)}
          onBlur={secretTouched.onBlur}
          {...secret.fieldProps}
        />
      </FormField>

      <FormField label="Environment" htmlFor="conn-plaid-env">
        <Select
          id="conn-plaid-env"
          value={form.environment}
          options={ENV_OPTIONS}
          onChange={(e) => set('environment', e.target.value)}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      {/* ── Bank link flow (Hosted Link → paste public token → exchange) ── */}
      <Stack gap="--space-3">
        <Text size="sm" tone="ink">
          {cfg.has_access_token ? 'Linked bank' : 'Link a bank'}
        </Text>
        <Cluster gap="--space-3" align="center" wrap>
          <Button size="sm" loading={linking} disabled={!cfg.has_secret} onClick={linkBank}>
            {cfg.has_access_token ? 'Re-link bank' : 'Link bank'}
          </Button>
          <Text size="xs" tone="faint">
            Opens Plaid Hosted Link in a new tab.
          </Text>
        </Cluster>

        {waiting && (
          <Cluster gap="--space-3" align="center" wrap>
            <Text size="xs" tone="faint">
              Waiting for you to finish at Plaid…
            </Text>
            <Button
              size="sm"
              variant="secondary"
              loading={checking}
              onClick={() => checkLink(true)}
            >
              Check now
            </Button>
          </Cluster>
        )}

        {linkedNote && !waiting && (
          <Text size="xs" tone="good">
            Bank linked. Its accounts are listed below — link each one to an account here.
          </Text>
        )}

        {linkErr && (
          <Text size="xs" tone="bad">
            {linkErr}
          </Text>
        )}
      </Stack>

      <ConnectorAccountsList key={accountsKey} provider="plaid" providerLabel="Plaid" />
    </Stack>
  )
}
