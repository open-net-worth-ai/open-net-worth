// @vitest-environment jsdom
//
// Regressions pinned here:
//   - first run was 7 steps and a <Modal> floating over a screen that was already onboarding;
//   - completion was tracked in localStorage['onboarding-dismissed'], which is per-browser, so
//     it reappeared in a private window and on every other device.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { FirstRun, stageToStep, stepToStage } from './FirstRun'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  localStorage.clear()
})

/** A resolved fetch Response stub. */
function ok(body: unknown = {}) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}

function mockApi() {
  authFetch.mockImplementation((url: string) => {
    if (url === '/api/model-config/providers') {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            // A detected CLI, so the flow tests can enrol and move on. There is no skip any
            // more: every way past this step configures a model.
            catalog: [{ key: 'claude_cli', label: 'Claude Code', transport: 'claude_cli' }],
            detected_clis: { claude_cli: true },
            detected_cli_paths: { claude_cli: '/usr/local/bin/claude' },
          }),
      })
    }
    // Nothing passes unproven any more, so the flow tests have to give a real verdict.
    if (url.endsWith('/validate')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ key_status: 'valid', models: ['sonnet'] }),
      })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

function called(url: string, method: string) {
  return authFetch.mock.calls.some((c) => c[0] === url && c[1]?.method === method)
}

/** From the greeting, into the numbered flow (step 0). */
async function enterSetup() {
  fireEvent.click(await screen.findByRole('button', { name: /^get started$/i }))
}

/** The first "Use this" button — a detected CLI provider is one click. */
async function firstUseThis(): Promise<HTMLElement> {
  const [first] = await screen.findAllByRole('button', { name: /use this/i }, { timeout: 4000 })
  if (!first) throw new Error('no "Use this" button rendered')
  return first
}

/** Walk the greeting into all three screens, declining the two optional ones. */
async function walkThrough() {
  await enterSetup()
  fireEvent.click(await screen.findByRole('button', { name: /continue/i }))
  // Step 2 is two stages on one page: pick a provider, then confirm it. It also holds its
  // answer for MIN_REVEAL_MS before showing anything, so it is inherently slower to appear
  // than the default 1s findBy timeout allows on a loaded run.
  // Step 2 is two pages: pick a provider (a detected CLI is one click), then the model.
  fireEvent.click(await firstUseThis())
  fireEvent.click(await screen.findByRole('button', { name: /^continue$/i }, { timeout: 4000 }))
  fireEvent.click(await screen.findByRole('button', { name: /skip/i })) // password
}

