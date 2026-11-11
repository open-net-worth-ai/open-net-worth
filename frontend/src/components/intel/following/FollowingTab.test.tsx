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
import {
  DIRECTORY,
  fund,
  installServer,
  json,
  member,
  performance,
  type Server,
} from './followingTestKit'

const mockFetch = authFetch as ReturnType<typeof vi.fn>

let server: Server
const onAskLucius = vi.fn()
function setup(follows = [fund(), member()], extra: Partial<Server> = {}) {
  server = { follows, directory: DIRECTORY, ...extra }
  installServer(mockFetch, server)
  return render(<FollowingTab onAskLucius={onAskLucius} />)
}

/** Refresh and Unfollow live in the hero's "More actions" menu. */
async function pick(item: string) {
  fireEvent.click(screen.getByRole('button', { name: /^More actions for/ }))
  fireEvent.click(await screen.findByRole('menuitem', { name: item }))
}

const calls = (method: string, part: string) =>
  mockFetch.mock.calls.filter(
    ([url, init]) =>
      String(url).includes(part) && ((init as RequestInit)?.method ?? 'GET') === method,
  )
const region = (name: string) => screen.getByRole('region', { name })

beforeEach(() => {
  clearQueryCache()
  navigate.mockReset()
  onAskLucius.mockReset()
})
afterEach(() => {
  cleanup()
  mockFetch.mockReset()
})

