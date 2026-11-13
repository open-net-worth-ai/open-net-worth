// @vitest-environment jsdom
//
// The regressions these pin, both verified against a live instance before this file existed:
//   1. The outgoing wizard offered anthropic|openai|ollama and never looked for a local CLI,
//      so a developer with `claude` on PATH was asked to paste a key.
//   2. Its save shape was wrong for ALL THREE options — `{provider:'anthropic'}` returned 422
//      ("AI model is not configured"), `{provider:'ollama'}` and `{provider:'openai'}` returned
//      422 ("provider must be one of [...]"). `provider` is a TRANSPORT and `model` is
//      required. Onboarding therefore never actually configured AI.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { StepLucius } from './StepLucius'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const CATALOG = [
  {
    key: 'anthropic',
    label: 'Anthropic',
    transport: 'anthropic',
    base_url: '',
    needs_api_key: true,
    model_hints: ['claude-sonnet-4-6', 'claude-haiku-4-5'],
  },
  {
    key: 'claude_cli',
    label: 'Claude Code CLI',
    transport: 'claude_cli',
    base_url: '',
    needs_api_key: false,
    model_hints: ['sonnet', 'opus'],
  },
  {
    key: 'codex_cli',
    label: 'Codex CLI',
    transport: 'codex_cli',
    base_url: '',
    needs_api_key: false,
    model_hints: ['gpt-5.6-sol'],
  },
  {
    key: 'antigravity_cli',
    label: 'Antigravity CLI',
    transport: 'antigravity_cli',
    base_url: '',
    needs_api_key: false,
    model_hints: ['gemini-2.5-flash'],
  },
  {
    key: 'openai',
    label: 'OpenAI',
    transport: 'openai_compatible',
    base_url: 'https://api.openai.com/v1',
    needs_api_key: true,
    model_hints: ['gpt-4o'],
  },
  {
    key: 'ollama',
    label: 'Ollama (local)',
    transport: 'openai_compatible',
    base_url: 'http://localhost:11434/v1',
    needs_api_key: false,
    editable_base_url: true,
    model_hints: ['llama3.1'],
  },
]

function mockApi(
  detected: Record<string, boolean>,
  paths: Record<string, string> = {},
  liveModels: string[] = [],
  extra: Record<string, unknown> = {},
) {
  authFetch.mockImplementation((url: string) => {
    if (url === '/api/model-config/providers') {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            catalog: CATALOG,
            detected_clis: detected,
            detected_cli_paths: paths,
            cli_host_platform: 'macos',
            ...extra,
          }),
      })
    }
    if (url.endsWith('/validate')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ key_status: 'valid', models: liveModels }),
      })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

function putTo(url: string) {
  return authFetch.mock.calls.find((c) => c[0] === url && c[1]?.method === 'PUT')
}

const T = { timeout: 4000 }

/** The first "Use this" button — a detected CLI provider is one click. */
async function firstUseThis(): Promise<HTMLElement> {
  const [first] = await screen.findAllByRole('button', { name: /use this/i }, T)
  if (!first) throw new Error('no "Use this" button rendered')
  return first
}

/** Page one is paced: nothing is offered until the probe answers AND the step has settled. */
async function page1() {
  return screen.findByLabelText(/^provider$/i, {}, T)
}