describe('FirstRun', () => {
  it('is three steps, not seven', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} />)
    await enterSetup()
    expect(await screen.findByText(/step 1 of 3/i)).toBeTruthy()
  })

  it('saves the base currency before leaving the welcome screen', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} />)
    await enterSetup()

    fireEvent.click(await screen.findByRole('button', { name: /continue/i }))

    await waitFor(() => {
      const put = authFetch.mock.calls.find(
        (c) => c[0] === '/api/profile' && c[1]?.method === 'PUT',
      )
      expect(put).toBeTruthy()
      expect(JSON.parse(put![1].body).base_currency).toBeTruthy()
    })
  })

  it('advances through all three screens in order', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} />)
    await enterSetup()

    expect(await screen.findByText(/step 1 of 3/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))
    expect(await screen.findByText(/step 2 of 3/i)).toBeTruthy()
    // Step 2 is two pages: pick a provider (a detected CLI is one click), then the model.
    fireEvent.click(await firstUseThis())
    fireEvent.click(await screen.findByRole('button', { name: /^continue$/i }, { timeout: 4000 }))
    expect(await screen.findByText(/step 3 of 3/i)).toBeTruthy()
  })

  it('stamps completion on the SERVER when finished', async () => {
    mockApi()
    const onDone = vi.fn()
    render(<FirstRun onDone={onDone} />)

    await walkThrough()

    await waitFor(() => expect(called('/api/setup-status/complete', 'POST')).toBe(true))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
  })

  it('never writes the retired per-browser dismissal flag', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} />)

    await walkThrough()

    await waitFor(() => expect(localStorage.getItem('onboarding-dismissed')).toBeNull())
  })

  it('still lets the user out when the completion stamp fails', async () => {
    // The stamp is a convenience signal, never a gate — a failing POST must not strand
    // someone inside first run forever.
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/setup-status/complete') return Promise.reject(new Error('offline'))
      if (url.endsWith('/validate')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ key_status: 'valid', models: ['sonnet'] }),
        })
      }
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              catalog: [{ key: 'claude_cli', label: 'Claude Code', transport: 'claude_cli' }],
              detected_clis: { claude_cli: true },
              detected_cli_paths: { claude_cli: '/usr/local/bin/claude' },
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    const onDone = vi.fn()
    render(<FirstRun onDone={onDone} />)

    await walkThrough()

    await waitFor(() => expect(onDone).toHaveBeenCalled())
  })

  it('does not trap the user on screen 1 when the currency save fails', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/profile') return Promise.reject(new Error('offline'))
      if (url === '/api/model-config/providers') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ catalog: [], detected_clis: {} }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    render(<FirstRun onDone={() => {}} />)
    await enterSetup()

    fireEvent.click(await screen.findByRole('button', { name: /continue/i }))
    expect(await screen.findByText(/step 2 of 3/i)).toBeTruthy()
  })
})

describe('FirstRun — the restore branch', () => {
  it('returns to the folder step from the restore Back action', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} initialStep={-1} />)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(await screen.findByRole('button', { name: /backup to restore/i })).toBeTruthy()
  })

  it('offers restore on the folder step, not the greeting', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} />)
    expect(screen.queryByRole('button', { name: /backup to restore/i })).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: /^get started$/i }))
    expect(await screen.findByRole('button', { name: /backup to restore/i })).toBeTruthy()
  })

  it('leaves the numbered flow entirely — restore is not step 1 of 3', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: /^get started$/i }))
    fireEvent.click(await screen.findByRole('button', { name: /backup to restore/i }))

    expect(await screen.findByText(/restore a backup/i)).toBeTruthy()
    expect(screen.queryByText(/step 1 of 3/i)).toBeNull()
  })

  it('can back out to ordinary setup', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: /^get started$/i }))
    fireEvent.click(await screen.findByRole('button', { name: /backup to restore/i }))
    fireEvent.click(await screen.findByRole('button', { name: /set up instead/i }))

    expect(await screen.findByText(/step 1 of 3/i)).toBeTruthy()
  })
})

describe('FirstRun — the chosen folder states what is already in it', () => {
  it('says a folder holding a book will be opened, not replaced', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/setup-status/data-location') {
        return ok({ data_dir: '~/.open-net-worth/data' })
      }
      if (url.startsWith('/api/setup-status/inspect-path')) {
        return ok({
          has_book: true,
          accounts: 30,
          transactions: 2101,
          writable: true,
        })
      }
      if (url === '/api/model-config/providers') return ok({ catalog: [], detected_clis: {} })
      return ok({})
    })
    render(<FirstRun onDone={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: /^get started$/i }))

    expect(await screen.findByText(/Already holds 30 accounts/i)).toBeTruthy()
    expect(screen.getByText(/opened as it is, not replaced/i)).toBeTruthy()
  })

  it('says an empty folder is new', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/setup-status/data-location') {
        return ok({ data_dir: '~/.open-net-worth/data' })
      }
      if (url.startsWith('/api/setup-status/inspect-path')) {
        return ok({
          has_book: false,
          accounts: null,
          transactions: null,
          writable: true,
        })
      }
      if (url === '/api/model-config/providers') return ok({ catalog: [], detected_clis: {} })
      return ok({})
    })
    render(<FirstRun onDone={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: /^get started$/i }))

    expect(await screen.findByText(/Everything lives here/i)).toBeTruthy()
  })
})

