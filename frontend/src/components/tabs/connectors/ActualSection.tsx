import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { invalidateQueryCache, useQueryCache } from '../../../state/queryCache'
import { FormField, PasswordField, SegmentedControl, Stack, Text, TextField } from '../../ui'
import { ACTUAL_APP } from '../automations/datasync/budgetApps'
import { PlanPicker } from '../imports/YnabImport'
import { usePlans } from '../imports/plans'
import { detailOf } from '../imports/runs'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { useConnectorSave, useTouched } from './formSave'
import { CONNECTORS_KEY } from './useConnectors'

// The Actual connector's form: the address of the person's own server, how they sign in to it, and
// which budget. Every secret is write-only: unlike YNAB's token, nothing here reads one back, so a
// saved one shows as a placeholder and a blank field keeps it. Save lives in the modal footer. The
// budget list doubles as the connection test, so it only loads once a sign-in is saved.

const STATUS_KEY = '/api/actual-budget/status'

interface ActualStatus {
  configured: boolean
  enabled: boolean
  available: boolean
  has_password: boolean
  has_token: boolean
  has_encryption_password: boolean
  has_server_url: boolean
  server_host: string | null
  server_plain_http: boolean
  budget_id: string | null
  budget_name: string | null
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

type SignIn = 'password' | 'token'
const SIGN_IN_OPTIONS: { value: SignIn; label: string }[] = [
  { value: 'password', label: 'Password' },
  { value: 'token', label: 'Token' },
]

const parsedUrl = (value: string) => {
  try {
    return new URL(value)
  } catch {
    return null
  }
}

function isServerUrl(value: string) {
  const url = parsedUrl(value)
  return !!url && (url.protocol === 'http:' || url.protocol === 'https:') && !!url.hostname
}

/** Plain http to anything but this computer; the server applies the same rule to a saved one. */
function isPlainHttpRemote(value: string) {
  const url = parsedUrl(value)
  if (url?.protocol !== 'http:') return false
  const host = url.hostname.toLowerCase()
  return !(
    host === 'localhost' ||
    host.endsWith('.localhost') ||
    /^127\.\d+\.\d+\.\d+$/.test(host) ||
    host === '[::1]'
  )
}

export function ActualSection() {
  const status = useQueryCache<ActualStatus>(STATUS_KEY, async () => {
    const r = await authFetch(STATUS_KEY)
    if (!r.ok) throw new Error(await detailOf(r, 'Could not load the Actual connector status.'))
    return (await r.json()) as ActualStatus
  })
  if (status.error && !status.data) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Actual connector status.
      </Text>
    )
  }
  if (!status.data) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }
  if (!status.data.available) {
    return (
      <Text size="sm" tone="muted">
        Actual isn&apos;t available on a hosted instance. It connects straight to your own server,
        and a hosted copy of the app can&apos;t safely reach your network.
      </Text>
    )
  }
  return <ActualForm cfg={status.data} refresh={() => void status.refresh()} />
}

