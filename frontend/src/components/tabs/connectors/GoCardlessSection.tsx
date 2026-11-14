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
import { BankHandoff } from './BankHandoff'
import { ConnectorAccountsList } from './ConnectorAccountsList'
import { GoCardlessBanks } from './GoCardlessBanks'
import { isPending, type GoCardlessBank } from './gocardlessBank'
import { bankReturnUrl, openBankPageTab } from './bankPage'

// GoCardless Bank Account Data connector config over /api/gocardless/*. A blank secret-key
// field keeps the saved key; the GET echoes only `has_credentials`. One connector holds any
// number of banks (./GoCardlessBanks), like Plaid. Linking opens the bank page GoCardless
// returns in a new tab (./bankPage — opened on the click, so no popup blocker eats it) and keeps
// the link on screen as a fallback; there is no code to paste — when the user comes back, Check links (run
// automatically on open while a link is unfinished) re-reads every requisition. Fetching
// transactions is Lucius's job (the connector page's Automate button); Save lives in the modal
// footer — see ./formSave.

interface GoCardlessStatus {
  configured: boolean
  enabled: boolean
  has_credentials: boolean
  secret_id: string
  has_link: boolean
  banks: GoCardlessBank[]
  last_synced_at: string | null
  last_status: string
  last_error: string | null
  last_caution: string | null
}

interface Institution {
  id: string
  name: string
}

interface GoCardlessForm {
  secret_id: string
  secret_key: string
}

function goCardlessFormOf(c: GoCardlessStatus): GoCardlessForm {
  return { secret_id: c.secret_id || '', secret_key: '' }
}

