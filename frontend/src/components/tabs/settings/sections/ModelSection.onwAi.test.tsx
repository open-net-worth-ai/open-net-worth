// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({ models: [], defaultModel: 'onw_ai:acme-flash', loading: false }),
}))

import { authFetch } from '../../../../api/client'
import { deleteCachedQueryData } from '../../../../state/queryCache'
import { ModelSection } from './ModelSection'
import { creditText, ONW_AI_STATUS_PATH } from './onwAiStatus'

const mockAuthFetch = vi.mocked(authFetch)
const CATALOG = [
  {
    key: 'onw_ai',
    label: 'Open Net Worth AI',
    transport: 'openai_compatible',
    base_url: 'https://inference.opennetworth.ai/v1',
    needs_api_key: true,
    editable_base_url: true,
    model_hints: ['acme-flash'],
  },
  {
    key: 'anthropic',
    label: 'Anthropic',
    transport: 'anthropic',
    base_url: '',
    needs_api_key: true,
    editable_base_url: false,
    model_hints: [],
  },
]
type Row = {
  has_api_key: boolean
  base_url: string
  model: string
  enabled: boolean
  key_status?: string
  managed?: boolean
}
let configured: Record<string, Row>
let status: unknown

function json(body: unknown, code = 200) {
  return new Response(JSON.stringify(body), {
    status: code,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  for (const key of [
    '/api/model-config/providers',
    '/api/model-config',
    '/api/preferred-models',
    ONW_AI_STATUS_PATH,
  ])
    deleteCachedQueryData(key)
  configured = {}
  status = { managed: false, credit: null }
  mockAuthFetch.mockImplementation(async (input) => {
    const url = String(input)
    if (url === '/api/model-config/providers')
      return json({
        catalog: CATALOG,
        configured_providers: configured,
        detected_clis: {},
        detected_cli_paths: {},
      })
    if (url === '/api/model-config')
      return json({
        default_model: 'onw_ai:acme-flash',
        provider: 'openai_compatible',
        model: 'acme-flash',
        base_url: '',
        task_models: {},
      })
    if (url === '/api/preferred-models') return json({ models: [] })
    if (url === ONW_AI_STATUS_PATH) return json(status)
    throw new Error(`Unexpected request: ${url}`)
  })
})

afterEach(() => cleanup())

const row = (name: string) => screen.getByText(name).closest('.rd-prov') as HTMLElement

it('a Private Cloud provider reads as managed, with its credit, and cannot be edited', async () => {
  configured = {
    onw_ai: {
      has_api_key: true,
      base_url: 'http://10.99.0.1:4000/v1',
      model: 'acme-flash',
      enabled: true,
      key_status: 'valid',
      managed: true,
    },
  }
  status = {
    managed: true,
    credit: { remaining_usd: 4.2, limit_usd: 10, resets_at: '2026-12-01T00:00:00+00:00' },
  }
  render(<ModelSection toast={vi.fn()} />)
  const card = await waitFor(() => row('Open Net Worth AI'))
  expect(within(card).getByText('Managed by Private Cloud')).toBeTruthy()
  expect(within(card).queryByText('http://10.99.0.1:4000/v1')).toBeNull()
  expect(await within(card).findByText('$4.20 of $10.00 left · resets Dec 1')).toBeTruthy()
  expect(within(card).queryByRole('button', { name: 'Edit' })).toBeNull()
})

it('shows no credit figure when none is known', async () => {
  configured = {
    onw_ai: {
      has_api_key: true,
      base_url: 'https://inference.opennetworth.ai/v1',
      model: 'acme-flash',
      enabled: true,
      key_status: 'valid',
    },
  }
  render(<ModelSection toast={vi.fn()} />)
  const card = await waitFor(() => row('Open Net Worth AI'))
  await waitFor(() =>
    expect(mockAuthFetch.mock.calls.some(([u]) => String(u) === ONW_AI_STATUS_PATH)).toBe(true),
  )
  expect(within(card).queryByText(/left/)).toBeNull()
  // A pasted key is the owner's own: it stays editable.
  expect(within(card).getByRole('button', { name: 'Edit' })).toBeTruthy()
})

it('adding it asks for a key with the public endpoint filled in', async () => {
  render(<ModelSection toast={vi.fn()} />)
  fireEvent.click(await screen.findByRole('button', { name: /add provider/i }))
  expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('onw_ai')
  expect(screen.getByDisplayValue('https://inference.opennetworth.ai/v1')).toBeTruthy()
  expect(screen.getByText(/get a key at cloud\.opennetworth\.ai/i)).toBeTruthy()
})

it('formats the credit line, and drops a reset date it cannot read', () => {
  expect(creditText({ remaining_usd: 0, limit_usd: 10, resets_at: '2027-01-01T00:00:00Z' })).toBe(
    '$0.00 of $10.00 left · resets Jan 1',
  )
  expect(creditText({ remaining_usd: 1.5, limit_usd: 10, resets_at: 'soon' })).toBe(
    '$1.50 of $10.00 left',
  )
})
