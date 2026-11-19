import { describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
import {
  applySpec,
  cleanRestId,
  draftOf,
  draftProblem,
  emptyDraft,
  restIdFromName,
  restIdOf,
  restToolLabel,
  saveRestApi,
} from './restApi'
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

describe('the id shown under the name', () => {
  // The same cases as tests/test_rest_connector.py: the server's rule decides, this mirrors it.
  it.each([
    ['Acme Bank', 'acme_bank'],
    ['  My bank’s API!  ', 'my_bank_s_api'],
    // Too long: whole words go from the end, never half of one.
    ['Swagger Petstore - OpenAPI 3.0', 'swagger_petstore'],
    ['A very long name that ends_ on a break', 'a_very_long_name'],
    ['abcdefghijklmnopqrs tuv', 'abcdefghijklmnopqrs'],
    ['Exactly twenty chars and more', 'exactly_twenty_chars'],
    ['Acme Bank International', 'acme_bank'],
    ['Acme Internationalbankingcorporation', 'acme'],
    // Dropping the long word would leave almost nothing: only then is a word cut.
    ['Supercalifragilisticexpialidocious', 'supercalifragilistic'],
    ['A supercalifragilisticexpialidocious', 'a_supercalifragilist'],
    ['!!!', ''],
  ])('%s -> %s', (name, id) => {
    expect(restIdFromName(name)).toBe(id)
  })

  it('refuses a new name with no letter or digit, which could have no id', () => {
    const draft = { ...emptyDraft(), name: '!!!', base_url: 'https://api.acme.test' }
    expect(draftProblem(draft)).toBe('Use letters or numbers in the name.')
  })
})

describe('an id the owner types', () => {
  it('is kept to what an id may hold, as they type', () => {
    expect(cleanRestId('Acme Bank')).toBe('acme_bank')
    expect(cleanRestId('__acme--bank')).toBe('acme_bank')
    // The next word is coming: a trailing separator stays in the field…
    expect(cleanRestId('acme ')).toBe('acme_')
    expect(cleanRestId('a'.repeat(30))).toHaveLength(20)
  })

  it('replaces the name’s id, and goes back to it when cleared to automatic', () => {
    const named = { ...emptyDraft(), name: 'Acme Bank International' }
    expect(restIdOf(named)).toBe('acme_bank')
    // …and is dropped from the id itself.
    expect(restIdOf({ ...named, custom_id: 'acme_' })).toBe('acme')
    expect(restIdOf({ ...named, custom_id: null })).toBe('acme_bank')
  })

  it('must not be empty, and never applies to a saved API', () => {
    const draft = { ...emptyDraft(), name: 'Acme', base_url: 'https://api.acme.test' }
    expect(draftProblem({ ...draft, custom_id: '' })).toBe('Enter an ID.')
    expect(draftProblem({ ...draft, custom_id: 'acme' })).toBeNull()
    expect(restIdOf({ ...draftOf(SAVED), custom_id: 'other' })).toBe('acme_bank')
  })
})

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

describe('what a save sends', () => {
  it('carries a permission the owner picked, never the level the server reported', async () => {
    authFetch.mockResolvedValue({ ok: true, json: async () => SAVED })
    await saveRestApi({
      ...draftOf(SAVED),
      endpoints: [
        { method: 'GET', path: '/txns', description: '', params: [], level: 'always_allow' },
        { method: 'POST', path: '/pay', description: '', params: [], permission: 'blocked' },
      ],
    })
    const sent = JSON.parse(authFetch.mock.calls[0]![1].body as string)
    expect(sent.endpoints).toEqual([
      { method: 'GET', path: '/txns', description: '', params: [] },
      { method: 'POST', path: '/pay', description: '', params: [], permission: 'blocked' },
    ])
  })
})

describe('a REST tool on its own API’s page', () => {
  it('is listed by its own name, without the part every tool of that API shares', () => {
    expect(restToolLabel('rest__frankfurter__get_latest')).toBe('get_latest')
    expect(restToolLabel('rest__acme_bank__list_endpoints')).toBe('list_endpoints')
    // Not a REST tool: left exactly as it is.
    expect(restToolLabel('coinbase_orders_list')).toBe('coinbase_orders_list')
    expect(restToolLabel('mcp__acme__rest__x')).toBe('mcp__acme__rest__x')
  })
})

describe('importing a document fills Instructions for Lucius', () => {
  const spec: RestSpecDraft = {
    name: 'Pets',
    base_url: 'https://pets.test',
    auth: { type: 'none', name: '' },
    endpoints: [],
    note: '',
    instructions: 'Amounts are in cents.',
  }

  it('from its description, when the field is empty', () => {
    expect(applySpec(emptyDraft(), spec).notes).toBe('Amounts are in cents.')
  })

  it('never over what the owner already wrote', () => {
    expect(applySpec({ ...emptyDraft(), notes: 'Mine.' }, spec).notes).toBe('Mine.')
  })
})
