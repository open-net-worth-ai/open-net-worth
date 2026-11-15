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
  Badge,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'

// Web Search connector config for ONE provider over /api/search/config (the native web-search
// providers — the Web family). One card per provider; each has its own enable state + API key
// (the GET only echoes `has_key`; a blank field leaves the saved one unchanged, and the saved
// key is read back through the eye — GET /api/connectors/web_search:<id>/secret/api_key) + any
// non-secret extra config (e.g. Google PSE's `cx`).
//
// THE SETTING IS "DEFAULT PROVIDER", NOT "USE FOR LUCIUS". It used to be labelled "Use for
// Lucius" / "Active for Lucius", which was FALSE: since the per-provider tools shipped,
// `<id>_search` / `<id>_fetch_page` resolve their own provider directly and never consult
// the active selection, so a non-default provider is perfectly usable by Lucius. What the
// selection actually picks is the fallback for searches that name NO provider — the generic
// `web_search`/`fetch_page` tools, and the background jobs that have no way to name one
// (`monitoring/collect.py`'s news scans, `monitoring/asset_brief.py`'s briefs). The background
// jobs are the bigger consumer and went unmentioned entirely.
//
// COPY DISCIPLINE: this section deliberately does NOT describe what web search is, and does not
// explain where the connector's own controls are. The detail page's identity line directly above
// already carries the one-line description, and the enable control is its Connect/Disconnect
// button — visible, and not on a "card" with a "toggle at the top" any more. Restating both here
// is what made the DuckDuckGo modal say "no API key" three times across four sentences.

interface ExtraField {
  key: string
  label: string
  hint: string
  value: string
}

interface ProviderEntry {
  id: string
  label: string
  hint: string
  docs_url: string
  needs_api_key: boolean
  // Key-free, but accepts an optional key (You.com: a key raises the daily limit).
  optional_api_key?: boolean
  native_fetch: boolean
  extra_fields: ExtraField[]
  enabled: boolean
  has_key: boolean
  // A key is actually stored (`has_key` is always true for a key-free provider).
  key_saved?: boolean
  is_active: boolean
}

interface SearchStatus {
  default_provider: string | null
  providers: ProviderEntry[]
}

/** The (non-secret) extra fields' saved values — the seed for `load()` AND the baseline the
 *  footer's Save compares against, so the two can never drift apart. */
function extraFieldsOf(p: ProviderEntry | undefined): Record<string, string> {
  return Object.fromEntries((p?.extra_fields ?? []).map((f) => [f.key, f.value]))
}

