import { useMemo, useState } from 'react'
import { Trash2 } from '../../../ui/icons'
import { useConnectorSave } from './formSave'
import { Icons } from './icons'
import { Btn } from './primitives'
import { RestEndpointsEditor } from './RestEndpointsEditor'
import { RestField, SecretInput } from './restFormParts'
import { IdField, IdHelp } from './RestIdField'
import { applySpec, draftProblem, readRestSpec, restIdFromName, saveRestApi } from './restApi'
import type { RestApi, RestApiDraft, RestAuthType, RestHeaderDraft } from './restApi'

// The form for ONE REST API — a new one, or one already saved. Two ways to fill it, freely
// mixed: import an OpenAPI / Swagger document (optional — it pre-fills the name, address,
// sign-in type and endpoints) or type everything by hand. The key itself is never in a
// document and is always typed here. Save lives in the modal footer — see ./formSave.
//
// Drawn in the connectors screen's own form language (`rd-*`, see ./restFormParts), the one
// the MCP server form uses. `data-ctx-secret` on the form keeps every field of it out of the
// screen snapshot Lucius can read: the base URL is a credential, and so may a path or a note be.

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
  const [message, setMessage] = useState<{ bad: boolean; text: string } | null>(null)

  async function read() {
    setBusy(true)
    setMessage(null)
    try {
      setMessage({ bad: false, text: await onRead(source) })
      setSource('')
    } catch (e) {
      setMessage({ bad: true, text: e instanceof Error ? e.message : 'Could not import it.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <RestField
      label="OpenAPI document (optional)"
      htmlFor="rest-spec"
      help="If the API publishes an OpenAPI or Swagger document, importing it fills in the address, the sign-in type and the endpoints. You can change anything afterwards."
    >
      <textarea
        id="rest-spec"
        className="rd-textarea cn-pastebox"
        rows={2}
        placeholder="https://api.example.com/openapi.json — or paste the document"
        value={source}
        onChange={(e) => setSource(e.target.value)}
      />
      <div className="rest-inline">
        <Btn disabled={busy || !source.trim()} onClick={read}>
          {busy ? 'Importing…' : 'Import'}
        </Btn>
        {message && (
          <span className={'rd-help' + (message.bad ? ' rd-help--bad' : '')} role="status">
            {message.text}
          </span>
        )}
      </div>
    </RestField>
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
    <RestField
      label="Extra headers (optional)"
      help="Sent with every request — an API version header, for example. Lucius cannot see or change them."
    >
      {headers.map((header, i) => (
        <div key={i} className="rest-header-row">
          <input
            className="rd-input"
            aria-label="Header name"
            placeholder="X-Api-Version"
            value={header.name}
            disabled={header.saved}
            onChange={(e) => set(i, { name: e.target.value })}
          />
          <SecretInput
            aria-label="Header value"
            placeholder={header.saved ? '•••••••• (saved)' : 'value'}
            value={header.value}
            onChange={(value) => set(i, { value })}
          />
          <button
            type="button"
            className="rest-ep-icon rest-ep-icon--remove"
            title="Remove"
            aria-label={`Remove header ${header.name}`}
            onClick={() => onChange(headers.filter((_, j) => j !== i))}
          >
            <Trash2 size={15} aria-hidden />
          </button>
        </div>
      ))}
      <div>
        <Btn onClick={() => onChange([...headers, { name: '', value: '', saved: false }])}>
          <Icons.plus /> Add a header
        </Btn>
      </div>
    </RestField>
  )
}

export interface RestApiEditorProps {
  initial: RestApiDraft
  /** Called with the API as the server now holds it — a new one, or this one after an edit. */
  onSaved: (api: RestApi) => void
  /** Called with the reason when a save fails, for the screen's toast: Save is in the dialog's
   *  footer and this form's own message is its last line, a screen below on a long form — the
   *  owner pressed Save and saw nothing happen (2026-11-11). */
  onSaveError?: (message: string) => void
}

export function RestApiEditor({ initial, onSaved, onSaveError }: RestApiEditorProps) {
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
      const message = e instanceof Error ? e.message : 'Could not save the API.'
      setSaveErr(message)
      onSaveError?.(message)
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
    return [`Imported ${count} endpoint${count === 1 ? '' : 's'}.`, spec.note]
      .filter(Boolean)
      .join(' ')
  }

  const auth = draft.auth_type
  const customId = !draft.id && draft.custom_id !== null
  return (
    <div className="rd-formgrid rest-form" data-ctx-secret="">
      {!draft.id && (
        <span className="rd-help full">
          Add an API by its address and key. Lucius can then request data from it and record what it
          finds. Requests only ever go to the address you save, and the key is added by the app and
          never shown to Lucius. Each API you add is its own connector.
        </span>
      )}

      <ImportBox onRead={readSpec} />

      <RestField
        label="Name"
        htmlFor="rest-name"
        required
        help={
          customId ? undefined : (
            <IdHelp
              draft={draft}
              onCustomize={() => set('custom_id', restIdFromName(draft.name))}
            />
          )
        }
      >
        <input
          id="rest-name"
          className="rd-input"
          placeholder="My bank's API"
          value={draft.name}
          onChange={(e) => set('name', e.target.value)}
        />
      </RestField>

      {customId && (
        <IdField
          value={draft.custom_id ?? ''}
          onChange={(next) => set('custom_id', next)}
          onUseName={() => set('custom_id', null)}
        />
      )}

      <RestField
        label="Base URL"
        htmlFor="rest-base"
        required
        help="Every request goes to an address that starts with this. Lucius chooses only what comes after it."
      >
        <input
          id="rest-base"
          className="rd-input"
          placeholder="https://api.example.com/v1"
          value={draft.base_url}
          onChange={(e) => set('base_url', e.target.value)}
        />
      </RestField>

      <RestField label="Authentication" htmlFor="rest-auth">
        <select
          id="rest-auth"
          className="rd-select"
          value={auth}
          onChange={(e) =>
            setDraft((d) => ({
              ...d,
              auth_type: e.target.value as RestAuthType,
              auth_saved: e.target.value === initial.auth_type && initial.auth_saved,
            }))
          }
        >
          {AUTH_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </RestField>

      {(auth === 'header' || auth === 'query') && (
        <RestField
          label={auth === 'header' ? 'Header name' : 'Parameter name'}
          htmlFor="rest-auth-name"
          required
        >
          <input
            id="rest-auth-name"
            className="rd-input"
            placeholder={auth === 'header' ? 'X-API-Key' : 'api_key'}
            value={draft.auth_name}
            onChange={(e) => set('auth_name', e.target.value)}
          />
        </RestField>
      )}

      {auth === 'basic' && (
        <RestField label="Username" htmlFor="rest-auth-user" required>
          <input
            id="rest-auth-user"
            className="rd-input"
            autoComplete="off"
            value={draft.auth_username}
            onChange={(e) => set('auth_username', e.target.value)}
          />
        </RestField>
      )}

      {auth !== 'none' && (
        <RestField
          label={VALUE_LABEL[auth]}
          htmlFor="rest-auth-value"
          required
          help={
            draft.auth_saved
              ? 'Saved. Leave blank to keep it, or type a new one to replace it.'
              : 'Added to each request by the app. Lucius never sees it.'
          }
        >
          <SecretInput
            id="rest-auth-value"
            placeholder={draft.auth_saved ? '••••••••' : ''}
            value={draft.auth_value}
            onChange={(value) => set('auth_value', value)}
          />
        </RestField>
      )}

      <Headers headers={draft.headers} onChange={(next) => set('headers', next)} />

      <RestField
        label="Endpoints"
        help="Each endpoint becomes a tool Lucius can call, with its own permission. Lucius can also request any other path under the base URL unless you block the connector's two general tools."
      >
        <RestEndpointsEditor
          endpoints={draft.endpoints}
          onChange={(next) => set('endpoints', next)}
        />
      </RestField>

      <RestField
        label="Notes for Lucius (optional)"
        htmlFor="rest-notes"
        help="Anything that helps Lucius use this API: how paging works, the date format, which account a feed belongs to."
      >
        <textarea
          id="rest-notes"
          className="rd-textarea"
          rows={3}
          placeholder="Transactions are paged with ?page=. Amounts are in cents; negative means money out."
          value={draft.notes}
          onChange={(e) => set('notes', e.target.value)}
        />
      </RestField>

      {(saveErr || problem) && (
        <span
          className={'rd-help full' + (saveErr ? ' rd-help--bad' : '')}
          role={saveErr ? 'alert' : undefined}
        >
          {saveErr || problem}
        </span>
      )}
    </div>
  )
}
