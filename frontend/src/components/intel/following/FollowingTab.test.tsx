// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => false }))

import { authFetch } from '../../../api/client'
import { clearQueryCache } from '../../../state/queryCache'
import { FollowingTab } from './FollowingTab'
import { COPY, askPrompt } from './following.copy'
import { shortDate } from './following.format'
import { DIRECTORY, fund, installServer, json, member, type Server } from './followingTestKit'

const mockFetch = authFetch as ReturnType<typeof vi.fn>

let server: Server
const onAskLucius = vi.fn()
function setup(follows = [fund(), member()]) {
  server = {
    follows,
    directory: DIRECTORY,
  }
  installServer(mockFetch, server)
  return render(<FollowingTab onAskLucius={onAskLucius} />)
}

const calls = (method: string, part: string) =>
  mockFetch.mock.calls.filter(
    ([url, init]) =>
      String(url).includes(part) && ((init as RequestInit)?.method ?? 'GET') === method,
  )

beforeEach(() => {
  clearQueryCache()
  navigate.mockReset()
  onAskLucius.mockReset()
})
afterEach(() => {
  cleanup()
  mockFetch.mockReset()
})

describe('empty and error states', () => {
  it('invites a first follow and opens the dialog with the featured list', async () => {
    setup([])
    expect(await screen.findByText(COPY.emptyBody)).toBeTruthy()
    expect(screen.getByText(COPY.emptyTitle)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: COPY.follow }))
    expect(await screen.findByRole('dialog')).toBeTruthy()
    expect(await screen.findByText(COPY.featured)).toBeTruthy()
    expect(await screen.findByText('Borealis Partners')).toBeTruthy()
  })

  it('shows an error that Retry recovers from', async () => {
    setup([fund()])
    server.failList = true
    clearQueryCache()
    cleanup()
    render(<FollowingTab />)
    expect(await screen.findByText(COPY.listErrorTitle)).toBeTruthy()
    server.failList = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect((await screen.findAllByText('Acme Capital')).length).toBeGreaterThan(0)
    expect(screen.queryByText(COPY.listErrorTitle)).toBeNull()
  })
})

describe('list and detail', () => {
  it('shows a fund with its filing date and age, and switches when another chip is picked', async () => {
    setup()
    expect(await screen.findByRole('heading', { name: 'Acme Capital' })).toBeTruthy()
    const hero = screen.getByRole('region', { name: 'Acme Capital' })
    expect(within(hero).getByText(shortDate('2026-08-14'))).toBeTruthy()
    expect(within(hero).getByText('88 days ago')).toBeTruthy()
    expect(within(hero).getByText('1 new · 1 added · 1 trimmed · 1 sold out')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: /Pat Placeholder/ }))
    expect(await screen.findByRole('heading', { name: /Pat Placeholder/ })).toBeTruthy()
    expect(screen.getByRole('tab', { name: /Pat Placeholder/ }).getAttribute('aria-selected')).toBe(
      'true',
    )
  })

  it('filters the holdings by change and lists the sold-out and unmatched disclosures', async () => {
    setup([fund()])
    expect(await screen.findByText('Crux Holdings')).toBeTruthy()
    expect(screen.getByText('22.40%')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: /^New\s*1$/ }))
    expect(screen.getByText('Crux Holdings')).toBeTruthy()
    expect(screen.queryByText('Acme Corp')).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: /^All\s*4$/ }))
    expect(screen.getByText('Acme Corp')).toBeTruthy()
    expect(screen.getByText(COPY.exited)).toBeTruthy()
    expect(screen.getByText(/Oxyz Energy/)).toBeTruthy()
    expect(
      screen.getByText(new RegExp(`${COPY.unmatched(1)} Mystery Trust \\(4\\.10%\\)`)),
    ).toBeTruthy()
  })

  it('labels a House member estimated, newest trade first, and counts unread reports', async () => {
    setup([member()])
    expect(await screen.findByRole('heading', { name: /Pat Placeholder/ })).toBeTruthy()
    expect(screen.getAllByText(COPY.estimated).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(new RegExp(COPY.estimatedSubtitle))).toBeTruthy()
    expect(screen.getByText(COPY.unreadReports(2))).toBeTruthy()
    const rows = within(screen.getByRole('region', { name: COPY.tradesTitle })).getAllByRole(
      'article',
    )
    expect(rows[0]!.textContent).toContain('Muni Bond 2030')
    expect(rows[0]!.textContent).toContain(COPY.notCounted)
    expect(rows[2]!.textContent).toContain('OLDR')
  })

  it('keeps the model above a failed update and says so', async () => {
    setup([fund({ status: 'error', error: 'SEC did not answer' })])
    expect(
      await screen.findByText(/We couldn't update this portfolio: SEC did not answer/),
    ).toBeTruthy()
    expect(screen.getByText('Crux Holdings')).toBeTruthy()
    expect(screen.getAllByText(COPY.couldNotUpdate).length).toBeGreaterThan(0)
  })

  it('explains a stale filing and shows an empty model with Refresh', async () => {
    setup([fund({ status: 'stale' })])
    expect(await screen.findByText(/it is 88 days old/)).toBeTruthy()
    cleanup()
    clearQueryCache()
    setup([fund({ status: 'error', error: 'bad parse', model: null })])
    expect(await screen.findByText(COPY.loadFailedTitle)).toBeTruthy()
    expect(screen.getByText('bad parse')).toBeTruthy()
  })
})