describe('StepLucius — the detected CLIs', () => {
  it('gives each detected CLI its own card, with the path it will run', async () => {
    mockApi(
      { claude_cli: true, codex_cli: true, antigravity_cli: true },
      { claude_cli: '/opt/homebrew/bin/claude', antigravity_cli: '/Users/test/.local/bin/agy' },
    )
    render(<StepLucius onDone={() => {}} />)

    expect(await screen.findByText(/Found Claude Code on this machine/i, {}, T)).toBeTruthy()
    expect(screen.getByText(/Found Codex on this machine/i)).toBeTruthy()
    expect(screen.getByText(/Found Antigravity CLI on this machine/i)).toBeTruthy()
    expect(screen.getByText('/opt/homebrew/bin/claude')).toBeTruthy()
    expect(screen.getByText('/Users/test/.local/bin/agy')).toBeTruthy()
  })

  it('does not say "on this machine" for a CLI that ships inside the container', async () => {
    mockApi({ codex_cli: true }, { codex_cli: '/usr/local/bin/codex' }, [], {
      cli_in_container: true,
      cli_sign_in: { codex_cli: 'not_signed_in' },
    })
    render(<StepLucius onDone={() => {}} />)

    expect(await screen.findByText(/Codex comes with this Docker install/i, {}, T)).toBeTruthy()
    expect(screen.queryByText(/Found Codex/i)).toBeNull()
    expect(screen.getByText('Not signed in')).toBeTruthy()
    // Signed out, the steps are already open — they are the one thing to do on this card.
    expect(
      screen.getByText('docker exec -it open-net-worth codex login --device-auth'),
    ).toBeTruthy()
  })

  it('offers installation for an absent CLI without claiming it was found', async () => {
    mockApi({ claude_cli: true, codex_cli: false })
    render(<StepLucius onDone={() => {}} />)

    await screen.findByText(/Found Claude Code/i, {}, T)
    expect(screen.queryByText(/Found Codex/i)).toBeNull()
    expect(screen.getByText('Codex', { exact: true })).toBeTruthy()
    expect(screen.getByText('Not installed')).toBeTruthy()
  })

  it('still offers the full provider list — detection is a shortcut, not a verdict', async () => {
    mockApi({ claude_cli: true })
    render(<StepLucius onDone={() => {}} />)

    await screen.findByText(/Found Claude Code/i, {}, T)
    expect(screen.getByLabelText(/^provider$/i)).toBeTruthy()
    expect(screen.getByText(/or use a provider/i)).toBeTruthy()
  })

  it('"Use this" stores the provider, then moves to the model page', async () => {
    mockApi({ claude_cli: true })
    render(<StepLucius onDone={() => {}} />)

    fireEvent.click(await firstUseThis())

    expect(await screen.findByLabelText(/^model$/i, {}, T)).toBeTruthy()
    expect(screen.queryByText(/or use a provider/i)).toBeNull()
  })
})

describe('StepLucius — a CLI that is installed but signed out', () => {
  it('disables Use this and opens the sign-in steps while signed out', async () => {
    mockApi({ codex_cli: true }, { codex_cli: '/usr/local/bin/codex' }, [], {
      cli_in_container: true,
      cli_sign_in: { codex_cli: 'not_signed_in' },
    })
    render(<StepLucius onDone={() => {}} />)

    const useThis = (await firstUseThis()) as HTMLButtonElement
    expect(useThis.disabled).toBe(true)
    expect(
      await screen.findByText('docker exec -it open-net-worth codex login --device-auth', {}, T),
    ).toBeTruthy()
  })

  it('keeps Use this available when the sign-in state is unknown', async () => {
    mockApi({ codex_cli: true }, {}, [], { cli_sign_in: { codex_cli: 'unknown' } })
    render(<StepLucius onDone={() => {}} />)
    expect(((await firstUseThis()) as HTMLButtonElement).disabled).toBe(false)
  })

  // Use this only proved the binary runs, so a signed-out Codex in a fresh Docker install
  // finished setup and Lucius's first message failed (2026-11-11).
  it('stays on the card and opens the docker sign-in steps instead of advancing', async () => {
    // Opening the steps is the whole answer: no second "not signed in" line beside the card's.
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              catalog: CATALOG,
              detected_clis: { codex_cli: true },
              detected_cli_paths: { codex_cli: '/usr/local/bin/codex' },
              cli_host_platform: 'linux',
              cli_in_container: true,
              // The card's status hasn't caught up yet — the click-time check is the backstop.
              cli_sign_in: { codex_cli: 'unknown' },
            }),
        })
      }
      if (url.endsWith('/validate')) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              key_status: 'valid',
              sign_in: 'not_signed_in',
              models: ['gpt-6-luna'],
              error: 'Codex CLI is installed but not signed in.',
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    render(<StepLucius onDone={() => {}} />)

    fireEvent.click(await firstUseThis())

    expect(
      await screen.findByText('docker exec -it open-net-worth codex login --device-auth', {}, T),
    ).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByLabelText(/^model$/i)).toBeNull()
  })
})