export function GoCardlessSection({ initialCountry }: { initialCountry?: string }) {
  const [cfg, setCfg] = useState<GoCardlessStatus | null>(null)
  const [form, setForm] = useState<GoCardlessForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  const [country, setCountry] = useState((initialCountry || 'GB').toUpperCase())
  const [institutions, setInstitutions] = useState<Institution[]>([])
  const [institutionId, setInstitutionId] = useState('')
  const [loadingBanks, setLoadingBanks] = useState(false)
  const [linkErr, setLinkErr] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)
  const [checking, setChecking] = useState(false)
  const [bankLink, setBankLink] = useState<{ url: string; opened: boolean; bank: string } | null>(
    null,
  )
  const autoChecked = useRef(false)

  const apply = useCallback((c: GoCardlessStatus) => {
    setCfg(c)
    setForm(goCardlessFormOf(c))
  }, [])

  const checkLink = useCallback(async () => {
    setChecking(true)
    setLinkErr(null)
    try {
      const r = await authFetch('/api/gocardless/check-link', { method: 'POST' })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) {
        setLinkErr(d.detail || 'Could not check the bank links.')
        return
      }
      apply(d as GoCardlessStatus)
    } catch {
      setLinkErr('Could not check the bank links — could not reach the server.')
    } finally {
      setChecking(false)
    }
  }, [apply])

  const load = useCallback(() => {
    authFetch('/api/gocardless/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: GoCardlessStatus) => {
        setError(false)
        apply(c)
        // Coming back from the bank: the link may have finished while this was closed.
        if (c.banks.some(isPending) && c.has_credentials && !autoChecked.current) {
          autoChecked.current = true
          void checkLink()
        }
      })
      .catch(() => setError(true))
  }, [apply, checkLink])

  useEffect(load, [load])

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = {
        enabled: cfg.configured ? cfg.enabled : true,
        secret_id: form.secret_id.trim(),
      }
      if (form.secret_key.trim()) body.secret_key = form.secret_key.trim()
      const r = await authFetch('/api/gocardless/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the credentials.')
        return false
      }
      apply((await r.json()) as GoCardlessStatus)
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
        `/api/gocardless/institutions?country=${encodeURIComponent(country)}`,
      )
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setLinkErr(d.detail || 'Could not list banks. Save your credentials first.')
        return
      }
      const data = (await r.json()) as { institutions: Institution[] }
      setInstitutions(data.institutions || [])
      setInstitutionId(data.institutions?.[0]?.id ?? '')
    } catch {
      setLinkErr('Could not list banks — could not reach the server.')
    } finally {
      setLoadingBanks(false)
    }
  }

  async function linkBank() {
    if (!institutionId) return
    const tab = openBankPageTab()
    setLinking(true)
    setLinkErr(null)
    setBankLink(null)
    let opened = false
    try {
      const r = await authFetch('/api/gocardless/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          institution_id: institutionId,
          country,
          redirect: bankReturnUrl(),
        }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) {
        setLinkErr(d.detail || 'Could not start the bank link.')
        return
      }
      if (d.link) {
        opened = tab.go(d.link)
        const bank = institutions.find((i) => i.id === institutionId)?.name ?? ''
        setBankLink({ url: d.link, opened, bank })
      }
      autoChecked.current = true
      load()
    } catch {
      setLinkErr('Could not start the bank link — could not reach the server.')
    } finally {
      if (!opened) tab.cancel()
      setLinking(false)
    }
  }

  const setSecretKey = useCallback((v: string) => setForm((f) => f && { ...f, secret_key: v }), [])
  const secretKeyReveal = useSecretReveal({
    saved: !!cfg?.has_credentials,
    value: form?.secret_key ?? '',
    onChange: setSecretKey,
    reveal: useCallback(() => revealConnectorSecret('gocardless', 'secret_key'), []),
  })

  const secretIdValid = !form || !!form.secret_id.trim()
  const secretKeyValid = !form || !!form.secret_key.trim() || !!cfg?.has_credentials
  const secretIdTouched = useTouched()
  const secretKeyTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && secretIdValid && secretKeyValid,
    values: form && { ...form, secret_key: unrevealed(form.secret_key, secretKeyReveal) },
    baseline: cfg ? goCardlessFormOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the GoCardless connector status.
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

  const set = <K extends keyof GoCardlessForm>(k: K, v: GoCardlessForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })
  const banks = cfg.banks ?? []
  // A bank whose row already says it needs the user is not repeated in the fetch caution.
  const flagged = banks.filter((b) => b.status !== 'LN').map((b) => `${b.institution_name}: `)
  const caution = (cfg.last_caution ?? '')
    .split('; ')
    .filter((part) => part && !flagged.some((f) => part.startsWith(f)))
    .join('; ')

  return (
    <Stack gap="--space-4" className="gc-form">
      <Text size="sm" tone="faint">
        For an existing{' '}
        <a href="https://bankaccountdata.gocardless.com" target="_blank" rel="noreferrer">
          GoCardless Bank Account Data
        </a>{' '}
        account — GoCardless stopped taking new signups in 2025. Create a secret under User secrets,
        paste it here, then link each of your banks and authorise access at the bank&apos;s own
        site. Banks allow only a few reads a day per account.
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
        label="Secret ID"
        htmlFor="conn-gc-id"
        required
        error={secretIdTouched.touched && !secretIdValid ? 'A secret ID is required.' : undefined}
      >
        <TextField
          id="conn-gc-id"
          value={form.secret_id}
          placeholder="00000000-0000-0000-0000-000000000000"
          onChange={(e) => set('secret_id', e.target.value)}
          onBlur={secretIdTouched.onBlur}
        />
      </FormField>

      <FormField
        label="Secret key"
        htmlFor="conn-gc-key"
        required
        help={secretKeyReveal.caption || undefined}
        error={
          secretKeyReveal.error ||
          (secretKeyTouched.touched && !secretKeyValid ? 'A secret key is required.' : undefined)
        }
      >
        <PasswordField
          id="conn-gc-key"
          value={form.secret_key}
          placeholder={cfg.has_credentials ? '••••••••' : ''}
          onChange={(e) => set('secret_key', e.target.value)}
          onBlur={secretKeyTouched.onBlur}
          {...(cfg.has_credentials ? secretKeyReveal.fieldProps : {})}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      {banks.length > 0 && (
        <Stack gap="--space-2" className="gc-section">
          <Text size="sm" tone="ink" weight={600}>
            Your banks
          </Text>
          <GoCardlessBanks banks={banks} onChanged={load} onLink={setBankLink} />
          {caution && (
            <Text size="xs" tone="warn">
              Last fetch couldn&apos;t read everything — {caution}
            </Text>
          )}
          {banks.some(isPending) && (
            <Cluster gap="--space-3" align="center">
              <Button size="sm" variant="secondary" loading={checking} onClick={checkLink}>
                Check links
              </Button>
            </Cluster>
          )}
        </Stack>
      )}

      <Stack gap="--space-2" className="gc-section">
        <Text size="sm" tone="ink" weight={600}>
          {banks.length ? 'Add a bank' : 'Link a bank'}
        </Text>
        <Cluster gap="--space-2" align="end" wrap className="gc-pick-row">
          <FormField label="Country" htmlFor="conn-gc-country">
            <TextField
              id="conn-gc-country"
              value={country}
              placeholder="GB"
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
        {!cfg.has_credentials && (
          <Text size="xs" tone="faint">
            Save your secret ID and key first.
          </Text>
        )}

        {institutions.length > 0 && (
          <Cluster gap="--space-2" align="end" wrap className="gc-pick-row">
            <div className="gc-bank-pick">
              <FormField label="Bank" htmlFor="conn-gc-bank">
                <Select
                  id="conn-gc-bank"
                  value={institutionId}
                  options={institutions.map((i) => ({ value: i.id, label: i.name }))}
                  onChange={(e) => setInstitutionId(e.target.value)}
                />
              </FormField>
            </div>
            <Button
              size="sm"
              variant="primary"
              loading={linking}
              disabled={!institutionId}
              onClick={linkBank}
            >
              {banks.length ? 'Link this bank' : 'Link bank'}
            </Button>
          </Cluster>
        )}

        {linkErr && (
          <Text size="xs" tone="bad">
            {linkErr}
          </Text>
        )}
      </Stack>

      {bankLink && banks.some(isPending) && (
        <div className="gc-section">
          <BankHandoff bank={bankLink.bank} url={bankLink.url} opened={bankLink.opened} />
        </div>
      )}

      <div className="gc-section">
        <ConnectorAccountsList provider="gocardless" providerLabel="GoCardless" />
      </div>
    </Stack>
  )
}