describe('nothing followed yet', () => {
  it('is a gallery of portfolios to pick, and following one opens it', async () => {
    setup([])
    const gallery = await screen.findByRole('region', { name: COPY.galleryTitle })
    const tile = (await within(gallery).findByText('Borealis Partners')).closest('li')!
    fireEvent.click(within(tile).getByRole('button', { name: COPY.followAction }))
    await waitFor(() => expect(calls('POST', '/api/followed-portfolios')).toHaveLength(1))
    expect(await screen.findByRole('heading', { name: 'Borealis Partners' })).toBeTruthy()
  })

  it('shows why a follow failed and keeps the gallery', async () => {
    setup([])
    server.followResult = { status: 502, body: { detail: 'SEC refused the request' } }
    const tile = (await screen.findByText('Borealis Partners')).closest('li')!
    fireEvent.click(within(tile).getByRole('button', { name: COPY.followAction }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(
      COPY.followFailed('Borealis Partners', 'SEC refused the request'),
    )
    expect(screen.getByText('Acme Capital')).toBeTruthy()
  })

  it('searches right in the gallery and says when nothing matches', async () => {
    setup([])
    const gallery = await screen.findByRole('region', { name: COPY.galleryTitle })
    await within(gallery).findByText('Acme Capital')
    const box = within(gallery).getByRole('searchbox', { name: COPY.searchLabel })
    fireEvent.change(box, { target: { value: 'bore' } })
    await waitFor(() => expect(within(gallery).queryByText('Acme Capital')).toBeNull())
    expect(within(gallery).getByText('Borealis Partners')).toBeTruthy()
    fireEvent.change(box, { target: { value: 'zzzz' } })
    expect(await within(gallery).findByText(COPY.noMatch('zzzz'))).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
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
    expect(await screen.findByRole('heading', { name: 'Acme Capital' })).toBeTruthy()
  })
})

describe('the hero leads with performance and the copy action', () => {
  it('shows the past 12 months against the benchmark, who it is and how old the filing is', async () => {
    setup()
    const hero = await screen.findByRole('region', { name: 'Acme Capital' })
    expect(await within(hero).findByText('+16.3%')).toBeTruthy()
    expect(within(hero).getByText(/SPY \+10\.8%/)).toBeTruthy()
    expect(within(hero).getByText(/\+5\.5 pts ahead/)).toBeTruthy()
    expect(within(hero).getByText(COPY.perfCaption['12M'])).toBeTruthy()
    expect(
      within(hero).getByText(new RegExp(`Filed ${shortDate('2026-08-14')}, 88 days ago`)),
    ).toBeTruthy()
  })

  it('links to the filing at its source and says when it was last checked', async () => {
    setup([fund({ source_url: 'https://www.sec.gov/Archives/edgar/data/1/000000000026000002/' })])
    const hero = await screen.findByRole('region', { name: 'Acme Capital' })
    const link = within(hero).getByRole('link', { name: COPY.viewFiling }) as HTMLAnchorElement
    expect(link.href).toBe('https://www.sec.gov/Archives/edgar/data/1/000000000026000002/')
    expect(link.target).toBe('_blank')
    expect(link.rel).toBe('noopener noreferrer')
    const checked = within(hero).getByText(/^· Checked /)
    expect(checked.getAttribute('title')).toContain('Checked automatically twice a day')
  })

  it('switches the window, and the holdings column follows it', async () => {
    setup([fund()])
    const hero = await screen.findByRole('region', { name: 'Acme Capital' })
    await within(hero).findByText('+16.3%')
    expect(within(region(COPY.holdingsTitle)).getByText('+29.4%')).toBeTruthy()
    expect(
      within(hero)
        .getAllByRole('radio')
        .map((r) => r.textContent),
    ).toEqual(['1M', '6M', '12M', 'Since filing'])
    fireEvent.click(within(hero).getByRole('radio', { name: '6M' }))
    expect(within(hero).getByText('+8.1%')).toBeTruthy()
    expect(within(region(COPY.holdingsTitle)).getByText('6 months')).toBeTruthy()
    fireEvent.click(within(hero).getByRole('radio', { name: 'Since filing' }))
    expect(within(hero).getByText('−1.2%')).toBeTruthy()
    expect(within(hero).getByText(/\+2\.3 pts ahead/)).toBeTruthy()
    expect(within(hero).getByText(COPY.perfCaption.FILING)).toBeTruthy()
    expect(within(region(COPY.holdingsTitle)).getByText('−2.0%')).toBeTruthy()
    expect(within(region(COPY.holdingsTitle)).getByText('Since filing')).toBeTruthy()
  })

  it('says so when prices are missing, and still shows the rest', async () => {
    setup([fund()], { performance: performance({ periods: {} }) })
    const hero = await screen.findByRole('region', { name: 'Acme Capital' })
    expect(await within(hero).findByText(COPY.perfUnavailable)).toBeTruthy()
    expect(within(region(COPY.holdingsTitle)).getByText('Acme Corp')).toBeTruthy()
  })

  it('offers a retry when performance cannot be loaded', async () => {
    setup([fund()], { performance: null })
    expect(await screen.findByText(COPY.perfErrorTitle)).toBeTruthy()
    server.performance = undefined
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await within(region('Acme Capital')).findByText(COPY.perfCaption['12M'])).toBeTruthy()
  })

  it('makes copying the one primary action and sends it to the automation template', async () => {
    setup([fund()])
    const hero = await screen.findByRole('region', { name: 'Acme Capital' })
    expect(within(hero).getByText(COPY.copyPitch)).toBeTruthy()
    const cta = within(hero).getByRole('button', { name: COPY.copyCta })
    expect(cta.className).toContain('btn-primary')
    expect(cta.className).toContain('ui-btn--sm')
    fireEvent.click(cta)
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/automations',
        search: { template: 'copy-followed-portfolio' },
      }),
    )
  })

  it('asks Lucius about the portfolio from the button beside the main one', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Acme Capital' })
    const ask = screen.getByRole('button', { name: COPY.askLabel('Acme Capital') })
    expect(ask.textContent).toContain(COPY.askLucius)
    expect(ask.className).toContain('ui-btn--sm')
    fireEvent.click(ask)
    expect(onAskLucius).toHaveBeenCalledWith(askPrompt('Acme Capital'))
  })

  it('copies one fund move through Lucius, with the trade spelled out', async () => {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    const row = within(region(COPY.movesTitle)).getAllByRole('listitem')[1]!
    fireEvent.click(within(row).getByRole('button', { name: /^Copy this trade: sold ACME/ }))
    const sent = onAskLucius.mock.calls[0]![0] as string
    expect(sent).toContain("I want to copy this trade from Acme Capital's latest 13F: sold ACME")
    expect(sent).toContain('−500K shares')
    expect(sent).toContain('for me to approve')
  })
})

describe('switcher', () => {
  it('shows each follow with its past 12 months and switches when another is picked', async () => {
    setup()
    const tab = await screen.findByRole('tab', { name: /Pat Placeholder/ })
    await waitFor(() => expect(tab.textContent).toContain('+16.3%'))
    fireEvent.click(tab)
    expect(await screen.findByRole('heading', { name: /Pat Placeholder/ })).toBeTruthy()
    expect(tab.getAttribute('aria-selected')).toBe('true')
  })
})

describe('what a fund did and what it holds', () => {
  it('lists the moves biggest first with size, share change and the period', async () => {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    const moves = region(COPY.movesTitle)
    expect(
      within(moves).getByText(`${shortDate('2026-03-31')} to ${shortDate('2026-06-30')}`),
    ).toBeTruthy()
    const rows = within(moves).getAllByRole('listitem')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Sold out · −$96M'),
      expect.stringContaining('−$15M'),
      expect.stringContaining('+$2M'),
    ])
    expect(rows[1]!.textContent).toContain('−500K shares (-33.33%)')
    expect(rows[1]!.getAttribute('title')).toContain('no trade dates or trade prices')
    expect(within(moves).getByText(COPY.changeBasis)).toBeTruthy()
  })

  it('ranks the holdings with weight and says what was left out', async () => {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    const holdings = region(COPY.holdingsTitle)
    const rows = within(holdings).getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(rows[0]!.textContent).toContain('ACME')
    expect(rows[0]!.textContent).toContain('22.40%')
    expect(
      within(holdings).getByText(new RegExp(`${COPY.unmatched(1)} Mystery Trust \\(4\\.10%\\)`)),
    ).toBeTruthy()
  })
})