describe('StepLucius — discover subscription installation', () => {
  it('offers both missing CLIs before the API-key path', async () => {
    mockApi({ claude_cli: false, codex_cli: false })
    render(<StepLucius onDone={() => {}} />)
    await page1()
    expect(screen.getByText('Claude Code', { exact: true })).toBeTruthy()
    expect(screen.getByText('Codex', { exact: true })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /Install & sign in/i })).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /Use this/i })).toBeNull()
    const choices = document.querySelector('.firstrun-found-group')!
    const apiRows = document.querySelector('.firstrun-rows')!
    expect(choices.compareDocumentPosition(apiRows) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('detects installation on return without resetting a key, selecting a provider or closing help', async () => {
    const detected: Record<string, boolean> = { claude_cli: false, codex_cli: false }
    mockApi(detected)
    render(<StepLucius onDone={() => {}} />)
    await page1()
    fireEvent.change(screen.getByLabelText(/^api key$/i), { target: { value: 'synthetic-draft' } })
    const [installClaude] = screen.getAllByRole('button', { name: /Install & sign in/i })
    if (!installClaude) throw new Error('no install button rendered')
    fireEvent.click(installClaude)
    expect(screen.getByText('curl -fsSL https://claude.ai/install.sh | bash')).toBeTruthy()
    detected.claude_cli = true
    fireEvent(window, new Event('focus'))
    await screen.findByText(/Found Claude Code on this machine/i, {}, T)
    expect((screen.getByLabelText(/^api key$/i) as HTMLInputElement).value).toBe('synthetic-draft')
    expect(screen.getByText('curl -fsSL https://claude.ai/install.sh | bash')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Use this/i })).toBeTruthy()
    expect(authFetch.mock.calls.every((call) => !call[1]?.method)).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /Use this/i }))
    await screen.findByLabelText(/^model$/i, {}, T)
    const count = authFetch.mock.calls.length
    fireEvent(window, new Event('focus'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(authFetch.mock.calls).toHaveLength(count)
  })

  it('keeps the last detected CLI and credential draft when a background check fails', async () => {
    mockApi({ claude_cli: true })
    render(<StepLucius onDone={() => {}} />)
    await page1()
    fireEvent.change(screen.getByLabelText(/^api key$/i), { target: { value: 'synthetic-draft' } })
    authFetch.mockRejectedValueOnce(new Error('offline'))
    fireEvent(window, new Event('focus'))
    await screen.findByText('Could not check installations. Check again.', {}, T)
    expect(screen.getByText(/Found Claude Code on this machine/i)).toBeTruthy()
    expect((screen.getByLabelText(/^api key$/i) as HTMLInputElement).value).toBe('synthetic-draft')
  })
})

describe('StepLucius — the provider list', () => {
  it('lists providers that enrol with a key or a url, never the CLIs', async () => {
    // A CLI's `base_url` is repurposed as its PATH, so putting one through these rows asks a
    // local binary for an OpenAI-compatible endpoint.
    mockApi({ claude_cli: true })
    render(<StepLucius onDone={() => {}} />)

    const select = (await page1()) as HTMLSelectElement
    const values = Array.from(select.options).map((o) => o.value)
    expect(values).toContain('anthropic')
    expect(values).not.toContain('claude_cli')
  })

  it('shows the API key field for a provider that needs one, straight away', async () => {
    // `picked` used to stay null while the select merely DISPLAYED the first option, so none
    // of the rows that belong to a provider ever rendered.
    mockApi({})
    render(<StepLucius onDone={() => {}} />)

    await page1()
    expect(screen.getByLabelText(/api key/i)).toBeTruthy()
    expect(screen.queryByLabelText(/endpoint/i)).toBeNull()
  })

  it('shows an endpoint field only where the endpoint is editable', async () => {
    mockApi({})
    render(<StepLucius onDone={() => {}} />)

    fireEvent.change(await page1(), { target: { value: 'ollama' } })
    expect(screen.getByLabelText(/endpoint/i)).toBeTruthy()
    expect(screen.queryByLabelText(/api key/i)).toBeNull()
  })

  it('will not continue until a required key is given', async () => {
    mockApi({})
    render(<StepLucius onDone={() => {}} />)

    await page1()
    const next = screen.getByRole('button', { name: /continue/i })
    expect(next.hasAttribute('disabled')).toBe(true)

    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-ant-x' } })
    expect(next.hasAttribute('disabled')).toBe(false)
  })
})

