import { useMemo, useState } from 'react'
import {
  Button,
  Cluster,
  FormField,
  PasswordField,
  Select,
  Stack,
  Text,
  TextArea,
  TextField,
} from '../../ui'
import { useConnectorSave } from './formSave'
import { RestEndpointsEditor } from './RestEndpointsEditor'
import { applySpec, draftProblem, readRestSpec, saveRestApi } from './restApi'
import type { RestApi, RestApiDraft, RestAuthType, RestHeaderDraft } from './restApi'

// The form for ONE REST API — a new one, or one already saved. Two ways to fill it, freely mixed: read an OpenAPI / Swagger
// document (optional — it pre-fills the name, address, sign-in type and endpoints) or type
// everything by hand. The key itself is never in a document and is always typed here.
// Save lives in the modal footer — see ./formSave.

const AUTH_OPTIONS: { value: RestAuthType; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'bearer', label: 'Bearer token' },
  { value: 'header', label: 'API key in a header' },
  { value: 'query', label: 'API key in the URL' },
  { value: 'basic', label: 'Username and password' },
]

const VALUE_LABEL: Record<RestAuthType, string> = {
  none: '',
  bearer: 'Token',
  header: 'API key',
  query: 'API key',
  basic: 'Password',
}

interface ImportBoxProps {
  onRead: (source: string) => Promise<string>
}

