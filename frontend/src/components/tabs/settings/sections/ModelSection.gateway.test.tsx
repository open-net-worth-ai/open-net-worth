// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({ models: [], defaultModel: 'anthropic:synthetic-model', loading: false }),
}))

import { authFetch } from '../../../../api/client'
import { deleteCachedQueryData } from '../../../../state/queryCache'
import { ModelSection } from './ModelSection'

const mockAuthFetch = vi.mocked(authFetch)
const changed = vi.fn()
const CATALOG = [
  {
    key: 'anthropic',
    label: 'Anthropic',
    transport: 'anthropic',
    base_url: '',
    needs_api_key: true,
    editable_base_url: false,
    model_hints: [],
  },
  {
    key: 'claude_cli',
    label: 'Claude Code CLI',
    transport: 'claude_cli',
    base_url: '',
    needs_api_key: false,
    editable_base_url: true,
    model_hints: [],
    cli_binary: 'claude',
  },
  {
    key: 'codex_cli',
    label: 'Codex CLI',
    transport: 'codex_cli',
    base_url: '',
    needs_api_key: false,
    editable_base_url: true,
    model_hints: [],
    cli_binary: 'codex',
  },
  {
    key: 'ollama',
    label: 'Ollama (local)',
    transport: 'openai_compatible',
    base_url: 'http://localhost:11434/v1',
    needs_api_key: false,
    editable_base_url: true,
    model_hints: [],
  },
]
let providers: {
  catalog: typeof CATALOG
  configured_providers: Record<
    string,
    { has_api_key: boolean; base_url: string; model: string; enabled: boolean }
  >
  detected_clis: Record<string, boolean>
  detected_cli_paths: Record<string, string>
  cli_host_platform: 'macos' | 'windows' | 'linux' | 'other'
}
let failed = false

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  for (const key of ['/api/model-config/providers', '/api/model-config', '/api/preferred-models']) {
    deleteCachedQueryData(key)
  }
  providers = {
    catalog: CATALOG,
    configured_providers: {
      anthropic: { has_api_key: true, base_url: '', model: 'synthetic-model', enabled: true },
    },
    detected_clis: { claude_cli: false, codex_cli: false },
    detected_cli_paths: { claude_cli: '', codex_cli: '' },
    cli_host_platform: 'windows',
  }
  failed = false
  mockAuthFetch.mockImplementation(async (input) => {
    const url = String(input)
    if (url === '/api/model-config/providers') return json(providers, failed ? 503 : 200)
    if (url === '/api/model-config')
      return json({
        default_model: 'anthropic:synthetic-model',
        provider: 'anthropic',
        model: 'synthetic-model',
        base_url: '',
        task_models: {},
      })
    if (url === '/api/preferred-models') return json({ models: [] })
    throw new Error(`Unexpected request: ${url}`)
  })
  window.addEventListener('model-config-changed', changed)
})

afterEach(() => {
  cleanup()
  window.removeEventListener('model-config-changed', changed)
  vi.useRealTimers()
})

function subscription(label: string) {
  return screen.getByText(label).closest('.rd-prov') as HTMLElement
}

it('on a Private Cloud server, points subscription sign-in at the Terminal', async () => {
  const openSection = vi.fn()
  render(<ModelSection toast={vi.fn()} gatewayMode openSection={openSection} />)
  const card = await waitFor(() => subscription('Claude Code CLI'))
  fireEvent.click(within(card).getByRole('button', { name: /sign in from terminal/i }))
  expect(within(card).getByText('claude', { selector: 'code' })).toBeTruthy()
  expect(within(card).getByText(/your own agreement with their provider/i)).toBeTruthy()
  expect(within(card).queryByText(/curl -fsSL|irm https:/)).toBeNull()
  fireEvent.click(within(card).getByRole('button', { name: 'Open Terminal' }))
  expect(openSection).toHaveBeenCalledWith('terminal')
})

it('on a Private Cloud server, hides local models, with no extra note', async () => {
  render(<ModelSection toast={vi.fn()} gatewayMode openSection={vi.fn()} />)
  await screen.findByRole('group', { name: 'Connect a subscription' })
  expect(screen.queryByText(/can.t reach a model running on your own computer/i)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /add provider/i }))
  expect(screen.queryByText('Ollama (local)')).toBeNull()
})

it('off a Private Cloud server, local models and install steps stay as they were', async () => {
  render(<ModelSection toast={vi.fn()} />)
  await screen.findByRole('group', { name: 'Connect a subscription' })
  expect(screen.queryByText(/can.t reach a model running on your own computer/i)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /add provider/i }))
  expect(screen.getAllByText('Ollama (local)').length).toBeGreaterThan(0)
})
