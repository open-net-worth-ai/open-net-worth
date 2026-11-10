// The REST connector's settings plane (/api/rest-connector/*): the owner's APIs.
//
// Each API is its own connector (`rest:<slug>`): this plane creates one and edits one, and
// Pause / Resume / Remove are the connector's own, like any other. The server never returns a
// credential VALUE — an API's key comes back as `auth.has_value`, a fixed header as its name
// only — so the editor's draft starts with those blank, and a blank on save means "keep the
// saved one".

/** The catalog's "REST API" entry: never set up itself, it is where a new API is added. */
export const REST_LAUNCHER_ID = 'rest'
const REST_PREFIX = 'rest:'

/** A REST tool's name without the part every tool of the same API shares: `rest__acme__get_txns`
 *  is `get_txns` on Acme's own page, as an MCP server's tools are listed by their own names. */
export function restToolLabel(tool: string): string {
  const parts = tool.split('__')
  return parts[0] === 'rest' && parts.length >= 3 ? parts.slice(2).join('__') : tool
}

/** The API id behind a `rest:<slug>` connector, or null for the launcher. */
export function restSlugOf(connectorId: string): string | null {
  return connectorId.startsWith(REST_PREFIX) ? connectorId.slice(REST_PREFIX.length) : null
}

import { authFetch } from '../../../api/client'
import { DEFAULT_PERMISSION, type PermissionLevel } from './model'

const MAX_SLUG = 20
const MIN_WORD_CUT = 4

/** The id a new API will get from its name, shown under the Name field as it is typed. Twin of
 *  the server's `rest_tools.slug_from_name`, which is the one that decides: lowercase, every
 *  other run of characters one `_`, at most 20, cut between words. Empty when the name has no
 *  letter or digit. */
export function restIdFromName(name: string): string {
  const trim = (s: string) => s.replace(/^_+|_+$/g, '')
  const slug = trim(name.toLowerCase().replace(/[^a-z0-9]+/g, '_'))
  if (slug.length <= MAX_SLUG) return slug
  // Too long: whole words go from the end, never half of one, unless that would leave
  // almost nothing.
  const cut = slug.slice(0, MAX_SLUG + 1).lastIndexOf('_')
  return cut >= MIN_WORD_CUT ? slug.slice(0, cut) : trim(slug.slice(0, MAX_SLUG))
}

export const REST_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const
export type RestMethod = (typeof REST_METHODS)[number]
export type RestAuthType = 'none' | 'bearer' | 'basic' | 'header' | 'query'
export type RestParamPlace = 'path' | 'query' | 'body'

export interface RestParam {
  name: string
  in: RestParamPlace
  required: boolean
  description: string
}

export interface RestEndpoint {
  /** The endpoint's stable id, which names its tool (`rest__<api>__<tool>`). Assigned by the
   *  server on first save and carried back unchanged, so editing an endpoint never renames
   *  the tool; absent on one the user has just added. */
  tool?: string
  method: RestMethod
  path: string
  description: string
  params: RestParam[]
  /** The level this endpoint's tool is at now, as the server reports it for a saved one. */
  level?: PermissionLevel | null
  /** The level the owner picked on this row and has not saved yet. Only a picked level is
   *  sent: an endpoint nobody touched keeps its default, whatever that is. */
  permission?: PermissionLevel
}

/** Whether an endpoint only reads (a GET) or writes (anything else): the two groups of the
 *  endpoint list, and what its tool is to the permission engine. */
export function isReadEndpoint(endpoint: Pick<RestEndpoint, 'method'>): boolean {
  return endpoint.method === 'GET'
}

/** The level a row shows: the owner's unsaved pick, else what the tool is at, else the default
 *  a new tool gets — a read runs unasked under the new permissions, everything else asks. */
export function shownLevel(endpoint: RestEndpoint, v2: boolean): PermissionLevel {
  return (
    endpoint.permission ??
    endpoint.level ??
    (v2 && isReadEndpoint(endpoint) ? 'always_allow' : DEFAULT_PERMISSION)
  )
}

/** An edit to one endpoint. Changing its method or path makes it a different request, so an
 *  Always allow it had does not carry over: the row goes back to asking, where the owner can
 *  see it and pick again. The server withdraws a surviving grant the same way. */
export function editedEndpoint(prev: RestEndpoint, next: RestEndpoint, v2: boolean): RestEndpoint {
  const moved = prev.method !== next.method || prev.path !== next.path
  if (!moved || shownLevel(prev, v2) !== 'always_allow') return next
  return { ...next, permission: DEFAULT_PERMISSION }
}

export interface RestApi {
  id: string
  /** The connector this API is: `rest:<id>`. */
  connector_id: string
  name: string
  enabled: boolean
  base_url: string
  auth: { type: RestAuthType; name: string; username: string; has_value: boolean }
  header_names: string[]
  endpoints: RestEndpoint[]
  notes: string
}

export interface RestHeaderDraft {
  name: string
  value: string
  /** A header the server already holds: its value is never sent back, blank keeps it. */
  saved: boolean
}

/** The editor's working copy of one API. `id` is null for one not saved yet. */
export interface RestApiDraft {
  id: string | null
  /** The id the owner typed for a NEW API in place of the name's; null takes the name's. */
  custom_id: string | null
  name: string
  base_url: string
  auth_type: RestAuthType
  auth_name: string
  auth_username: string
  auth_value: string
  auth_saved: boolean
  headers: RestHeaderDraft[]
  endpoints: RestEndpoint[]
  notes: string
}

/** What the owner types into the ID field, kept to what an id may hold as they type: lowercase
 *  letters, digits and single underscores, at most 20. A trailing `_` stays while typing (the
 *  next word is coming) and is dropped by `restIdOf`. */
