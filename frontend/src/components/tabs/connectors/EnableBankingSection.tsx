import { useCallback, useEffect, useRef, useState } from 'react'
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
import { Icons } from './icons'
import { SetupStep, type SetupStepState } from './SetupStep'

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

// Enable Banking covers the EEA and the UK; names come from the browser, in the user's language.
const COUNTRY_CODES = [
  'AT',
  'BE',
  'BG',
  'HR',
  'CY',
  'CZ',
  'DK',
  'EE',
  'FI',
  'FR',
  'DE',
  'GR',
  'HU',
  'IS',
  'IE',
  'IT',
  'LV',
  'LI',
  'LT',
  'LU',
  'MT',
  'NL',
  'NO',
  'PL',
  'PT',
  'RO',
  'SK',
  'SI',
  'ES',
  'SE',
  'GB',
]

function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(undefined, { type: 'region' }).of(code) || code
  } catch {
    return code
  }
}

const COUNTRY_OPTIONS = COUNTRY_CODES.map((code) => ({
  value: code,
  label: countryName(code),
})).sort((a, b) => a.label.localeCompare(b.label))

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
  const startCountry = useRef((initialCountry || 'FI').toUpperCase())
  const [country, setCountry] = useState(startCountry.current)
  const [institutions, setInstitutions] = useState<Institution[]>([])
  const [aspsp, setAspsp] = useState('')
  const [loadingBanks, setLoadingBanks] = useState(false)
  const [linkErr, setLinkErr] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)
  // After Link bank: whether the bank will bring the user back here (the app's URL is
  // registered), or to the old https://localhost/connectors page they copy the code from.
  const [returnsToApp, setReturnsToApp] = useState<boolean | null>(null)
  const [copied, setCopied] = useState(false)
  const [showPaste, setShowPaste] = useState(false)
  const [editingSetup, setEditingSetup] = useState(false)
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
        const code = c.country || startCountry.current
        if (c.country) setCountry(c.country)
        if (c.has_credentials) void loadBanks(code, c.aspsp)
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

  async function loadBanks(code: string, keep?: string) {
    setLoadingBanks(true)
    setLinkErr(null)
    setInstitutions([])
    setAspsp('')
    try {
      const r = await authFetch(
        `/api/enable-banking/institutions?country=${encodeURIComponent(code)}`,
      )
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setLinkErr(d.detail || 'Could not list banks.')
        return
      }
      const data = (await r.json()) as { institutions: Institution[] }
      const banks = data.institutions || []
      setInstitutions(banks)
      const pick = banks.find((i) => i.id === keep) ?? banks[0]
      if (pick) setAspsp(pick.id)
    } finally {
      setLoadingBanks(false)
    }
  }

  function chooseCountry(code: string) {
    setCountry(code)
    if (cfg?.has_credentials) void loadBanks(code)
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

  // The paste fallback stays folded away unless asked for, or unless this link fell back to it.
  const pasteOpen = showPaste || returnsToApp === false
  const renewing = Boolean(cfg.has_session && aspsp && aspsp === cfg.aspsp)
  // A fetch refused by the bank after a link existed: the ~90-day consent has run out.
  const consentExpired =
    cfg.has_session && /401|403|expired|unauthori[sz]ed|consent/i.test(cfg.last_error || '')
  // Step 1 stays open until its keys are saved, while being edited, or when a link fell back
  // to pasting a code (its Redirect URL is the fix).
  const setupOpen = !cfg.has_credentials || editingSetup || returnsToApp === false
  const keysState: SetupStepState = cfg.has_credentials ? 'done' : 'current'
  const linkState: SetupStepState = cfg.has_session
    ? 'done'
    : cfg.has_credentials
      ? 'current'
      : 'todo'
  const redirectUrl = enableBankingReturnUrl()
  function copyRedirect() {
    // Absent over plain http on another host: the value is on screen and selectable anyway.
    navigator.clipboard?.writeText(redirectUrl).catch(() => {})
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  const set = <K extends keyof EnableBankingForm>(k: K, v: EnableBankingForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4" className="gc-form">
      <Text size="sm" tone="faint">
        Banks in the EU and UK. You approve access on your bank&apos;s own site.
      </Text>

      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Currently paused — saving here won&apos;t resume it. Use Resume on the connector card to
          turn it back on.
        </Text>
      )}
      <ConnectorStatusLine lastSyncedAt={cfg.last_synced_at} lastStatus={cfg.last_status} />
      {cfg.last_error && !consentExpired && (
        <Text size="xs" tone="bad">
          Last error: {cfg.last_error}
        </Text>
      )}

      <SetupStep
        n={1}
        title="Register your app with Enable Banking"
        state={keysState}
        aside={
          cfg.has_credentials && !setupOpen ? (
            <Button size="sm" variant="ghost" onClick={() => setEditingSetup(true)}>
              Edit
            </Button>
          ) : undefined
        }
      >
        {setupOpen ? (
          <Stack gap="--space-3">
            <Text size="xs" tone="muted">
              Create an app at{' '}
              <a href="https://enablebanking.com/cp/applications" target="_blank" rel="noreferrer">
                enablebanking.com
              </a>{' '}
              and keep its Application ID and private key handy.
            </Text>
            <FormField
              label="Redirect URL"
              help="Paste this into your Enable Banking app's Redirect URLs so your bank can bring you back here."
            >
              <Cluster gap="--space-2" align="center" wrap={false}>
                <code className="eb-url">{redirectUrl}</code>
                <Button size="sm" aria-label="Copy the redirect URL" onClick={copyRedirect}>
                  {copied ? <Icons.check /> : <Icons.copy />}
                  {copied ? 'Copied' : 'Copy'}
                </Button>
              </Cluster>
            </FormField>
            <FormField
              label="Application ID"
              htmlFor="conn-eb-id"
              required
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
              label="Private key"
              htmlFor="conn-eb-key"
              required
              help={
                privateKeyReveal.caption ||
                (cfg.has_credentials
                  ? 'Saved. Leave blank to keep it.'
                  : 'Paste the whole .pem file, first and last lines included.')
              }
              error={
                privateKeyReveal.error ||
                (privateKeyTouched.touched && !privateKeyValid
                  ? 'A private key is required.'
                  : undefined)
              }
            >
              {cfg.has_credentials ? (
                // The eye belongs on this branch only — it's the one with a saved value to
                // reveal. A revealed PEM key is multi-line; that's acceptable in this
                // single-line input (it scrolls), and switching to a TextArea on reveal would
                // remount the field and drop focus.
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
                  placeholder="-----BEGIN PRIVATE KEY----- …"
                  onChange={(e) => set('private_key', e.target.value)}
                  onBlur={privateKeyTouched.onBlur}
                />
              )}
            </FormField>
            {!cfg.has_credentials && (
              <Text size="xs" tone="faint">
                Click Save at the bottom when both are filled in.
              </Text>
            )}
            {saveErr && (
              <Text size="xs" tone="bad">
                {saveErr}
              </Text>
            )}
          </Stack>
        ) : (
          <Text size="xs" tone="muted">
            Application ID {cfg.application_id.slice(0, 8)}… · private key saved
          </Text>
        )}
      </SetupStep>

      <SetupStep n={2} title="Link your bank" state={linkState}>
        <Stack gap="--space-3">
          {consentExpired && (
            <Text size="xs" tone="warn">
              Your bank access has expired. Link again to renew it.
            </Text>
          )}
          {cfg.has_session && cfg.aspsp ? (
            <Stack gap="--space-1">
              <Text size="sm">
                Linked: {cfg.aspsp} · {countryName(cfg.country)}
              </Text>
              <Text size="xs" tone="muted">
                Consent lasts about 90 days. Link again to renew it.
              </Text>
            </Stack>
          ) : (
            <Text size="xs" tone={cfg.has_credentials ? 'muted' : 'faint'}>
              {cfg.has_credentials
                ? 'Pick your bank and approve access on its own site.'
                : 'Save your application ID and private key first.'}
            </Text>
          )}

          <Cluster gap="--space-2" align="end" wrap className="gc-pick-row">
            <FormField label="Country" htmlFor="conn-eb-country">
              <Select
                id="conn-eb-country"
                value={country}
                options={COUNTRY_OPTIONS}
                disabled={!cfg.has_credentials || linking}
                onChange={(e) => chooseCountry(e.target.value)}
              />
            </FormField>
            <div className="gc-bank-pick">
              <FormField label="Bank" htmlFor="conn-eb-bank">
                <Select
                  id="conn-eb-bank"
                  value={aspsp}
                  placeholder={
                    loadingBanks
                      ? 'Loading banks…'
                      : cfg.has_credentials
                        ? 'No banks found for this country'
                        : 'Choose a bank'
                  }
                  options={institutions.map((i) => ({ value: i.id, label: i.name }))}
                  disabled={!institutions.length || linking}
                  onChange={(e) => setAspsp(e.target.value)}
                />
              </FormField>
            </div>
            <Button
              size="sm"
              variant="primary"
              loading={linking}
              disabled={!aspsp || !cfg.has_credentials}
              onClick={linkBank}
            >
              {renewing ? 'Renew link' : 'Link bank'}
            </Button>
          </Cluster>

          {linkErr && (
            <Text size="xs" tone="bad">
              {linkErr}
            </Text>
          )}
          {returnsToApp === true && (
            <Text size="xs" tone="muted">
              Approve access in the tab that just opened. Your bank brings you back here and the
              link finishes by itself.
            </Text>
          )}
          {returnsToApp === false && (
            <Text size="xs" tone="warn">
              Your bank will send you to a page that won&apos;t load. Copy the code from its address
              bar (the part after code=) and paste it below. To skip this next time, add the
              Redirect URL from step 1 to your Enable Banking app.
            </Text>
          )}

          {pasteOpen ? (
            <Cluster gap="--space-2" align="end" wrap className="gc-pick-row">
              <div className="gc-bank-pick">
                <FormField label="Code from the address bar" htmlFor="conn-eb-code">
                  <TextField
                    id="conn-eb-code"
                    value={authCode}
                    placeholder="the value after code="
                    onChange={(e) => setAuthCode(e.target.value)}
                  />
                </FormField>
              </div>
              <Button
                size="sm"
                loading={completing}
                disabled={!authCode.trim() || !cfg.has_credentials}
                onClick={completeLink}
              >
                Complete link
              </Button>
            </Cluster>
          ) : (
            cfg.has_credentials && (
              <div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="eb-link"
                  onClick={() => setShowPaste(true)}
                >
                  Bank didn&apos;t bring you back? Enter its code
                </Button>
              </div>
            )
          )}
        </Stack>
      </SetupStep>

      <ConnectorAccountsList provider="enable_banking" providerLabel="Enable Banking" />
    </Stack>
  )
}
