import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  Stack,
  Cluster,
  Text,
  Button,
  FormField,
  TextField,
  PasswordField,
  SegmentedControl,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'
import { SetupStep, type SetupStepState } from './SetupStep'
import { usePlaidLink } from './usePlaidLink'
import { ConnectorNextStep } from './ConnectorNextStep'

// Plaid connector setup over /api/plaid/* — three numbered steps, in the order a new user must
// follow: ① the API keys (client_id + write-only secret + environment), ② link a bank (Hosted
// Link, see ./usePlaidLink — "Link bank" saves unsaved keys first, so there is no "Save or Link
// first?" question), ③ choose which of the bank's accounts to track (ConnectorAccountsList).
// The secret is read back through its own eye (GET /api/connectors/plaid/secret/secret); the
// access token stays write-only. Fetching/booking transactions is Lucius's job (the list's
// "Next" step opens the sync Automation). The footer Save (./formSave) stays for editing keys
// later; the unified /api/connectors PATCH owns the card-header enable toggle.

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

const ENV_OPTIONS = [
  { value: 'sandbox', label: 'Sandbox' },
  { value: 'production', label: 'Production' },
]

const ENV_HELP: Record<string, string> = {
  sandbox: "Plaid's test banks — sign in with user_good / pass_good. Use the Sandbox secret.",
  production: 'Your real bank. Needs Plaid to approve your account, and the Production secret.',
}