describe('StepLucius — the two commits', () => {
  it('page one stores the credential and does NOT set the model yet', async () => {
    // Split deliberately: a live model list needs a persisted key to query with.
    mockApi({})
    render(<StepLucius onDone={() => {}} />)

    await page1()
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-ant-x' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await waitFor(() => {
      const cred = putTo('/api/model-config/providers/anthropic')
      expect(cred).toBeTruthy()
      expect(JSON.parse(cred![1].body).api_key).toBe('sk-ant-x')
    })
    expect(putTo('/api/model-config')).toBeUndefined()
  })

  it('page two sets the transport, endpoint and model, then finishes', async () => {
    mockApi({})
    const onDone = vi.fn()
    render(<StepLucius onDone={onDone} />)

    fireEvent.change(await page1(), { target: { value: 'ollama' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await screen.findByLabelText(/^model$/i, {}, T)
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))

    await waitFor(() => {
      const body = JSON.parse(putTo('/api/model-config')![1].body)
      expect(body.provider).toBe('openai_compatible')
      expect(body.base_url).toBe('http://localhost:11434/v1')
      expect(body.model).toBe('llama3.1')
    })
    await waitFor(() => expect(onDone).toHaveBeenCalled())
  })

  it('saves the model the row shows, not the catalogue default', async () => {
    mockApi({}, {}, ['claude-sonnet-4-6', 'claude-haiku-4-5'])
    render(<StepLucius onDone={() => {}} />)

    await page1()
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-ant-x' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    const model = await screen.findByLabelText(/^model$/i, {}, T)
    expect(model.textContent).toBe('claude-sonnet-4-6')

    fireEvent.click(screen.getByRole('button', { name: /change model/i }))
    fireEvent.click(await screen.findByRole('option', { name: /claude-haiku-4-5/i }))
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))

    await waitFor(() =>
      expect(JSON.parse(putTo('/api/model-config')![1].body).model).toBe('claude-haiku-4-5'),
    )
  })

  it('lets a CLI with no hints save a blank model rather than trapping you', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              catalog: [
                { key: 'codex_cli', label: 'Codex', transport: 'codex_cli' },
                ...CATALOG.filter((c) => c.key === 'anthropic'),
              ],
              detected_clis: { codex_cli: true },
              detected_cli_paths: {},
            }),
        })
      }
      if (url.endsWith('/validate')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ key_status: 'valid', models: [] }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    render(<StepLucius onDone={() => {}} />)

    fireEvent.click(await firstUseThis())
    await screen.findByLabelText(/^model$/i, {}, T)
    const save = screen.getByRole('button', { name: /^continue$/i })
    expect(save.hasAttribute('disabled')).toBe(false)
    fireEvent.click(save)

    await waitFor(() => expect(JSON.parse(putTo('/api/model-config')![1].body).model).toBe(''))
  })
})

describe('StepLucius — going back', () => {
  it('returns to a provider page the dropdown can actually describe', async () => {
    // `picked` survives the trip, so arriving back after "Use this" on a CLI left a CLI
    // selected: no key row, and the select falling back to displaying the first option.
    mockApi({ claude_cli: true })
    render(<StepLucius onDone={() => {}} />)

    fireEvent.click(await firstUseThis())
    await screen.findByLabelText(/^model$/i, {}, T)
    fireEvent.click(screen.getByRole('button', { name: /change provider/i }))

    const select = (await page1()) as HTMLSelectElement
    expect(select.value).toBe('anthropic')
    expect(screen.getByLabelText(/api key/i)).toBeTruthy()
  })
})

