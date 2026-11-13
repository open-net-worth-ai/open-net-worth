import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  clearEnableBankingReturnError,
  enableBankingReturnUrl,
  openBankPageTab,
  peekEnableBankingReturnError,
} from './bankPage'
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
import { EnableBankingBanks } from './EnableBankingBanks'
import { accessEnded, type EnableBankingBank } from './enableBankingBank'
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
  banks: EnableBankingBank[]
  last_synced_at: string | null
  last_status: string
  last_error: string | null
  last_caution?: string | null
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
  // A failed return from the bank lands here with its reason (ConnectorsTab keeps it).
  const [linkErr, setLinkErr] = useState<string | null>(peekEnableBankingReturnError)
  // Listing a country's banks has its own line: loading them on open must not wipe the reason
  // a bank return failed.
  const [banksErr, setBanksErr] = useState<string | null>(null)
  // Shown once: the reason from a failed bank return stays in this form, not the next one.
  useEffect(() => clearEnableBankingReturnError(), [])
  const [linking, setLinking] = useState(false)
  // After Link bank: whether the bank will bring the user back here (the app's URL is
  // registered), or to the old https://localhost/connectors page they copy the code from.
  const [returnsToApp, setReturnsToApp] = useState<boolean | null>(null)
  // Enable Banking refused this app's address. Step 1 holds the address to add; this keeps the
  // bank to retry with a code.
  const [unregistered, setUnregistered] = useState<{ target?: EnableBankingBank } | null>(null)
  const redirectRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (unregistered) redirectRef.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [unregistered])
  const [copied, setCopied] = useState(false)
  const [showPaste, setShowPaste] = useState(false)
  const [editingSetup, setEditingSetup] = useState(false)
  const [renewingKey, setRenewingKey] = useState<string | null>(null)
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
    setBanksErr(null)
    setInstitutions([])
    setAspsp('')
    try {
      const r = await authFetch(
        `/api/enable-banking/institutions?country=${encodeURIComponent(code)}`,
      )
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setBanksErr(d.detail || 'Could not list banks.')
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

  /** Start a consent: for the picked bank, or — Renew on a listed one — for that bank.
   *  `withCode`: the user chose to link by pasting the code (no address of ours is sent). */
  async function linkBank(target?: EnableBankingBank, withCode = false) {
    const bank = target ? target.aspsp : aspsp
    const code = target ? target.country : country
    if (!bank) return
    const tab = openBankPageTab()
    let opened = false
    setLinking(true)
    setRenewingKey(target?.key ?? null)
    setLinkErr(null)
    setUnregistered(null)
    try {
      const r = await authFetch('/api/enable-banking/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          aspsp: bank,
          country: code,
          redirect: withCode ? '' : enableBankingReturnUrl(),
        }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        if (d.detail?.code === 'redirect_not_registered') {
          setUnregistered({ target })
          // The code field would be a second way round; the link below is the one offered.
          setShowPaste(false)
          setReturnsToApp(null)
          return
        }
        setLinkErr(
          (typeof d.detail === 'string' ? d.detail : d.detail?.message) ||
            'Could not start the bank link.',
        )
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
      setRenewingKey(null)
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

  // The paste fallback stays folded away unless asked for, or unless this link chose it.
  const pasteOpen = showPaste || returnsToApp === false
  const banks = cfg.banks ?? []
  // Picking a bank that is already listed renews it (the server swaps the consent in).
  const renewing = banks.some((b) => b.aspsp === aspsp && b.country === country)
  // A consent past its end date, or a fetch the bank refused: access has run out somewhere.
  const consentExpired =
    banks.some((b) => accessEnded(b)) ||
    (cfg.has_session && /401|403|expired|unauthori[sz]ed|consent/i.test(cfg.last_error || ''))
  const appChanged = banks.filter((b) => b.access_ended === 'app_changed').length
  // Typing another app's ID while banks are linked under this one: say what Save will do.
  const switchingApp =
    !!form &&
    !!cfg.application_id &&
    !!form.application_id.trim() &&
    form.application_id.trim() !== cfg.application_id &&
    banks.some((b) => !b.access_ended)
  // What the last fetch couldn't read, less what a bank row below already says.
  const flagged = banks.filter((b) => accessEnded(b)).map((b) => b.aspsp)
  const caution = (cfg.last_caution ?? '')
    .split('; ')
    .filter((part) => part && !flagged.some((name) => name && part.startsWith(name)))
    .join('; ')
  // Step 1 stays open until its keys are saved, while being edited, or when Enable Banking
  // refused its Redirect URL (then only that field shows, unless the keys are being edited).
  const setupOpen = !cfg.has_credentials || editingSetup || Boolean(unregistered)
  const onlyRedirect = Boolean(unregistered) && cfg.has_credentials && !editingSetup
  // Keys saved but Enable Banking doesn't know the address: step 1 isn't done after all.
  const keysState: SetupStepState = cfg.has_credentials && !unregistered ? 'done' : 'current'
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

  const redirectField = (
    <div ref={redirectRef}>
      <FormField
        label="Redirect URL"
        help="Paste this into your Enable Banking app's Redirect URLs so your bank can bring you back here."
        error={
          unregistered ? (
            <>
              Enable Banking doesn&apos;t know this address yet. Add it under Redirect URLs in your
              app at{' '}
              <a href="https://enablebanking.com/cp/applications" target="_blank" rel="noreferrer">
                enablebanking.com
              </a>
              , save, then link your bank again.
            </>
          ) : undefined
        }
      >
        <Cluster gap="--space-2" align="center" wrap={false}>
          <code className="eb-url">{redirectUrl}</code>
          <Button size="sm" aria-label="Copy the redirect URL" onClick={copyRedirect}>
            {copied ? <Icons.check /> : <Icons.copy />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </Cluster>
      </FormField>
    </div>
  )

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
      {caution && (
        <Text size="xs" tone="warn">
          Last fetch couldn&apos;t read everything — {caution}
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
        {onlyRedirect ? (
          redirectField
        ) : setupOpen ? (
          <Stack gap="--space-3">
            <Text size="xs" tone="muted">
              Create an app at{' '}
              <a href="https://enablebanking.com/cp/applications" target="_blank" rel="noreferrer">
                enablebanking.com
              </a>{' '}
              and keep its Application ID and private key handy.
            </Text>
            {redirectField}
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
            {switchingApp && (
              <Text size="xs" tone="warn">
                Your banks are linked under your current app. Saving another app ends their access,
                and you&apos;ll renew each bank after.
              </Text>
            )}
            <FormField
              label="Private key"
              htmlFor="conn-eb-key"
              required
              help={
                privateKeyReveal.caption ||
                (switchingApp
                  ? "Paste the new app's private key."
                  : cfg.has_credentials
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

      <SetupStep n={2} title="Link your banks" state={linkState}>
        <Stack gap="--space-3">
          {consentExpired && (
            <Text size="xs" tone="warn">
              {appChanged > 1 && appChanged === banks.length
                ? 'Your banks were linked under your previous Enable Banking app. Click Renew next to each one to keep it syncing.'
                : appChanged
                  ? `${appChanged === 1 ? 'A bank was' : 'Some banks were'} linked under your previous Enable Banking app. Click Renew next to ${appChanged === 1 ? 'it' : 'each one'} to keep it syncing.`
                  : 'Access to a bank has ended. Click Renew next to it to keep it syncing.'}
            </Text>
          )}
          {banks.length > 0 ? (
            <>
              <EnableBankingBanks
                banks={banks}
                countryName={countryName}
                renewing={renewingKey}
                busy={linking}
                onRenew={(bank) => void linkBank(bank)}
                onChanged={load}
              />
              <Text size="xs" tone="muted">
                Access lasts about 90 days per bank. Add another bank below.
              </Text>
            </>
          ) : (
            <Text size="xs" tone={cfg.has_credentials ? 'muted' : 'faint'}>
              {cfg.has_credentials
                ? 'Pick your bank and approve access on its own site. You can add more banks after.'
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
              loading={linking && renewingKey === null}
              disabled={!aspsp || !cfg.has_credentials || linking}
              onClick={() => void linkBank()}
            >
              {renewing ? 'Renew link' : banks.length ? 'Add bank' : 'Link bank'}
            </Button>
          </Cluster>

          {banksErr && (
            <Text size="xs" tone="bad">
              {banksErr}
            </Text>
          )}
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
          {unregistered && (
            <Text size="xs" tone="warn">
              Add the Redirect URL from step 1 to your Enable Banking app first, then link again.
            </Text>
          )}
          {returnsToApp === false && (
            <Text size="xs" tone="warn">
              After you approve, the bank tab shows an error page. That&apos;s expected: copy the
              code from its address bar (the part after code=) and paste it below.
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
                {/* One way round the redirect at a time: while the address isn't registered,
                    linking with a code is it; otherwise, entering a code the bank showed. */}
                {unregistered ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="eb-link"
                    onClick={() => void linkBank(unregistered.target, true)}
                  >
                    Can&apos;t add it? Link with a code instead
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="eb-link"
                    onClick={() => setShowPaste(true)}
                  >
                    Bank didn&apos;t bring you back? Enter its code
                  </Button>
                )}
              </div>
            )
          )}
        </Stack>
      </SetupStep>

      {/* Re-read when a bank is added, renewed or removed — like PlaidSection's banksVersion —
          so a removed bank's accounts never linger in the list. */}
      <ConnectorAccountsList
        key={banks.map((b) => b.key).join(',')}
        provider="enable_banking"
        providerLabel="Enable Banking"
      />
    </Stack>
  )
}
