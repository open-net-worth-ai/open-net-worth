// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VoiceSection } from './VoiceSection'
import { useConnectorSaveHost } from './formSave'

const { authFetch, restartCall } = vi.hoisted(() => ({
  authFetch: vi.fn(),
  restartCall: vi.fn(async (change: () => Promise<void>) => change()),
}))
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('../../chat/lucius/voiceCall', () => ({ restartCall }))

const CONFIG = {
  enabled: true,
  ready: true,
  livekit_url: 'wss://acme.livekit.cloud',
  front_model: 'google/gemma-4-31b-it',
  stt_model: 'auto',
  tts_model: 'inworld/inworld-tts-2-flash',
  tts_voice: '',
  language: 'en',
  noise_filter: 'standard',
  has_api_key: true,
  has_api_secret: true,
  worker: { flag_on: true, libraries_installed: true, running: true, error: '' },
}

function Host() {
  const host = useConnectorSaveHost()
  return (
    <host.Provider>
      <VoiceSection />
      <button onClick={() => void host.run()}>Save</button>
    </host.Provider>
  )
}

function serve(config: object) {
  authFetch.mockImplementation(async (_url: string, init?: RequestInit) => ({
    ok: true,
    json: async () =>
      init?.method === 'PUT' ? { ...config, ...JSON.parse(String(init.body)) } : config,
  }))
}

const noise = () => screen.findByLabelText('Noise cancelling') as Promise<HTMLSelectElement>

describe('Noise cancelling in the Voice calls settings', () => {
  beforeEach(() => {
    authFetch.mockReset()
    restartCall.mockClear()
    serve(CONFIG)
  })
  afterEach(cleanup)

  it('offers all three, starting on what is saved', async () => {
    render(<Host />)
    const select = await noise()
    expect(select.value).toBe('standard')
    expect([...select.options].map((o) => o.value)).toEqual(['off', 'standard', 'isolation'])
  })

  it('moves the estimate as soon as an option is picked, before saving', async () => {
    render(<Host />)
    fireEvent.change(await noise(), { target: { value: 'isolation' } })
    expect(screen.getByText(/About the first 100 use voice isolation/)).toBeTruthy()
    expect(screen.getByText(/also removes other people talking/)).toBeTruthy()
    fireEvent.change(await noise(), { target: { value: 'off' } })
    expect(screen.queryByText(/About the first 100 use voice isolation/)).toBeNull()
    expect(screen.getByText('Estimated free minutes: about 170 a month')).toBeTruthy()
  })

  it('moves the estimate with the other settings too', async () => {
    render(<Host />)
    await noise()
    fireEvent.change(screen.getByLabelText('Speech to text'), {
      target: { value: 'assemblyai/universal-streaming' },
    })
    expect(screen.getByText('Estimated free minutes: about 200 a month')).toBeTruthy()
  })

  it('says when LiveKit refused voice isolation and new calls are on Standard', async () => {
    serve({
      ...CONFIG,
      noise_filter: 'isolation',
      isolation_standard_until: '2099-12-01T00:00:00+00:00',
    })
    render(<Host />)
    await noise()
    expect(screen.getByText(/LiveKit refused voice isolation/)).toBeTruthy()
    fireEvent.change(await noise(), { target: { value: 'standard' } })
    expect(screen.queryByText(/LiveKit refused voice isolation/)).toBeNull()
  })

  it('saves the choice and restarts a call that is on', async () => {
    render(<Host />)
    fireEvent.change(await noise(), { target: { value: 'isolation' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(restartCall).toHaveBeenCalledTimes(1))
    const put = authFetch.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(String(put?.[1]?.body)).noise_filter).toBe('isolation')
  })

  it('shows neither the option nor a free-plan estimate on a server of your own', async () => {
    serve({ ...CONFIG, livekit_url: 'wss://livekit.example.com' })
    render(<Host />)
    await screen.findByLabelText('Speech to text')
    expect(screen.queryByLabelText('Noise cancelling')).toBeNull()
    expect(screen.queryByText(/Estimated free minutes/)).toBeNull()
  })
})

describe('Noise cancelling missing from this install', () => {
  beforeEach(() => authFetch.mockReset())
  afterEach(cleanup)

  const missing = { ...CONFIG.worker, noise_installed: { standard: false, isolation: false } }

  it('says calls run without it, and still lets you choose', async () => {
    serve({ ...CONFIG, worker: missing })
    render(<Host />)
    const select = await noise()
    expect(await screen.findByText(/Krisp isn’t installed on this computer/)).toBeTruthy()
    expect(select.disabled).toBe(false)
  })

  it('says nothing when it is off', async () => {
    serve({ ...CONFIG, noise_filter: 'off', worker: missing })
    render(<Host />)
    await noise()
    expect(screen.queryByText(/Krisp isn’t installed/)).toBeNull()
  })

  it('says nothing when it is installed', async () => {
    const installed = { ...CONFIG.worker, noise_installed: { standard: true, isolation: true } }
    serve({ ...CONFIG, worker: installed })
    render(<Host />)
    await noise()
    expect(screen.queryByText(/Krisp isn’t installed/)).toBeNull()
  })
})