export function cleanRestId(typed: string): string {
  return typed
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+/, '')
    .slice(0, MAX_SLUG)
}

/** The id this draft has (saved) or will get on its first save. */
export function restIdOf(draft: RestApiDraft): string {
  if (draft.id) return draft.id
  if (draft.custom_id === null) return restIdFromName(draft.name)
  return draft.custom_id.replace(/_+$/, '')
}

/** What reading an OpenAPI / Swagger document gives back: a pre-fill, never a save. */
export interface RestSpecDraft {
  name: string
  base_url: string
  auth: { type: RestAuthType; name: string }
  endpoints: RestEndpoint[]
  note: string
}

export function emptyDraft(): RestApiDraft {
  return {
    id: null,
    custom_id: null,
    name: '',
    base_url: '',
    auth_type: 'none',
    auth_name: '',
    auth_username: '',
    auth_value: '',
    auth_saved: false,
    headers: [],
    endpoints: [],
    notes: '',
  }
}

export function draftOf(api: RestApi): RestApiDraft {
  return {
    id: api.id,
    custom_id: null,
    name: api.name,
    base_url: api.base_url,
    auth_type: api.auth.type,
    auth_name: api.auth.name,
    auth_username: api.auth.username,
    auth_value: '',
    auth_saved: api.auth.has_value,
    headers: api.header_names.map((name) => ({ name, value: '', saved: true })),
    endpoints: api.endpoints,
    notes: api.notes,
  }
}

/** Lay a document's pre-fill over the draft. Only what the document knows is replaced: the
 *  key the owner typed, their headers and their notes stay. */
export function applySpec(draft: RestApiDraft, spec: RestSpecDraft): RestApiDraft {
  const authChanged = spec.auth.type !== 'none' && spec.auth.type !== draft.auth_type
  return {
    ...draft,
    name: draft.name.trim() || spec.name,
    base_url: draft.base_url.trim() || spec.base_url,
    auth_type: spec.auth.type === 'none' ? draft.auth_type : spec.auth.type,
    auth_name: spec.auth.type === 'none' ? draft.auth_name : spec.auth.name,
    auth_saved: authChanged ? false : draft.auth_saved,
    endpoints: spec.endpoints,
  }
}

/** Why the draft cannot be saved yet, or null when it can. */
export function draftProblem(draft: RestApiDraft): string | null {
  if (!draft.name.trim()) return 'Give the API a name.'
  if (!draft.id && !restIdOf(draft)) {
    return draft.custom_id === null ? 'Use letters or numbers in the name.' : 'Enter an ID.'
  }
  if (!/^https?:\/\/[^/\s]+/i.test(draft.base_url.trim())) {
    return 'Enter the base URL, starting with http:// or https://.'
  }
  const t = draft.auth_type
  if (t !== 'none' && !draft.auth_value.trim() && !draft.auth_saved) {
    return 'Enter the key, token or password.'
  }
  if ((t === 'header' || t === 'query') && !draft.auth_name.trim()) {
    return t === 'header' ? 'Enter the header name.' : 'Enter the parameter name.'
  }
  if (t === 'basic' && !draft.auth_username.trim()) return 'Enter the username.'
  if (draft.headers.some((h) => h.name.trim() && !h.value.trim() && !h.saved)) {
    return 'Every header needs a value.'
  }
  return null
}

/** An endpoint as sent: `level` is the server's own report, not something to send back. */
function toSend(endpoint: RestEndpoint): RestEndpoint {
  const sent = { ...endpoint }
  delete sent.level
  return sent
}

function bodyOf(draft: RestApiDraft): Record<string, unknown> {
  const headers: Record<string, string> = {}
  for (const h of draft.headers) {
    if (h.name.trim()) headers[h.name.trim()] = h.value.trim()
  }
  return {
    ...(draft.id === null && draft.custom_id !== null ? { id: restIdOf(draft) } : {}),
    name: draft.name.trim(),
    base_url: draft.base_url.trim(),
    auth: {
      type: draft.auth_type,
      name: draft.auth_name.trim(),
      username: draft.auth_username.trim(),
      value: draft.auth_value.trim(),
    },
    headers,
    endpoints: draft.endpoints.filter((e) => e.path.trim()).map(toSend),
    notes: draft.notes,
  }
}

/** The server's own reason, or the fallback with the status when the answer was not ours to
 *  read (a proxy in front of the app replaces some error pages with its own). */
async function detailOf(r: Response, fallback: string): Promise<string> {
  const d = await r.json().catch(() => ({}))
  return typeof d.detail === 'string' && d.detail ? d.detail : `${fallback} (error ${r.status})`
}

export async function getRestApi(slug: string): Promise<RestApi> {
  const r = await authFetch(`/api/rest-connector/apis/${encodeURIComponent(slug)}`)
  if (!r.ok) throw new Error('Could not load this API.')
  return r.json()
}

export async function saveRestApi(draft: RestApiDraft): Promise<RestApi> {
  const path = draft.id
    ? `/api/rest-connector/apis/${encodeURIComponent(draft.id)}`
    : '/api/rest-connector/apis'
  const r = await authFetch(path, {
    method: draft.id ? 'PUT' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bodyOf(draft)),
  })
  if (!r.ok) throw new Error(await detailOf(r, 'Could not save the API.'))
  return r.json()
}

/** Read an OpenAPI / Swagger document: pasted text when it looks like one, else its address. */
export async function readRestSpec(source: string): Promise<RestSpecDraft> {
  const text = source.trim()
  const isUrl = /^https?:\/\/\S+$/i.test(text)
  const r = await authFetch('/api/rest-connector/openapi', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(isUrl ? { url: text } : { text }),
  })
  if (!r.ok) throw new Error(await detailOf(r, 'Could not import that document.'))
  return r.json()
}