describe('StepLucius — this step cannot be skipped', () => {
  it('offers no way past without configuring a model', async () => {
    mockApi({ claude_cli: false })
    const onDone = vi.fn()
    render(<StepLucius onDone={onDone} />)

    await page1()
    expect(screen.queryByRole('button', { name: /not now/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /skip/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /set up later/i })).toBeNull()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('offers a retry, not a dead end, when the provider list cannot be read', async () => {
    authFetch.mockImplementation(() => Promise.reject(new Error('offline')))
    render(<StepLucius onDone={() => {}} />)

    expect(
      await screen.findByText(/could not load the list of model providers/i, {}, T),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy()
  })

  it('re-reads the providers when Try again is pressed', async () => {
    authFetch.mockImplementation(() => Promise.reject(new Error('offline')))
    render(<StepLucius onDone={() => {}} />)

    await screen.findByRole('button', { name: /try again/i }, T)
    const before = authFetch.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))

    await waitFor(() => expect(authFetch.mock.calls.length).toBeGreaterThan(before))
  })
})

describe('StepLucius — the reveal is paced, not raced', () => {
  it('offers nothing until the probe answers, then shows it', async () => {
    mockApi({ claude_cli: true })
    render(<StepLucius onDone={() => {}} />)

    expect(screen.queryByLabelText(/^provider$/i)).toBeNull()
    expect(await page1()).toBeTruthy()
  })

  it('never shows a loading message for a probe that answered quickly', async () => {
    mockApi({ claude_cli: true })
    render(<StepLucius onDone={() => {}} />)

    await page1()
    expect(screen.queryByText(/looking for a model/i)).toBeNull()
  })
})

describe('StepLucius — the server error', () => {
  it('surfaces a rejected key on page one instead of advancing', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ catalog: CATALOG, detected_clis: {} }),
        })
      }
      return Promise.resolve({
        ok: false,
        json: () => Promise.resolve({ detail: 'that key is not valid' }),
      })
    })
    render(<StepLucius onDone={() => {}} />)

    await screen.findByLabelText(/^provider$/i, {}, T)
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByText(/that key is not valid/i)).toBeTruthy()
    expect(screen.queryByLabelText(/^model$/i)).toBeNull()
  })
})

describe('StepLucius — the model list is fetched, not assumed', () => {
  function mockWithLive(models: string[]) {
    authFetch.mockImplementation((url: string, opts?: { method?: string }) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ catalog: CATALOG, detected_clis: {} }),
        })
      }
      if (url.endsWith('/validate') && opts?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ key_status: 'valid', models }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
  }

  it('asks the provider what it serves, instead of showing the catalogue floor', async () => {
    // The catalogue lists a handful of hints that go stale; OpenRouter alone serves hundreds.
    // Page one commits the key first precisely so this call can be made.
    mockWithLive(['anthropic/claude-opus-5', 'openai/gpt-5', 'google/gemini-3-pro'])
    render(<StepLucius onDone={() => {}} />)

    await screen.findByLabelText(/^provider$/i, {}, T)
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-x' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    const model = await screen.findByLabelText(/^model$/i, {}, T)
    await waitFor(() => expect(model.textContent).toBe('anthropic/claude-opus-5'))

    // The list is a searchable modal, not a dropdown — it can run to hundreds of entries.
    fireEvent.click(screen.getByRole('button', { name: /change model/i }))
    expect(await screen.findByRole('option', { name: /gemini-3-pro/i })).toBeTruthy()
    expect(screen.queryByRole('option', { name: /claude-sonnet-4-6/i })).toBeNull()
  })

  it('stops on a provider it could not verify, with the reason and no override', async () => {
    // Nothing passes unproven — a provider that never answered was silently accepted before,
    // and the failure only surfaced when Lucius could not reply. There is no "use anyway":
    // accepting it here only moves the failure to the first message Lucius cannot answer, by
    // which point nobody connects the two.
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ catalog: CATALOG, detected_clis: {} }),
        })
      }
      if (url.endsWith('/validate')) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({ key_status: 'unreachable', error: 'Could not reach Anthropic.' }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    render(<StepLucius onDone={() => {}} />)

    await screen.findByLabelText(/^provider$/i, {}, T)
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-x' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByText(/could not reach anthropic/i, {}, T)).toBeTruthy()
    expect(screen.queryByLabelText(/^model$/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /use anyway/i })).toBeNull()
  })
})

describe('StepLucius — a url that outlived its state', () => {
  it('falls back to the provider page when the model url is opened cold', async () => {
    // /setup/model-pick puts the PAGE in the url, but the chosen provider is component
    // state. Reloading there rendered nothing at all: page two had no provider to describe
    // and page one was switched off by the url.
    mockApi({})
    render(<StepLucius onDone={() => {}} stage="model" onStageChange={() => {}} />)

    expect(await screen.findByLabelText(/^provider$/i, {}, T)).toBeTruthy()
  })

  it('asks the url to go back to the provider page, so Back stays consistent', async () => {
    mockApi({})
    const onStageChange = vi.fn()
    render(<StepLucius onDone={() => {}} stage="model" onStageChange={onStageChange} />)

    await waitFor(() => expect(onStageChange).toHaveBeenCalledWith('provider'), { timeout: 4000 })
  })
})