function ImportBox({ onRead }: ImportBoxProps) {
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'faint' | 'bad'; text: string } | null>(null)

  async function read() {
    setBusy(true)
    setMessage(null)
    try {
      setMessage({ tone: 'faint', text: await onRead(source) })
      setSource('')
    } catch (e) {
      setMessage({ tone: 'bad', text: e instanceof Error ? e.message : 'Could not read it.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormField
      label="OpenAPI document (optional)"
      htmlFor="rest-spec"
      info="If the API publishes an OpenAPI or Swagger document, reading it fills in the address, the sign-in type and the endpoints for you. You can change anything afterwards."
    >
      <Stack gap="--space-2">
        <TextArea
          id="rest-spec"
          mono
          rows={2}
          placeholder="https://api.example.com/openapi.json — or paste the document"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
        <Cluster>
          <Button size="sm" loading={busy} disabled={!source.trim()} onClick={read}>
            Read document
          </Button>
          {message && (
            <Text size="xs" tone={message.tone}>
              {message.text}
            </Text>
          )}
        </Cluster>
      </Stack>
    </FormField>
  )
}

interface HeadersProps {
  headers: RestHeaderDraft[]
  onChange: (next: RestHeaderDraft[]) => void
}

function Headers({ headers, onChange }: HeadersProps) {
  const set = (i: number, patch: Partial<RestHeaderDraft>) =>
    onChange(headers.map((h, j) => (j === i ? { ...h, ...patch } : h)))
  return (
    <FormField
      label="Extra headers (optional)"
      info="Sent with every request — an API version header, for example. Lucius cannot see or change them."
    >
      <Stack gap="--space-2">
        {headers.map((header, i) => (
          <div key={i} className="rest-header-row">
            <TextField
              aria-label="Header name"
              placeholder="X-Api-Version"
              value={header.name}
              disabled={header.saved}
              onChange={(e) => set(i, { name: e.target.value })}
            />
            <PasswordField
              aria-label="Header value"
              placeholder={header.saved ? '•••••••• (saved)' : 'value'}
              value={header.value}
              onChange={(e) => set(i, { value: e.target.value })}
            />
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Remove header ${header.name}`}
              onClick={() => onChange(headers.filter((_, j) => j !== i))}
            >
              Remove
            </Button>
          </div>
        ))}
        <div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onChange([...headers, { name: '', value: '', saved: false }])}
          >
            Add a header
          </Button>
        </div>
      </Stack>
    </FormField>
  )
}

export interface RestApiEditorProps {
  initial: RestApiDraft
  /** Called with the API as the server now holds it — a new one, or this one after an edit. */
  onSaved: (api: RestApi) => void
}

export function RestApiEditor({ initial, onSaved }: RestApiEditorProps) {
  const [draft, setDraft] = useState<RestApiDraft>(initial)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const baseline = useMemo(() => initial, [initial])
  const problem = draftProblem(draft)

  const set = <K extends keyof RestApiDraft>(key: K, value: RestApiDraft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }))

  async function save(): Promise<boolean> {
    setSaving(true)
    setSaveErr(null)
    try {
      onSaved(await saveRestApi(draft))
      return true
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : 'Could not save the API.')
      return false
    } finally {
      setSaving(false)
    }
  }

  useConnectorSave({
    ready: problem === null,
    values: draft,
    baseline,
    saving,
    error: saveErr,
    save,
  })

  async function readSpec(source: string): Promise<string> {
    const spec = await readRestSpec(source)
    setDraft((d) => applySpec(d, spec))
    const count = spec.endpoints.length
    return [`Read ${count} endpoint${count === 1 ? '' : 's'}.`, spec.note].filter(Boolean).join(' ')
  }

  const auth = draft.auth_type
  return (
    <Stack gap="--space-4">
      {!draft.id && (
        <Text size="sm" tone="faint">
          Add an API by its address and key. Lucius can then request data from it and record what it
          finds. Requests only ever go to the address you save, and the key is added by the app and
          never shown to Lucius. Each API you add is its own connector.
        </Text>
      )}

      <ImportBox onRead={readSpec} />

      <FormField label="Name" htmlFor="rest-name" required>
        <TextField
          id="rest-name"
          placeholder="My bank's API"
          value={draft.name}
          onChange={(e) => set('name', e.target.value)}
        />
      </FormField>

      <FormField
        label="Base URL"
        htmlFor="rest-base"
        required
        info="Every request goes to an address that starts with this. Lucius chooses only what comes after it."
      >
        <TextField
          id="rest-base"
          placeholder="https://api.example.com/v1"
          value={draft.base_url}
          onChange={(e) => set('base_url', e.target.value)}
        />
      </FormField>

      <FormField label="Authentication" htmlFor="rest-auth">
        <Select
          id="rest-auth"
          options={AUTH_OPTIONS}
          value={auth}
          onChange={(e) =>
            setDraft((d) => ({
              ...d,
              auth_type: e.target.value as RestAuthType,
              auth_saved: e.target.value === initial.auth_type && initial.auth_saved,
            }))
          }
        />
      </FormField>

      {(auth === 'header' || auth === 'query') && (
        <FormField
          label={auth === 'header' ? 'Header name' : 'Parameter name'}
          htmlFor="rest-auth-name"
          required
        >
          <TextField
            id="rest-auth-name"
            placeholder={auth === 'header' ? 'X-API-Key' : 'api_key'}
            value={draft.auth_name}
            onChange={(e) => set('auth_name', e.target.value)}
          />
        </FormField>
      )}

      {auth === 'basic' && (
        <FormField label="Username" htmlFor="rest-auth-user" required>
          <TextField
            id="rest-auth-user"
            autoComplete="off"
            value={draft.auth_username}
            onChange={(e) => set('auth_username', e.target.value)}
          />
        </FormField>
      )}

      {auth !== 'none' && (
        <FormField
          label={VALUE_LABEL[auth]}
          htmlFor="rest-auth-value"
          required
          help={
            draft.auth_saved
              ? 'Saved. Leave blank to keep it, or type a new one to replace it.'
              : 'Added to each request by the app. Lucius never sees it.'
          }
        >
          <PasswordField
            id="rest-auth-value"
            autoComplete="off"
            placeholder={draft.auth_saved ? '••••••••' : ''}
            value={draft.auth_value}
            onChange={(e) => set('auth_value', e.target.value)}
          />
        </FormField>
      )}

      <Headers headers={draft.headers} onChange={(next) => set('headers', next)} />

      <FormField
        label="Endpoints"
        info="Each endpoint becomes a tool Lucius can call, with its own permission level. Lucius can also request any other path under the base URL unless you block the connector's two general tools."
      >
        <RestEndpointsEditor
          endpoints={draft.endpoints}
          onChange={(next) => set('endpoints', next)}
        />
      </FormField>

      <FormField
        label="Notes for Lucius (optional)"
        htmlFor="rest-notes"
        info="Anything that helps Lucius use this API: how paging works, the date format, which account a feed belongs to."
      >
        <TextArea
          id="rest-notes"
          rows={3}
          placeholder="Transactions are paged with ?page=. Amounts are in cents; negative means money out."
          value={draft.notes}
          onChange={(e) => set('notes', e.target.value)}
        />
      </FormField>

      {(saveErr || problem) && (
        <Text size="xs" tone={saveErr ? 'bad' : 'faint'}>
          {saveErr || problem}
        </Text>
      )}
    </Stack>
  )
}