function ActualForm({ cfg, refresh }: { cfg: ActualStatus; refresh: () => void }) {
  const [url, setUrl] = useState('')
  const [signIn, setSignIn] = useState<SignIn>(
    cfg.has_token && !cfg.has_password ? 'token' : 'password',
  )
  const [password, setPassword] = useState('')
  const [token, setToken] = useState('')
  const [encryption, setEncryption] = useState('')
  // Only what was picked here: the rest follows the server, or a stale read would be saved back.
  const [pick, setPick] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const urlTouched = useTouched()
  const passwordTouched = useTouched()
  const tokenTouched = useTouched()

  const budgets = usePlans(ACTUAL_APP, cfg.configured)
  const list = budgets.data ?? []
  const budgetId = pick ?? cfg.budget_id ?? ''
  const picked = list.find((b) => b.id === budgetId)
  if (pick !== null && pick === cfg.budget_id) setPick(null)
  if (!budgetId && list.length === 1) setPick(list[0]!.id)

  // A read that was refused, or stopped being, changes the connector's status elsewhere on screen.
  const readOutcome = budgets.error ? 'refused' : budgets.data ? 'read' : null
  useEffect(() => {
    if (readOutcome) invalidateQueryCache(CONNECTORS_KEY)
  }, [readOutcome])

  const secret = signIn === 'password' ? password : token
  const secretSaved = signIn === 'password' ? cfg.has_password : cfg.has_token
  const urlTyped = url.trim()
  const urlValid = urlTyped ? isServerUrl(urlTyped) : cfg.has_server_url
  const secretValid = !!secret.trim() || secretSaved
  const needsEncryption = !!picked?.encrypted && !cfg.has_encryption_password && !encryption.trim()
  const budgetChange = picked && budgetId !== cfg.budget_id ? budgetId : ''
  const plainHttp = urlTyped ? isPlainHttpRemote(urlTyped) : cfg.server_plain_http

  async function save(): Promise<boolean> {
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, string> = {}
      if (urlTyped) body.server_url = urlTyped
      if (secret.trim()) body[signIn] = secret.trim()
      if (encryption.trim()) body.encryption_password = encryption.trim()
      if (budgetChange) body.budget_id = budgetChange
      const r = await authFetch('/api/actual-budget/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        setSaveErr(await detailOf(r, 'Could not save.'))
        return false
      }
      setUrl('')
      setPassword('')
      setToken('')
      setEncryption('')
      invalidateQueryCache('/api/actual-budget/budgets')
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      // Refused or not: the sign-in is saved before the budget pick is checked.
      refresh()
      setSaving(false)
    }
  }

  useConnectorSave({
    ready: urlValid && secretValid && !needsEncryption,
    values: { url: urlTyped, secret: secret.trim(), encryption: encryption.trim(), budgetChange },
    baseline: { url: '', secret: '', encryption: '', budgetChange: '' },
    saving,
    error: saveErr,
    save,
  })

  const secretWord = signIn === 'password' ? 'password' : 'token'
  const secretTouched = signIn === 'password' ? passwordTouched : tokenTouched
  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        If you keep your budget in{' '}
        <a href="https://actualbudget.org" target="_blank" rel="noreferrer">
          Actual
        </a>
        , enter the address of your server and how you sign in to it, then choose a budget. You
        import it and keep it in step from Automations, under Data sync.
      </Text>

      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Currently paused — saving here won&apos;t resume it. Use Resume on the connector card to
          turn it back on.
        </Text>
      )}
      {cfg.last_synced_at && (
        <ConnectorStatusLine lastSyncedAt={cfg.last_synced_at} lastStatus={cfg.last_status} />
      )}
      {cfg.last_error && (
        <Text size="xs" tone="bad">
          Last error: {cfg.last_error}
        </Text>
      )}

      <FormField
        label="Server address"
        htmlFor="conn-actual-url"
        required
        help={
          plainHttp ? (
            <Text size="sm" tone="warn">
              This address uses plain http, which isn&apos;t encrypted. Anyone on that network could
              read your budget as it downloads. It still works; use https if you can.
            </Text>
          ) : cfg.has_server_url ? (
            'Saved. Type a new address to replace it.'
          ) : (
            'The address you open Actual at.'
          )
        }
        error={
          urlTouched.touched && !urlValid
            ? urlTyped
              ? 'The server address must start with http:// or https:// and name a host.'
              : 'A server address is required.'
            : undefined
        }
      >
        <TextField
          id="conn-actual-url"
          value={url}
          placeholder={cfg.server_host ?? 'https://actual.example.com'}
          autoComplete="off"
          spellCheck={false}
          autoCapitalize="none"
          inputMode="url"
          onChange={(e) => setUrl(e.target.value)}
          onBlur={urlTouched.onBlur}
        />
      </FormField>

      <FormField label="Sign in with">
        <SegmentedControl
          ariaLabel="Sign in with"
          value={signIn}
          onChange={setSignIn}
          options={SIGN_IN_OPTIONS}
        />
      </FormField>

      <FormField
        key={signIn}
        label={signIn === 'password' ? 'Password' : 'Token'}
        htmlFor={`conn-actual-${signIn}`}
        required
        help={
          secretSaved
            ? `A ${secretWord} is saved. Leave blank to keep it.`
            : signIn === 'password'
              ? 'The password you sign in to your Actual server with.'
              : "For a server that doesn't take a password."
        }
        error={secretTouched.touched && !secretValid ? `A ${secretWord} is required.` : undefined}
      >
        <PasswordField
          id={`conn-actual-${signIn}`}
          value={secret}
          placeholder={secretSaved ? '••••••••' : undefined}
          autoComplete="new-password"
          onChange={(e) => (signIn === 'password' ? setPassword : setToken)(e.target.value)}
          onBlur={secretTouched.onBlur}
        />
      </FormField>

      {cfg.configured ? (
        <PlanPicker
          app={ACTUAL_APP}
          plans={budgets}
          planId={picked ? budgetId : ''}
          onChange={setPick}
          id="conn-actual-budget"
          label="Budget"
          framed
          help={
            cfg.budget_id && !list.some((b) => b.id === cfg.budget_id) ? (
              <Text size="sm" tone="warn">
                {cfg.budget_name
                  ? `${cfg.budget_name} is no longer on this server. Choose another.`
                  : 'The budget you chose is no longer on this server. Choose another.'}
              </Text>
            ) : (
              `Connected to ${cfg.server_host}.`
            )
          }
        />
      ) : (
        <FormField label="Budget">
          <Text size="sm" tone="faint">
            Your budgets appear here once you save an address and sign-in.
          </Text>
        </FormField>
      )}

      <FormField
        label="Encryption password"
        htmlFor="conn-actual-encryption"
        required={needsEncryption}
        help={
          cfg.has_encryption_password
            ? 'An encryption password is saved. Leave blank to keep it.'
            : 'Only if this budget has end-to-end encryption on in Actual.'
        }
        error={
          needsEncryption
            ? `${picked?.name} is encrypted, so it needs its encryption password.`
            : undefined
        }
      >
        <PasswordField
          id="conn-actual-encryption"
          value={encryption}
          placeholder={cfg.has_encryption_password ? '••••••••' : undefined}
          autoComplete="new-password"
          onChange={(e) => setEncryption(e.target.value)}
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