describe('FirstRun — setup is a place, not a layer', () => {
  it('maps every screen to a url segment and back', () => {
    // The pairing has to hold in both directions: RouterShell reads a pathname to decide what
    // to render, and FirstRun emits a slug to decide what the address bar says.
    for (const [stage, step] of [
      ['folder', 0],
      ['model', 1],
      ['password', 2],
      ['restore', -1],
    ] as const) {
      expect(stageToStep(stage)).toBe(step)
      expect(stepToStage(step)).toBe(stage)
    }
  })

  it('treats an unknown or absent segment as the greeting', () => {
    expect(stageToStep(undefined)).toBe(-2)
    expect(stageToStep('')).toBe(-2)
    expect(stageToStep('nonsense')).toBe(-2)
  })

  it('opens on the screen the url names, so a reload lands where you were', async () => {
    mockApi()
    render(<FirstRun onDone={() => {}} initialStep={stageToStep('model')} />)

    // Screen 2 of 3 — the model step, not the greeting the flow used to snap back to.
    expect(await screen.findByText(/step 2 of 3/i)).toBeTruthy()
  })

  it('tells the url when the screen changes', async () => {
    mockApi()
    const onStepChange = vi.fn()
    render(<FirstRun onDone={() => {}} onStepChange={onStepChange} />)

    fireEvent.click(await screen.findByRole('button', { name: /^get started$/i }))

    expect(onStepChange).toHaveBeenCalledWith('folder', undefined)
  })
})

describe('FirstRun — the browser owns the history', () => {
  it('follows the url when it changes underneath, as Back and Forward do', async () => {
    mockApi()
    const onStepChange = vi.fn()
    const { rerender } = render(
      <FirstRun onDone={() => {}} initialStep={stageToStep('model')} onStepChange={onStepChange} />,
    )
    expect(await screen.findByText(/step 2 of 3/i)).toBeTruthy()

    // What the browser's Back button does: the route changes, the props change, the screen
    // follows. Local state plus a sync effect could not do this — the effect fired with the
    // stale prop and snapped the screen back, so Back appeared to do nothing.
    rerender(
      <FirstRun
        onDone={() => {}}
        initialStep={stageToStep('folder')}
        onStepChange={onStepChange}
      />,
    )

    expect(await screen.findByText(/step 1 of 3/i)).toBeTruthy()
  })

  it('asks the url to move rather than moving itself, when the url is in charge', async () => {
    mockApi()
    const onStepChange = vi.fn()
    render(
      <FirstRun
        onDone={() => {}}
        initialStep={stageToStep('folder')}
        onStepChange={onStepChange}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Back' }))

    // No local state to move: it emits, the router navigates, the new url comes back as a
    // prop. Two writers for one fact is what broke this the first time.
    expect(onStepChange).toHaveBeenCalledWith('', undefined)
  })
})

describe('FirstRun — Back from the password step', () => {
  function mockSavedModel(defaultModel: string) {
    authFetch.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve(url === '/api/model-config' ? { default_model: defaultModel } : {}),
      }),
    )
  }

  it('returns to the model page, replacing the entry, when a model is already saved', async () => {
    mockSavedModel('openrouter:z-ai/glm-4.6')
    const onStepChange = vi.fn()
    render(
      <FirstRun
        onDone={() => {}}
        initialStep={stageToStep('password')}
        currentStage="password"
        onStepChange={onStepChange}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Back' }))

    await waitFor(() => expect(onStepChange).toHaveBeenCalledWith('model-pick', { replace: true }))
  })

  it('falls back to the previous step when nothing is saved', async () => {
    mockSavedModel('')
    const onStepChange = vi.fn()
    render(
      <FirstRun
        onDone={() => {}}
        initialStep={stageToStep('password')}
        currentStage="password"
        onStepChange={onStepChange}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Back' }))

    await waitFor(() => expect(onStepChange).toHaveBeenCalledWith('model', undefined))
  })
})