describe('StepLucius — the model list is searchable', () => {
  it('filters a long list instead of making you scroll it', async () => {
    // OpenRouter returns hundreds. A dropdown cannot be scrolled through usefully, which is
    // why this is the same searchable modal the currency row uses.
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ catalog: CATALOG, detected_clis: {} }),
        })
      }
      if (url.endsWith('/validate')) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              key_status: 'valid',
              models: ['anthropic/claude-opus-5', 'openai/gpt-5', 'meta/llama-4'],
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    render(<StepLucius onDone={() => {}} />)

    await screen.findByLabelText(/^provider$/i, {}, T)
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-x' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await screen.findByLabelText(/^model$/i, {}, T)
    fireEvent.click(screen.getByRole('button', { name: /change model/i }))

    const search = await screen.findByLabelText(/search models/i)
    fireEvent.change(search, { target: { value: 'llama' } })

    expect(screen.getByRole('option', { name: /meta\/llama-4/i })).toBeTruthy()
    expect(screen.queryByRole('option', { name: /gpt-5/i })).toBeNull()
  })
})

describe('StepLucius — the wait shows on the button you pressed', () => {
  it('puts the wait on the control that started it, not above the page', async () => {
    // Storing the key, proving it and fetching the model list is a real round trip. The
    // feedback belongs on the control that started it — a banner floating above the cards
    // explained a wait next to something the user had not touched.
    let release: (v: unknown) => void = () => {}
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ catalog: CATALOG, detected_clis: {} }),
        })
      }
      if (url === '/api/model-config/providers/anthropic') {
        return new Promise((res) => {
          release = res
        })
      }
      if (url.endsWith('/validate')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ key_status: 'valid', models: ['claude-sonnet-4-6'] }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    render(<StepLucius onDone={() => {}} />)

    await screen.findByLabelText(/^provider$/i, {}, T)
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-x' } })
    const next = screen.getByRole('button', { name: /^continue$/i })
    fireEvent.click(next)

    await waitFor(() => expect(next.hasAttribute('disabled')).toBe(true))
    // The status sits BESIDE the button, in the same slot the error uses — one row, one
    // message, so nothing appearing there can push the page.
    expect(screen.getByText(/checking your key/i)).toBeTruthy()

    release({ ok: true, json: () => Promise.resolve({}) })
    await waitFor(() => expect(screen.queryByLabelText(/^model$/i)).toBeTruthy())
  })
})

describe('StepLucius — the picker takes the keyboard', () => {
  async function openModelPicker() {
    authFetch.mockImplementation((url: string, opts?: { method?: string }) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ catalog: CATALOG, detected_clis: {} }),
        })
      }
      if (url.endsWith('/validate') && opts?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              key_status: 'valid',
              models: ['one/alpha', 'two/beta', 'three/gamma'],
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    render(<StepLucius onDone={() => {}} />)
    await screen.findByLabelText(/^provider$/i, {}, T)
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: 'sk-x' } })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))
    await screen.findByLabelText(/^model$/i, {}, T)
    fireEvent.click(screen.getByRole('button', { name: /change model/i }))
    return screen.findByLabelText(/search models/i)
  }

  it('moves the highlight with the arrow keys and picks with Enter', async () => {
    // 419 rows is unusable if the pointer is the only way in: you type to narrow, then arrow
    // without ever leaving the field.
    const search = await openModelPicker()

    fireEvent.keyDown(search, { key: 'ArrowDown' })
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    fireEvent.keyDown(search, { key: 'Enter' })

    await waitFor(() => expect(screen.getByLabelText(/^model$/i).textContent).toBe('three/gamma'))
  })

  it('returns the highlight to the top when the search changes what is in front of you', async () => {
    const search = await openModelPicker()

    fireEvent.keyDown(search, { key: 'ArrowDown' })
    fireEvent.change(search, { target: { value: 'beta' } })
    fireEvent.keyDown(search, { key: 'Enter' })

    await waitFor(() => expect(screen.getByLabelText(/^model$/i).textContent).toBe('two/beta'))
  })
})