describe('a House member', () => {
  it('is labelled estimated and lists trades newest first with the stock since', async () => {
    setup([member()])
    expect(await screen.findByRole('heading', { name: /Pat Placeholder/ })).toBeTruthy()
    expect(screen.getAllByText(COPY.estimated).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(COPY.estimatedSubtitle)).toBeTruthy()
    expect(screen.getByText(COPY.unreadReports(2))).toBeTruthy()
    const rows = within(region(COPY.tradesTitle)).getAllByRole('listitem')
    expect(rows[0]!.textContent).toContain('Muni Bond 2030')
    expect(rows[0]!.textContent).toContain(COPY.notCounted)
    expect(rows[1]!.textContent).toContain('Bought NOVA')
    await waitFor(() => expect(rows[1]!.textContent).toContain('+10.2%'))
    expect(rows[2]!.textContent).toContain('Sold OLDR')
    expect(within(rows[0]!).queryByRole('button')).toBeNull()
    fireEvent.click(within(rows[1]!).getByRole('button', { name: /^Copy this trade: bought NOVA/ }))
    expect(onAskLucius.mock.calls[0]![0]).toContain(
      'I want to copy this trade from Pat Placeholder: bought NOVA (Nova Systems) on',
    )
    expect(screen.queryByRole('region', { name: COPY.movesTitle })).toBeNull()
  })
})

describe('states', () => {
  it('keeps the model above a failed update and says so', async () => {
    setup([fund({ status: 'error', error: 'SEC did not answer' })])
    expect(
      await screen.findByText(/We couldn't update this portfolio: SEC did not answer/),
    ).toBeTruthy()
    expect(within(region(COPY.holdingsTitle)).getByText('Crux Holdings')).toBeTruthy()
    expect(screen.getAllByText(COPY.couldNotUpdate).length).toBeGreaterThan(0)
  })

  it('explains a stale filing, and offers Refresh when nothing could be read', async () => {
    setup([fund({ status: 'stale' })])
    expect(await screen.findByText(/it is 88 days old/)).toBeTruthy()
    cleanup()
    clearQueryCache()
    setup([fund({ status: 'error', error: 'bad parse', model: null })])
    expect(await screen.findByText(COPY.loadFailedTitle)).toBeTruthy()
    expect(screen.getByText('bad parse')).toBeTruthy()
    expect(screen.queryByRole('button', { name: COPY.copyCta })).toBeNull()
  })
})

describe('refresh and unfollow', () => {
  it('refreshes the selected follow and re-reads the list', async () => {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    const listReads = () =>
      calls('GET', '/api/followed-portfolios').filter(([u]) => u === '/api/followed-portfolios')
        .length
    const before = listReads()
    await pick(COPY.refresh)
    await waitFor(() => expect(calls('POST', '/fol_fund/refresh')).toHaveLength(1))
    await waitFor(() => expect(listReads()).toBeGreaterThan(before))
  })

  it('shows the error when a refresh cannot be sent', async () => {
    setup([fund()])
    await screen.findByRole('heading', { name: 'Acme Capital' })
    const original = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) =>
      String(url).endsWith('/refresh') ? json({ detail: 'try later' }, 503) : original(url, init),
    )
    await pick(COPY.refresh)
    expect(await screen.findByText(COPY.refreshFailed('try later'))).toBeTruthy()
  })

  it('removes a follow only after the confirmation, then shows the next one', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Acme Capital' })
    await pick(COPY.unfollow)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(calls('DELETE', '/fol_fund')).toHaveLength(0)
    await pick(COPY.unfollow)
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
    const created = fund({ id: 'fol_new', name: 'Borealis Partners' })
    server.follows.push(created)
    release(json({ follow: created, new_filing: true }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await screen.findByRole('heading', { name: 'Borealis Partners' })).toBeTruthy()
  })

  it('closes from the footer button', async () => {
    const dialog = await openDialog()
    fireEvent.click(within(dialog).getAllByRole('button', { name: COPY.close }).at(-1)!)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})

describe('copy', () => {
  it('names the action for what the user gets, not for where it lives', () => {
    expect(COPY.copyCta).toBe('Start copy trading')
    expect(
      Object.values(COPY)
        .filter((v) => typeof v === 'string')
        .join(' '),
    ).not.toMatch(/in Automations/)
  })
})