export function WebSearchSection({ providerId }: { providerId: string }) {
  const [cfg, setCfg] = useState<SearchStatus | null>(null)
  const [error, setError] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [extra, setExtra] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [settingDefault, setSettingDefault] = useState(false)
  const [madeDefault, setMadeDefault] = useState(false)

  function load() {
    setError(false)
    authFetch('/api/search/config')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: SearchStatus) => {
        setCfg(c)
        setApiKey('')
        const p = c.providers.find((x) => x.id === providerId)
        // Seed the (non-secret) extra fields with their saved values.
        setExtra(extraFieldsOf(p))
      })
      .catch(() => setError(true))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [providerId])

  const provider = cfg?.providers.find((p) => p.id === providerId)

  async function save(): Promise<boolean> {
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = {}
      if (apiKey.trim()) body.api_key = apiKey.trim()
      if (provider && provider.extra_fields.length) body.extra = extra
      const r = await authFetch(`/api/search/config/${providerId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the web-search configuration.')
        return false
      }
      setApiKey('')
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const apiKeyReveal = useSecretReveal({
    saved: provider?.optional_api_key ? !!provider.key_saved : !!provider?.has_key,
    value: apiKey,
    onChange: setApiKey,
    reveal: useCallback(
      () => revealConnectorSecret(`web_search:${providerId}`, 'api_key'),
      [providerId],
    ),
  })

  // A key-free provider with no extra fields (DuckDuckGo) has nothing to save — its inline
  // Save button never rendered even before the footer move. `ready: false` keeps the footer's
  // Save permanently disabled rather than offering a write with nothing to write; leaving is
  // the `‹ <name>` back control, as it is on every other form.
  const savable =
    !!provider &&
    (provider.needs_api_key || !!provider.optional_api_key || provider.extra_fields.length > 0)
  const apiKeyValid = !provider || !provider.needs_api_key || !!apiKey.trim() || !!provider.has_key
  const apiKeyTouched = useTouched()
  useConnectorSave({
    ready: savable && apiKeyValid,
    values: { apiKey: unrevealed(apiKey, apiKeyReveal), extra },
    baseline: { apiKey: '', extra: extraFieldsOf(provider) },
    saving,
    error: saveErr,
    save,
  })

  async function makeDefault() {
    setSettingDefault(true)
    try {
      const r = await authFetch('/api/search/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_provider: providerId }),
      })
      if (r.ok) {
        setMadeDefault(true)
        setTimeout(() => setMadeDefault(false), 2000)
        load()
      }
    } finally {
      setSettingDefault(false)
    }
  }

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load this provider&apos;s settings.
      </Text>
    )
  }
  if (!cfg || !provider) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }

  const needsKey = provider.needs_api_key
  const optionalKey = !needsKey && !!provider.optional_api_key

  return (
    <Stack gap="--space-4">
      {/* Rendered ONLY for a keyed provider: the hint says what is distinctive about it, and a
          key-free provider's distinctive fact is already the badge below. */}
      {(needsKey || optionalKey) && (
        <Text size="sm" tone="faint">
          {provider.hint}{' '}
          {provider.docs_url && (
            <a href={provider.docs_url} target="_blank" rel="noreferrer">
              Get a key →
            </a>
          )}
        </Text>
      )}

      {/* NO "Default provider" badge here. The detail page's identity line, two rows above this
          form, already renders the live `Default` chip (`adapter.toEntry` derives it from
          `is_active`) — a badge here would be the same fact a second time, which is how the old
          copy ended up asserting it three ways at once. Being the default is shown ABOVE; the
          only thing this form owns is the ACTION that changes it.

          Also no "API key saved"/"No API key" badge (owner review, 2026-08-21): the connector's
          OWN Connected/Not connected status two rows above already says whether it's configured
          — a keyed provider can't be Connected without a saved key, so restating it here was the
          exact same duplicate-status-badge pattern already fixed for the sync connectors. "No
          key needed" stays: unlike the other two, it states a fact Connected/Not connected
          doesn't — that THIS provider has no key concept at all. */}
      {!needsKey && (
        <Cluster gap="--space-3" align="center" wrap>
          <Badge tone="pos">No key needed</Badge>
        </Cluster>
      )}

      {(needsKey || optionalKey) && (
        <FormField
          label={optionalKey ? 'API key (optional)' : 'API key'}
          htmlFor={`conn-search-key-${providerId}`}
          required={needsKey}
          help={apiKeyReveal.caption || undefined}
          error={
            apiKeyReveal.error ||
            (apiKeyTouched.touched && !apiKeyValid ? 'An API key is required.' : undefined)
          }
        >
          <PasswordField
            id={`conn-search-key-${providerId}`}
            value={apiKey}
            placeholder={
              (optionalKey ? provider.key_saved : provider.has_key) ? '••••••••' : 'paste the API key'
            }
            onChange={(e) => setApiKey(e.target.value)}
            onBlur={apiKeyTouched.onBlur}
            {...apiKeyReveal.fieldProps}
          />
        </FormField>
      )}

      {provider.extra_fields.map((f) => (
        <FormField
          key={f.key}
          label={f.label}
          htmlFor={`conn-search-${providerId}-${f.key}`}
          help={f.hint}
        >
          <TextField
            id={`conn-search-${providerId}-${f.key}`}
            value={extra[f.key] ?? ''}
            onChange={(e) => setExtra((prev) => ({ ...prev, [f.key]: e.target.value }))}
          />
        </FormField>
      ))}

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      {/* "Make default" lives at the BOTTOM, after every field — owner review, 2026-08-21: it
          reads as the form's closing action (the last thing you'd do once the key is in), not a
          status chip competing with "No key needed" for the same row and the same visual
          language.

          The Cluster itself is NOT gated on `!provider.is_active` — only the Button is. Clicking
          flips `is_active` via the reload this same click triggers, so gating the whole row on it
          would yank "Now the default." off screen in the same instant it was meant to confirm. */}
      {provider.has_key && (
        <Cluster gap="--space-3" align="center" wrap>
          {!provider.is_active && (
            <Button size="sm" variant="secondary" loading={settingDefault} onClick={makeDefault}>
              Make default
            </Button>
          )}
          {madeDefault && (
            <Text size="xs" tone="good">
              Now the default.
            </Text>
          )}
        </Cluster>
      )}

      {/* The one sentence that says what the setting DOES. Naming the background jobs is the
          substantive half — they are the bigger consumer of the default and the old copy never
          mentioned them, while claiming the setting decided what Lucius could use at all. */}
      <Text size="xs" tone="faint">
        Searches that don’t name a provider use the default — Lucius’s general web lookups, plus
        background news scans and asset briefs.
      </Text>
    </Stack>
  )
}
