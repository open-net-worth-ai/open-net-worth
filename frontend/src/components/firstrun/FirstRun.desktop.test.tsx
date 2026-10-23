// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FirstRun } from './FirstRun'

const authFetch = vi.hoisted(() => vi.fn())
const showBookChoice = vi.fn<() => Promise<void>>()
vi.mock('../../api/client', () => ({ authFetch }))

beforeEach(() => {
  showBookChoice.mockReset().mockResolvedValue(undefined)
  const settings = {
    bookFolder: '/synthetic/chosen-book',
    browserUrl: null,
    background: false,
    launchAtLogin: false,
    keepAwake: false,
    notifications: false,
    watchedFolders: [],
  }
  window.onwDesktop = {
    version: 1,
    showBookChoice,
    chooseBookFolder: async () => ({ cancelled: true, opened: false }),
    chooseImportFiles: async () => ({ cancelled: true, files: [] }),
    saveExport: async () => ({ cancelled: true }),
    getSettings: async () => settings,
    updateSettings: async () => settings,
    chooseWatchedFolder: async () => settings,
    revokeWatchedFolder: async () => settings,
    onCommand: () => () => {},
    openExternal: async () => {},
    openBrowser: async () => {},
    retryStartup: async () => {},
  }
  authFetch.mockImplementation((url: string) => {
    const body =
      url === '/api/setup-status/data-location'
        ? { instance_root: settings.bookFolder, data_dir: `${settings.bookFolder}/data` }
        : url.startsWith('/api/setup-status/inspect-path')
          ? { has_book: false, accounts: 0, transactions: 0, writable: true }
          : url === '/api/model-config/providers'
            ? { catalog: [], detected_clis: {} }
            : {}
    return Promise.resolve({ ok: true, json: async () => body })
  })
})

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  delete window.onwDesktop
})

it('returns from currency to the native folder choice through Back', async () => {
  render(<FirstRun onDone={() => {}} initialStep={0} />)
  expect(screen.getByRole('heading', { name: /choose your app currency/i })).toBeTruthy()
  // The book folder was chosen natively before this screen; it is not restated here.
  await waitFor(() => expect(authFetch).toHaveBeenCalledWith('/api/setup-status/data-location'))
  expect(screen.queryByText('/synthetic/chosen-book')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Change book folder' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  await waitFor(() => expect(showBookChoice).toHaveBeenCalledOnce())
  expect(screen.queryByRole('button', { name: /^get started$/i })).toBeNull()
})

it('returns from native restore directly to the currency decision', async () => {
  render(<FirstRun onDone={() => {}} initialStep={-1} />)
  fireEvent.click(screen.getByRole('button', { name: /set up instead/i }))
  expect(screen.getByRole('heading', { name: /choose your app currency/i })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Back' })).toBeTruthy()
})

it('keeps the native restore Back action inside setup instead of opening a second greeting', () => {
  render(<FirstRun onDone={() => {}} initialStep={-1} />)
  fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(screen.getByRole('heading', { name: /choose your app currency/i })).toBeTruthy()
  expect(screen.queryByRole('button', { name: /^get started$/i })).toBeNull()
})

it('still lets a native user revisit currency from the model step', async () => {
  render(<FirstRun onDone={() => {}} initialStep={0} />)
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  await waitFor(() => expect(screen.getByText(/step 2 of 3/i)).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(screen.getByRole('heading', { name: /choose your app currency/i })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Back' })).toBeTruthy()
})

it('keeps currency visible and permits retry when native Back fails', async () => {
  showBookChoice.mockRejectedValueOnce(new Error('Unavailable'))
  render(<FirstRun onDone={() => {}} initialStep={0} />)
  fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Could not return to book folders. Try again.',
  )
  expect(screen.getByRole('heading', { name: /choose your app currency/i })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  await waitFor(() => expect(showBookChoice).toHaveBeenCalledTimes(2))
})