export function PlaidSection() {
  const [cfg, setCfg] = useState<PlaidStatus | null>(null)
  const [form, setForm] = useState<PlaidForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [editingKeys, setEditingKeys] = useState(false)
  // Step ③'s list reports how many of the bank's accounts are linked — step ④ depends on it.
  const [counts, setCounts] = useState({ linked: 0, total: 0, live: false })

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
  const link = usePlaidLink(load)

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
        setSaveErr(d.detail || 'Could not save the keys.')
        return false
      }
      setForm((f) => f && { ...f, secret: '' })
      setEditingKeys(false)
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

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

  const keysEntered = !!form.client_id.trim() && (!!form.secret.trim() || cfg.has_secret)
  const keysDirty =
    form.client_id.trim() !== (cfg.client_id || '') ||
    form.environment !== cfg.environment ||
    !!form.secret.trim()
  const keysSaved = cfg.has_secret && !keysDirty
  const showKeyFields = !cfg.has_secret || editingKeys || keysDirty
  // A link this page just completed counts at once — not only after the status reload lands.
  const linked = cfg.has_access_token || link.justLinked

  /** Link bank saves unsaved keys first — never a "Save or Link first?" choice. */
  async function linkBank() {
    if (!keysSaved && !(await save())) return
    await link.start()
  }

  const keysState: SetupStepState = keysSaved ? 'done' : 'current'
  const linkState: SetupStepState = linked ? 'done' : keysEntered ? 'current' : 'todo'
  const hasLinkedAccount = counts.live && counts.linked > 0
  const accountsState: SetupStepState = hasLinkedAccount ? 'done' : linked ? 'current' : 'todo'
  const syncState: SetupStepState = hasLinkedAccount ? 'current' : 'todo'
  const set = <K extends keyof PlaidForm>(k: K, v: PlaidForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="muted">
        Connect US and Canadian bank accounts through your own{' '}
        <a href="https://dashboard.plaid.com" target="_blank" rel="noreferrer">
          Plaid
        </a>{' '}
        developer account. Read-only: nothing here can move money.
      </Text>

      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Paused — nothing syncs. Use Resume on the connector card to turn it back on.
        </Text>
      )}
      {cfg.last_error && (
        <Text size="xs" tone="bad">
          Last error: {cfg.last_error}
        </Text>
      )}

      <SetupStep
        n={1}
        title="Your Plaid keys"
        state={keysState}
        aside={
          !showKeyFields && (
            <Button size="sm" variant="ghost" onClick={() => setEditingKeys(true)}>
              Edit keys
            </Button>
          )
        }
      >
        {showKeyFields ? (
          <Stack gap="--space-3">
            <Text size="xs" tone="faint">
              From the Plaid dashboard → Developers → Keys.
            </Text>
            <FormField label="Client ID" htmlFor="conn-plaid-id" required>
              <TextField
                id="conn-plaid-id"
                value={form.client_id}
                placeholder="24 characters"
                onChange={(e) => set('client_id', e.target.value)}
              />
            </FormField>
            <FormField
              label="Secret"
              htmlFor="conn-plaid-secret"
              required
              help={secret.caption || undefined}
              error={
                secret.error ||
                (secretTouched.touched && !secretValid ? 'A Plaid secret is required.' : undefined)
              }
            >
              <PasswordField
                id="conn-plaid-secret"
                value={form.secret}
                placeholder={cfg.has_secret ? '••••••••' : 'The secret for the environment below'}
                onChange={(e) => set('secret', e.target.value)}
                onBlur={secretTouched.onBlur}
                {...secret.fieldProps}
              />
            </FormField>
            <Stack gap="--space-1">
              <Text size="xs" tone="ink" weight={500}>
                Environment
              </Text>
              <SegmentedControl
                ariaLabel="Environment"
                options={ENV_OPTIONS}
                value={form.environment}
                onChange={(v) => set('environment', v)}
              />
              <Text size="xs" tone="faint">
                {ENV_HELP[form.environment]}
              </Text>
            </Stack>
            {saveErr && (
              <Text size="xs" tone="bad">
                {saveErr}
              </Text>
            )}
          </Stack>
        ) : (
          <Text size="xs" tone="muted">
            {cfg.environment === 'production' ? 'Production' : 'Sandbox'} · Client ID ••
            {cfg.client_id.slice(-4)}
          </Text>
        )}
      </SetupStep>

      <SetupStep
        n={2}
        title="Link your bank"
        state={linkState}
        aside={
          linked &&
          !link.waiting && (
            <Button size="sm" variant="ghost" loading={link.starting} onClick={linkBank}>
              Re-link bank
            </Button>
          )
        }
      >
        <Stack gap="--space-3">
          {link.waiting ? (
            <Cluster gap="--space-3" align="center" wrap>
              <Text size="xs" tone="muted">
                Waiting for you to finish at Plaid… this page finishes on its own.
              </Text>
              <Button size="sm" variant="secondary" loading={link.checking} onClick={link.checkNow}>
                Check now
              </Button>
            </Cluster>
          ) : linked ? (
            <Text size="xs" tone={link.justLinked ? 'good' : 'muted'}>
              {link.justLinked ? 'Bank linked. ' : 'Linked. '}Choose its accounts below.
            </Text>
          ) : (
            <>
              <Text size="xs" tone="muted">
                Sign in at your bank in your browser. When Plaid says you can close the window, come
                back — this page finishes the link on its own.
              </Text>
              <Cluster gap="--space-3" align="center" wrap>
                <Button
                  size="sm"
                  variant="primary"
                  loading={link.starting || saving}
                  disabled={!keysEntered}
                  onClick={linkBank}
                >
                  Link bank
                </Button>
                {!keysEntered && (
                  <Text size="xs" tone="faint">
                    Enter your Client ID and Secret first.
                  </Text>
                )}
              </Cluster>
            </>
          )}
          {link.error && (
            <Text size="xs" tone="bad">
              {link.error}
            </Text>
          )}
        </Stack>
      </SetupStep>

      <SetupStep n={3} title="Choose and link accounts" state={accountsState}>
        <Stack gap="--space-3">
          <ConnectorAccountsList
            key={link.linkCount}
            provider="plaid"
            providerLabel="Plaid"
            embedded
            onCounts={setCounts}
            notConfiguredText={{
              title: 'No bank linked yet',
              body: 'Link a bank above — its accounts appear here.',
            }}
          />
        </Stack>
      </SetupStep>

      <SetupStep n={4} title="Bring in transactions" state={syncState}>
        <Stack gap="--space-2">
          {hasLinkedAccount ? (
            <ConnectorNextStep
              provider="plaid"
              providerLabel="Plaid"
              linkedCount={counts.linked}
              unlinkedCount={counts.total - counts.linked}
              bare
            />
          ) : (
            <Text size="xs" tone="muted">
              Link an account above first — then Lucius can add its transactions.
            </Text>
          )}
          {cfg.last_synced_at && (
            <ConnectorStatusLine lastSyncedAt={cfg.last_synced_at} lastStatus={cfg.last_status} />
          )}
        </Stack>
      </SetupStep>
    </Stack>
  )
}