describe('refresh and unfollow', () => {
  it('refreshes the selected follow and re-reads the list', async () => {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    const before = calls('GET', '/api/followed-portfolios').filter(
      ([u]) => u === '/api/followed-portfolios',
    ).length
    fireEvent.click(screen.getByRole('button', { name: COPY.refresh }))
    await waitFor(() => expect(calls('POST', '/fol_fund/refresh')).toHaveLength(1))
    await waitFor(() =>
      expect(
        calls('GET', '/api/followed-portfolios').filter(([u]) => u === '/api/followed-portfolios')
          .length,
      ).toBeGreaterThan(before),
    )
  })

  it('shows the error when a refresh cannot be sent', async () => {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    mockFetch.mockImplementationOnce(async () => json({ detail: 'try later' }, 503))
    fireEvent.click(screen.getByRole('button', { name: COPY.refresh }))
    expect(await screen.findByText(COPY.refreshFailed('try later'))).toBeTruthy()
  })

  it('removes a follow only after the confirmation, then selects the next one', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Acme Capital' })
    fireEvent.click(screen.getByRole('button', { name: COPY.unfollow }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(calls('DELETE', '/fol_fund')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: COPY.unfollow }))
    expect(screen.getByText(COPY.unfollowTitle('Acme Capital'))).toBeTruthy()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: COPY.unfollow }))
    await waitFor(() => expect(calls('DELETE', '/fol_fund')).toHaveLength(1))
    expect(await screen.findByRole('heading', { name: /Pat Placeholder/ })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Acme Capital' })).toBeNull()
  })
})

describe('follow dialog', () => {
  async function openDialog() {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    fireEvent.click(screen.getByRole('button', { name: COPY.follow }))
    return screen.findByRole('dialog')
  }

  it('searches the directory and restores the featured list when cleared', async () => {
    const dialog = await openDialog()
    await within(dialog).findByText('Borealis Partners')
    fireEvent.change(within(dialog).getByRole('searchbox'), { target: { value: 'bore' } })
    expect(await within(dialog).findByText(COPY.results('bore'))).toBeTruthy()
    await waitFor(() => expect(within(dialog).queryByText('Pat Placeholder')).toBeNull())
    fireEvent.change(within(dialog).getByRole('searchbox'), { target: { value: '' } })
    expect(await within(dialog).findByText(COPY.featured)).toBeTruthy()
  })

  it('says so when nothing matches', async () => {
    const dialog = await openDialog()
    fireEvent.change(within(dialog).getByRole('searchbox'), { target: { value: 'zzzz' } })
    expect(await within(dialog).findByText(COPY.noMatch('zzzz'))).toBeTruthy()
  })

  it('marks an already followed result and offers no Follow button for it', async () => {
    const dialog = await openDialog()
    await within(dialog).findByText('Pat Placeholder')
    expect(within(dialog).getByText(COPY.following)).toBeTruthy()
    expect(within(dialog).getAllByRole('button', { name: COPY.followAction })).toHaveLength(2)
  })

  it('shows the filing fetch while following, then selects the new follow and closes', async () => {
    const dialog = await openDialog()
    let release: (r: Response) => void = () => {}
    const pending = new Promise<Response>((resolve) => (release = resolve))
    const original = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) =>
      init?.method === 'POST' ? pending : original(url, init),
    )
    const borealis = (await within(dialog).findByText('Borealis Partners')).closest('li')!
    fireEvent.click(within(borealis).getByRole('button', { name: COPY.followAction }))
    expect(await within(dialog).findByText(COPY.fetching)).toBeTruthy()
    expect(within(borealis).getByRole('button', { name: COPY.followBusy })).toBeTruthy()
    const created = fund({ id: 'fol_new', name: 'Borealis Partners' })
    server.follows.push(created)
    release(json({ follow: created, new_filing: true }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await screen.findByRole('heading', { name: 'Borealis Partners' })).toBeTruthy()
  })

  it('shows the server reason on failure, keeps the list and lets the user retry', async () => {
    const dialog = await openDialog()
    server.followResult = { status: 502, body: { detail: 'SEC refused the request' } }
    const borealis = (await within(dialog).findByText('Borealis Partners')).closest('li')!
    fireEvent.click(within(borealis).getByRole('button', { name: COPY.followAction }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert.textContent).toBe(
      COPY.followFailed('Borealis Partners', 'SEC refused the request'),
    )
    expect(screen.getAllByText('Acme Capital').length).toBeGreaterThan(0)
    server.followResult = undefined
    fireEvent.click(within(borealis).getByRole('button', { name: COPY.tryAgain }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('closes from the footer button', async () => {
    const dialog = await openDialog()
    fireEvent.click(within(dialog).getAllByRole('button', { name: COPY.close }).at(-1)!)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})

describe('copying lives in Automations', () => {
  it('deep-links the template and says this screen places nothing', async () => {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    expect(screen.getByText(new RegExp(COPY.notAdvice))).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: COPY.setUpAutomation }))
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/automations',
        search: { template: 'copy-followed-portfolio' },
      }),
    )
  })

  it('asks Lucius about the selected follow', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Acme Capital' })
    fireEvent.click(screen.getByRole('button', { name: COPY.askLucius }))
    expect(onAskLucius).toHaveBeenCalledWith(askPrompt('Acme Capital'))
    expect(onAskLucius.mock.calls[0]![0]).toBe(
      'What does Acme Capital hold, what changed in the latest filing, and how does it compare with my own portfolio?',
    )
  })

  it('has no order or plan controls', () => {
    expect(Object.keys(COPY).some((k) => /mirror|sleeve|plan|order/i.test(k))).toBe(false)
  })
})