describe('StepLucius — a CLI suggests its own default model', () => {
  function mockCli(live: string[]) {
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              catalog: CATALOG,
              detected_clis: { codex_cli: true },
              detected_cli_paths: {},
              cli_default_models: { codex_cli: 'synthetic-default', claude_cli: 'opus' },
            }),
        })
      }
      if (url.endsWith('/validate')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ key_status: 'valid', models: live }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
  }

  it('preselects the CLI default over the catalogue hint, and saves it', async () => {
    // The hint (gpt-5.6-sol) is ALSO in the live list — the old code kept it for that reason.
    mockCli(['gpt-5.6-sol', 'synthetic-default'])
    render(<StepLucius onDone={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: /use this/i }, T))

    const model = await screen.findByLabelText(/^model$/i, {}, T)
    await waitFor(() => expect(model.textContent).toBe('synthetic-default'))
    expect(screen.getByText(/own default/i)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
    await waitFor(() => {
      const body = JSON.parse(putTo('/api/model-config')![1].body)
      expect(body.provider).toBe('codex_cli')
      expect(body.model).toBe('synthetic-default')
    })
  })

  it('keeps the CLI default selectable when the CLI never lists it', async () => {
    mockCli(['gpt-5.6-sol'])
    render(<StepLucius onDone={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: /use this/i }, T))
    const model = await screen.findByLabelText(/^model$/i, {}, T)
    await waitFor(() => expect(model.textContent).toBe('synthetic-default'))

    fireEvent.click(screen.getByRole('button', { name: /change model/i }))
    expect(await screen.findByText('Default in Codex CLI')).toBeTruthy()
  })
})

describe('StepLucius — on a Private Cloud server', () => {
  it('offers no subscription cards and no local model, and says why', async () => {
    mockApi({ claude_cli: true, codex_cli: true })
    render(<StepLucius onDone={() => {}} gatewayMode />)

    const provider = await page1()
    expect(screen.queryByText(/Found Claude Code/i)).toBeNull()
    expect(screen.queryByText('Connect a subscription')).toBeNull()
    expect(provider.textContent).not.toMatch(/ollama/i)
    expect(screen.getByText(/can.t reach a model running on your own computer/i)).toBeTruthy()
    expect(screen.getByText(/open Settings → Terminal/i)).toBeTruthy()
  })
})

describe('StepLucius — a provider already saved survives a remount', () => {
  // Reload, or Back from the password step: the component starts empty but the server holds
  // the choice. It used to show the default provider with Continue disabled.
  function mockSaved() {
    authFetch.mockImplementation((url: string) => {
      const reply = (body: unknown) =>
        Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
      if (url === '/api/model-config/providers') {
        return reply({
          catalog: CATALOG,
          detected_clis: {},
          detected_cli_paths: {},
          cli_host_platform: 'macos',
          configured_providers: { openai: { has_api_key: true, key_status: 'valid' } },
        })
      }
      if (url === '/api/model-config') {
        return reply({
          default_model: 'openai:gpt-4o',
          provider: 'openai_compatible',
          model: 'gpt-4o',
        })
      }
      return reply({ key_status: 'valid', models: [] })
    })
  }

  it('preselects the saved provider and lets Continue through without re-entering its key', async () => {
    mockSaved()
    render(<StepLucius onDone={() => {}} />)

    const provider = (await page1()) as HTMLSelectElement
    await waitFor(() => expect(provider.value).toBe('openai'))
    const key = screen.getByLabelText(/api key/i) as HTMLInputElement
    expect(key.placeholder).toMatch(/saved/i)
    expect(
      (screen.getByRole('button', { name: /^continue$/i }) as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('stays on the model page when the url says so, instead of bouncing to the provider page', async () => {
    mockSaved()
    const onStageChange = vi.fn()
    render(<StepLucius onDone={() => {}} stage="model" onStageChange={onStageChange} />)

    const model = await screen.findByLabelText(/^model$/i, {}, T)
    expect(model.textContent).toBe('gpt-4o')
    expect(onStageChange).not.toHaveBeenCalled()
  })
})
