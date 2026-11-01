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

// GoCardless Bank Account Data connector config over /api/gocardless/*, mirroring
// EnableBankingSection. A blank secret-key field keeps the saved key; the GET echoes only
// `has_credentials`. Linking opens the bank page GoCardless returns in a new tab; there is no
// code to paste — when the user comes back, Check link (run automatically on open) re-reads
// the requisition. Fetching transactions is Lucius's job (the connector page's Automate
// button); Save lives in the modal footer — see ./formSave.

interface GoCardlessStatus {
  configured: boolean
  enabled: boolean
  has_credentials: boolean
  secret_id: string
  institution_id: string
  country: string
  has_requisition: boolean
  requisition_status: string
  has_link: boolean
  consent_expires_at: string | null
  linked_accounts: number
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

interface Institution {
  id: string
  name: string
}

interface GoCardlessForm {
  secret_id: string
  secret_key: string
}

// GoCardless requisition statuses that end without a link — only a new link recovers them.
const ENDED: Record<string, string> = {
  EX: 'The bank access has expired. Link the bank again to keep fetching.',
  RJ: 'The bank refused access. Link the bank again to retry.',
  SU: 'GoCardless suspended this link. Link the bank again.',
  ER: 'The bank link failed. Link the bank again.',
}

function goCardlessFormOf(c: GoCardlessStatus): GoCardlessForm {
  return { secret_id: c.secret_id || '', secret_key: '' }
}

function linkSummary(cfg: GoCardlessStatus): { text: string; tone: 'faint' | 'warn' | 'bad' } {
  if (cfg.has_link) {
    const until = cfg.consent_expires_at
      ? ` Access lasts until about ${new Date(cfg.consent_expires_at).toLocaleDateString()}.`
      : ''
    return { text: `Linked to ${cfg.institution_id}.${until}`, tone: 'faint' }
  }
  const ended = ENDED[cfg.requisition_status]
  if (ended) return { text: ended, tone: 'bad' }
  return {
    text: 'Finish authorising at your bank, then select Check link.',
    tone: 'warn',
  }
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
  const autoChecked = useRef(false)

  const apply = useCallback((c: GoCardlessStatus) => {
    setCfg(c)
    setForm(goCardlessFormOf(c))
    if (c.country) setCountry(c.country)
  }, [])

  const checkLink = useCallback(async () => {
    setChecking(true)
    setLinkErr(null)
    try {
      const r = await authFetch('/api/gocardless/check-link', { method: 'POST' })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) {
        setLinkErr(d.detail || 'Could not check the bank link.')
        return
      }
      apply(d as GoCardlessStatus)
    } catch {
      setLinkErr('Could not check the bank link — could not reach the server.')
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
        const pending = c.has_requisition && !c.has_link && !ENDED[c.requisition_status]
        if (pending && c.has_credentials && !autoChecked.current) {
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
    setLinking(true)
    setLinkErr(null)
    try {
      const r = await authFetch('/api/gocardless/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          institution_id: institutionId,
          country,
          redirect: window.location.origin,
        }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) {
        setLinkErr(d.detail || 'Could not start the bank link.')
        return
      }
      if (d.link) window.open(d.link, '_blank', 'noopener,noreferrer')
      autoChecked.current = true
      load()
    } catch {
      setLinkErr('Could not start the bank link — could not reach the server.')
    } finally {
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
  const summary = cfg.has_requisition ? linkSummary(cfg) : null

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        For an existing{' '}
        <a href="https://bankaccountdata.gocardless.com" target="_blank" rel="noreferrer">
          GoCardless Bank Account Data
        </a>{' '}
        account — GoCardless stopped taking new signups in 2025. Create a secret under User secrets,
        paste it here, then link a bank and authorise access at the bank&apos;s own site. Banks
        allow only a few reads a day per account.
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

      <Stack gap="--space-3">
        <Text size="sm">{cfg.has_link ? 'Linked bank' : 'Link a bank'}</Text>
        {summary && (
          <Text size="xs" tone={summary.tone}>
            {summary.text}
          </Text>
        )}
        {cfg.has_requisition && !ENDED[cfg.requisition_status] && (
          <Cluster gap="--space-3" align="center">
            <Button size="sm" variant="secondary" loading={checking} onClick={checkLink}>
              Check link
            </Button>
          </Cluster>
        )}
        <Cluster gap="--space-2" align="end" wrap>
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

        {institutions.length > 0 && (
          <FormField label="Bank" htmlFor="conn-gc-bank">
            <Select
              id="conn-gc-bank"
              value={institutionId}
              options={institutions.map((i) => ({ value: i.id, label: i.name }))}
              onChange={(e) => setInstitutionId(e.target.value)}
            />
          </FormField>
        )}

        {linkErr && (
          <Text size="xs" tone="bad">
            {linkErr}
          </Text>
        )}

        <Cluster gap="--space-3" align="center">
          <Button size="sm" loading={linking} disabled={!institutionId} onClick={linkBank}>
            {cfg.has_requisition ? 'Link a different bank' : 'Link bank'}
          </Button>
        </Cluster>
      </Stack>

      <ConnectorAccountsList provider="gocardless" providerLabel="GoCardless" />
    </Stack>
  )
}
