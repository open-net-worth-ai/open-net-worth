import { describe, expect, it } from 'vitest'
import { applySpec, draftOf, draftProblem, emptyDraft } from './restApi'
import type { RestApi, RestSpecDraft } from './restApi'

const SAVED: RestApi = {
  id: 'acme_bank',
  connector_id: 'rest:acme_bank',
  name: 'Acme Bank',
  enabled: true,
  base_url: 'https://api.acme.test/v1',
  auth: { type: 'bearer', name: '', username: '', has_value: true },
  header_names: ['X-Api-Version'],
  endpoints: [{ method: 'GET', path: '/txns', description: 'Transactions', params: [] }],
  notes: 'Paged with ?page=.',
}

const SPEC: RestSpecDraft = {
  name: 'Acme Bank API',
  base_url: 'https://api.acme.test/v2',
  auth: { type: 'header', name: 'X-API-Key' },
  endpoints: [{ method: 'POST', path: '/transfers', description: 'Move money', params: [] }],
  note: '',
}

describe('the REST API draft', () => {
  it('opens a saved API with its credential and header values blank', () => {
    const draft = draftOf(SAVED)
    expect(draft.auth_value).toBe('')
    expect(draft.auth_saved).toBe(true)
    expect(draft.headers).toEqual([{ name: 'X-Api-Version', value: '', saved: true }])
    // Blank is "keep the saved one", so a saved API is valid to save untouched.
    expect(draftProblem(draft)).toBeNull()
  })

  it('says what a new API still needs, one thing at a time', () => {
    const draft = emptyDraft()
    expect(draftProblem(draft)).toMatch(/name/)
    draft.name = 'Acme'
    expect(draftProblem(draft)).toMatch(/base URL/)
    draft.base_url = 'https://api.acme.test'
    expect(draftProblem(draft)).toBeNull()
    draft.auth_type = 'header'
    expect(draftProblem(draft)).toMatch(/key, token or password/)
    draft.auth_value = 'k'
    expect(draftProblem(draft)).toMatch(/header name/)
    draft.auth_name = 'X-API-Key'
    expect(draftProblem(draft)).toBeNull()
    draft.headers = [{ name: 'X-V', value: '', saved: false }]
    expect(draftProblem(draft)).toMatch(/header needs a value/)
  })

  it('lays a document over an empty form', () => {
    const draft = applySpec(emptyDraft(), SPEC)
    expect(draft.name).toBe('Acme Bank API')
    expect(draft.base_url).toBe('https://api.acme.test/v2')
    expect(draft.auth_type).toBe('header')
    expect(draft.auth_name).toBe('X-API-Key')
    expect(draft.endpoints).toEqual(SPEC.endpoints)
  })

  it('keeps what the owner typed, and asks for a key again when the sign-in type changes', () => {
    const draft = applySpec({ ...draftOf(SAVED), notes: 'mine' }, SPEC)
    expect(draft.name).toBe('Acme Bank')
    expect(draft.base_url).toBe('https://api.acme.test/v1')
    expect(draft.notes).toBe('mine')
    expect(draft.auth_type).toBe('header')
    expect(draft.auth_saved).toBe(false)
    expect(draftProblem(draft)).toMatch(/key, token or password/)
  })

  it('leaves the sign-in alone when the document names none it can use', () => {
    const draft = applySpec(draftOf(SAVED), { ...SPEC, auth: { type: 'none', name: '' } })
    expect(draft.auth_type).toBe('bearer')
    expect(draft.auth_saved).toBe(true)
  })
})
