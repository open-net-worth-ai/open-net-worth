// @vitest-environment jsdom
//
// Settings → AI: several OpenAI-compatible ("Custom") endpoints at once. "Custom" is one catalog
// row, so adding a second used to PUT to the same key and overwrite the first. A further endpoint
// is its own `custom_<id>` key, shown as a copy of that row under the owner's Display name.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({ models: [], defaultModel: 'anthropic:claude-haiku-4-5', loading: false }),
}))

import { ModelSection } from './ModelSection'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const entry = (key: string, label: string, editable = false, base_url = '') => ({
  key,
  label,
  transport: key === 'anthropic' ? 'anthropic' : 'openai_compatible',
  base_url,
  needs_api_key: true,
  editable_base_url: editable,
  model_hints: [],
})

const PROVIDERS = {
  catalog: [
    entry('anthropic', 'Anthropic'),
    entry('openrouter', 'OpenRouter', false, 'https://openrouter.ai/api/v1'),
    entry('custom', 'Custom (OpenAI-compatible)', true),
  ],
  configured_providers: {
    anthropic: { has_api_key: true, base_url: '', model: 'claude-haiku-4-5', enabled: true },
    custom: {
      has_api_key: true,
      base_url: 'https://one.example/v1',
      model: '',
      enabled: true,
      label: 'Work gateway',
    },
    custom_ab12cd34: {
      has_api_key: true,
      base_url: 'https://two.example/v1',
      model: '',
      enabled: true,
      label: 'Home server',
    },
  },
  detected_clis: {},
  detected_cli_paths: {},
}

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })

function route(url: string): Response {
  if (url.startsWith('/api/model-config/providers')) return json(PROVIDERS)
  if (url.startsWith('/api/model-config'))
    return json({
      default_model: 'anthropic:claude-haiku-4-5',
      provider: 'anthropic',
      model: 'claude-haiku-4-5',
      base_url: '',
      task_models: {},
    })
  if (url.startsWith('/api/preferred-models')) return json({ models: [] })
  return json({})
}

beforeEach(() => {
  vi.clearAllMocks()
  invalidateQueryCache(() => true)
  mockAuthFetch.mockImplementation(async (url: string) => route(String(url)))
})
afterEach(() => cleanup())

const puts = () =>
  mockAuthFetch.mock.calls
    .filter((c) => (c[1] as RequestInit | undefined)?.method === 'PUT')
    .map((c) => ({ url: String(c[0]), body: JSON.parse(String((c[1] as RequestInit).body)) }))

describe('several Custom endpoints', () => {
  it('lists each under its own Display name', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Work gateway')).toBeTruthy())
    expect(screen.getByText('Home server')).toBeTruthy()
    expect(screen.getByText('https://one.example/v1')).toBeTruthy()
    expect(screen.getByText('https://two.example/v1')).toBeTruthy()
  })

  it('adding another Custom endpoint gets its own key instead of overwriting `custom`', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Work gateway')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: /Add provider/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Add AI provider' })
    fireEvent.change(within(dialog).getAllByRole('combobox')[0]!, { target: { value: 'custom' } })
    fireEvent.change(within(dialog).getByPlaceholderText('e.g. Work gateway'), {
      target: { value: 'Third box' },
    })
    fireEvent.change(within(dialog).getByPlaceholderText('https://…/v1'), {
      target: { value: 'https://three.example/v1' },
    })
    fireEvent.change(within(dialog).getByPlaceholderText('Paste your API key'), {
      target: { value: 'sk-three' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add provider' }))

    await waitFor(() => expect(puts().length).toBe(1))
    const [put] = puts()
    expect(put!.url).toMatch(/^\/api\/model-config\/providers\/custom_[a-z0-9]+$/)
    expect(put!.url).not.toBe('/api/model-config/providers/custom')
    expect(put!.body).toMatchObject({
      label: 'Third box',
      base_url: 'https://three.example/v1',
      api_key: 'sk-three',
    })
  })

  it('editing an extra endpoint saves to its own key and keeps its name', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Home server')).toBeTruthy())
    const row = screen.getByText('Home server').closest('.rd-prov') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit provider' })
    fireEvent.change(within(dialog).getByPlaceholderText('e.g. Work gateway'), {
      target: { value: 'Home server 2' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(puts().length).toBe(1))
    expect(puts()[0]!.url).toBe('/api/model-config/providers/custom_ab12cd34')
    expect(puts()[0]!.body.label).toBe('Home server 2')
  })
})
