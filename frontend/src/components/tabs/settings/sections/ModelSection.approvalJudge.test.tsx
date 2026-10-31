// @vitest-environment jsdom
//
// Settings → AI → Task models: the Auto-approval judge row, which exists only while
// permissions_v2 is on.
//
// An unpinned judge follows the CONVERSATION's own model, not the instance default (owner
// decision, 2026-10-10). The shared picker names the default model on every other row when no
// override is set, so passing it here would label the judge with the wrong model whenever a
// conversation runs on something else — the row must say "Same as chat model" instead. This was
// found only by driving the real UI: the label prop is a mere fallback, so setting it alone did
// nothing.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

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
import { overrideFlagsForTests } from '../../../../hooks/useFlag'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const PROVIDERS = {
  catalog: [
    {
      key: 'anthropic',
      label: 'Anthropic',
      transport: 'anthropic',
      base_url: '',
      needs_api_key: true,
      editable_base_url: false,
      model_hints: [],
    },
  ],
  configured_providers: {
    anthropic: { has_api_key: true, base_url: '', model: 'claude-haiku-4-5', enabled: true },
  },
  detected_clis: {},
  detected_cli_paths: {},
}

function route(url: string, taskModels: Record<string, string>): Response {
  if (url.startsWith('/api/model-config/providers')) return json(PROVIDERS)
  if (url.startsWith('/api/model-config'))
    return json({
      default_model: 'anthropic:claude-haiku-4-5',
      provider: 'anthropic',
      model: 'claude-haiku-4-5',
      base_url: '',
      task_models: taskModels,
    })
  if (url.startsWith('/api/preferred-models')) return json({ models: [] })
  if (url.startsWith('/api/memory/settings')) return json({ enabled: true })
  if (url.startsWith('/api/memory/facts')) return json({ total: 0 })
  if (url.startsWith('/api/cost/summary'))
    return json({
      total: { turns: 0, input_tokens: 0, cache_read_tokens: 0, output_tokens: 0, cost_usd: 0 },
      by_model: {},
      by_provider: {},
      by_surface: {},
      window_days: 1,
    })
  return json({})
}

beforeEach(() => {
  vi.clearAllMocks()
  invalidateQueryCache(() => true)
})
afterEach(() => cleanup())

describe('ModelSection — the Auto-approval judge row (permissions_v2=true)', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ permissions_v2: true })))
  afterEach(() => restore())

  it('is listed, and unpinned says "Same as chat model" while other rows do not', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => route(String(url), {}))
    render(<ModelSection toast={vi.fn()} />)

    const judge = await screen.findByRole('button', { name: 'Auto-approval judge' })
    expect(judge.textContent).toContain('Same as chat model')

    // Every ordinary task row names the instance default instead (here "Default", the
    // fallback, because the mocked model list is empty) — never this label.
    const memory = await screen.findByRole('button', { name: 'Memory' })
    expect(memory.textContent).not.toContain('Same as chat model')
  })

  it('shows the pinned model once one is chosen', async () => {
    mockAuthFetch.mockImplementation(async (url: string) =>
      route(String(url), { approval: 'anthropic:claude-haiku-4-5' }),
    )
    render(<ModelSection toast={vi.fn()} />)

    await waitFor(async () => {
      const judge = await screen.findByRole('button', { name: 'Auto-approval judge' })
      expect(judge.textContent).not.toContain('Same as chat model')
      expect(judge.textContent).toContain('claude-haiku-4-5')
    })
  })
})

describe('ModelSection — the Auto-approval judge row (permissions_v2=false)', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ permissions_v2: false })))
  afterEach(() => restore())

  it('is not listed, and the other task rows are', async () => {
    mockAuthFetch.mockImplementation(async (url: string) => route(String(url), {}))
    render(<ModelSection toast={vi.fn()} />)

    await screen.findByRole('button', { name: 'Memory' })
    expect(screen.queryByRole('button', { name: 'Auto-approval judge' })).toBeNull()
  })

  it('a judge model pinned earlier neither counts as an override nor shows', async () => {
    mockAuthFetch.mockImplementation(async (url: string) =>
      route(String(url), { approval: 'anthropic:claude-haiku-4-5' }),
    )
    render(<ModelSection toast={vi.fn()} />)

    await screen.findByRole('button', { name: 'Memory' })
    expect(screen.getByText('All tasks use your default model')).toBeTruthy()
    expect(screen.queryByText(/Auto-approval judge/)).toBeNull()
  })
})
