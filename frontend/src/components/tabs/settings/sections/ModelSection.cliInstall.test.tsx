// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

function expectNoWrites() {
  expect(
    mockAuthFetch.mock.calls.filter(([, init]) => init?.method && init.method !== 'GET'),
  ).toEqual([])
}

it.each([
  ['windows', 'irm https://claude.ai/install.ps1 | iex'],
  ['macos', 'curl -fsSL https://claude.ai/install.sh | bash'],
] as const)(
  'shows missing subscriptions before Default model with server %s instructions',
  async (platform, command) => {
    providers.cli_host_platform = platform
    render(<ModelSection toast={vi.fn()} />)
    const group = await screen.findByRole('group', { name: 'Connect a subscription' })
    expect(within(group).getByText('Claude Code CLI')).toBeTruthy()
    expect(within(group).getByText('Codex CLI')).toBeTruthy()
    expect(
      group.compareDocumentPosition(screen.getAllByText('Default model')[0]!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    const card = subscription('Claude Code CLI')
    fireEvent.click(within(card).getByRole('button', { name: /install & sign in/i }))
    expect(within(card).getByText(command)).toBeTruthy()
    expectNoWrites()
  },
)

it('avoids duplicate subscription cards for configured or detected CLIs and labels presence Installed', async () => {
  providers.configured_providers.claude_cli = {
    has_api_key: false,
    base_url: '',
    model: '',
    enabled: true,
  }
  providers.detected_clis.codex_cli = true
  providers.detected_cli_paths.codex_cli = '/synthetic/codex'
  render(<ModelSection toast={vi.fn()} />)
  await screen.findByText('/synthetic/codex')
  expect(screen.getAllByText('Claude Code CLI')).toHaveLength(1)
  expect(screen.getAllByText('Codex CLI')).toHaveLength(1)
  expect(screen.queryByRole('group', { name: 'Connect a subscription' })).toBeNull()
  expect(within(subscription('Codex CLI')).getByText('Installed')).toBeTruthy()
  expectNoWrites()
})

it('discovers a newly installed CLI on focus without resetting an unsaved API-key editor', async () => {
  render(<ModelSection toast={vi.fn()} />)
  await screen.findByRole('group', { name: 'Connect a subscription' })
  fireEvent.click(within(subscription('Anthropic')).getByRole('button', { name: 'Edit' }))
  const key = screen.getByPlaceholderText('•••••••• (saved)') as HTMLInputElement
  fireEvent.change(key, { target: { value: 'sk-unsaved-draft' } })
  providers.detected_clis.claude_cli = true
  providers.detected_cli_paths.claude_cli = '/synthetic/claude'
  fireEvent.focus(window)
  await screen.findByText('/synthetic/claude')
  expect(screen.getByRole('dialog', { name: 'Edit provider' })).toBeTruthy()
  expect(key.value).toBe('sk-unsaved-draft')
  expect(changed).toHaveBeenCalledOnce()
  expectNoWrites()
})

it('re-checks on focus and refreshes models only when detection or the executable path changes', async () => {
  render(<ModelSection toast={vi.fn()} />)
  await screen.findByRole('group', { name: 'Connect a subscription' })
  const card = subscription('Claude Code CLI')
  fireEvent.click(within(card).getByRole('button', { name: /install & sign in/i }))
  providers.detected_clis.claude_cli = true
  providers.detected_cli_paths.claude_cli = '/synthetic/claude'
  fireEvent.focus(window)
  await screen.findByText('/synthetic/claude')
  expect(subscription('Claude Code CLI')).toBe(card)
  expect(screen.getAllByText('Claude Code CLI')).toHaveLength(1)
  expect(
    within(card)
      .getByRole('button', { name: /install & sign in/i })
      .getAttribute('aria-expanded'),
  ).toBe('true')
  expect(within(card).getByText('claude auth login')).toBeTruthy()
  expect(within(card).getByText('Installed')).toBeTruthy()
  expect(within(card).getByRole('button', { name: 'Edit' })).toBeTruthy()
  expect(changed).toHaveBeenCalledOnce()
  await act(async () => {
    fireEvent.focus(window)
  })
  expect(changed).toHaveBeenCalledOnce()
  providers.detected_cli_paths.claude_cli = '/synthetic/new/claude'
  fireEvent.focus(window)
  await screen.findByText('/synthetic/new/claude')
  expect(changed).toHaveBeenCalledTimes(2)
  expectNoWrites()
})

it('keeps the last detected provider visible and offers a friendly status after a background failure', async () => {
  providers.detected_clis.codex_cli = true
  providers.detected_cli_paths.codex_cli = '/synthetic/codex'
  render(<ModelSection toast={vi.fn()} />)
  await screen.findByText('/synthetic/codex')
  failed = true
  fireEvent.focus(window)
  await screen.findByText('Could not check installations. Check again.')
  expect(screen.getByText('/synthetic/codex')).toBeTruthy()
  expect(within(subscription('Codex CLI')).getByText('Installed')).toBeTruthy()
  failed = false
  fireEvent.focus(window)
  await waitFor(() =>
    expect(screen.queryByText('Could not check installations. Check again.')).toBeNull(),
  )
  expect(changed).not.toHaveBeenCalled()
  expectNoWrites()
})

it('keeps the CLI Binary path draft while editor installation help checks the catalog', async () => {
  render(<ModelSection toast={vi.fn()} />)
  await screen.findByRole('group', { name: 'Connect a subscription' })
  fireEvent.click(screen.getByRole('button', { name: /add provider/i }))
  const modal = screen.getByRole('dialog', { name: 'Add AI provider' })
  const path = within(modal).getByPlaceholderText('claude') as HTMLInputElement
  fireEvent.change(path, { target: { value: '/synthetic/draft/claude' } })
  fireEvent.click(within(modal).getByRole('button', { name: /install & sign in/i }))
  providers.detected_clis.claude_cli = true
  providers.detected_cli_paths.claude_cli = '/synthetic/claude'
  fireEvent.focus(window)
  await screen.findByText('/synthetic/claude')
  expect(path.value).toBe('/synthetic/draft/claude')
  expect(within(modal).getByText('irm https://claude.ai/install.ps1 | iex')).toBeTruthy()
  expectNoWrites()
})

it('does not announce unchanged five-second detection polls as model configuration writes', async () => {
  vi.useFakeTimers()
  await act(async () => {
    render(<ModelSection toast={vi.fn()} />)
  })
  const reads = () =>
    mockAuthFetch.mock.calls.filter(([url]) => String(url) === '/api/model-config/providers').length
  const initial = reads()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000)
  })
  expect(reads()).toBe(initial + 2)
  expect(changed).not.toHaveBeenCalled()
  expectNoWrites()
})
