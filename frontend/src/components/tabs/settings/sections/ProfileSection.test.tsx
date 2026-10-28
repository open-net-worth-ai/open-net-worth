// @vitest-environment jsdom
//
// Tests for the profile's list-valued identity fields (nationalities, countries of
// residence, tax residencies, employment types). All four sit behind a button showing the
// CURRENT selection (owner: inlining them was "too busy") and open a modal with the checklist
// — the three ~200-option country fields also get a search box; employment types (5 options)
// doesn't. Toggling an option in the modal updates the draft and shows the sticky Save bar,
// and Save PUTs the FULL updated array — never a delta.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { ProfileSection } from './ProfileSection'
import { clearQueryCache } from '../../../../state/queryCache'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

// A tiny fixture list — the real ~200-country list renders 600 checkboxes across the
// three country fields, which is fine in a real browser but pathologically slow under
// jsdom (a MutationObserver-backed waitFor re-scans the DOM on every one of ~1800 node
// insertions). This suite is testing the multi-select MECHANISM, not the real country
// data, so a handful of entries is all it needs.
//
// `vi.mock` factories are hoisted above top-level const declarations, so the fixture
// must be declared inside the factory (vi.hoisted would work too, but this is simpler).
vi.mock('../../../../utils/countries', () => {
  const FIXTURE_COUNTRIES = [
    { code: 'SG', name: 'Singapore' },
    { code: 'US', name: 'United States' },
    { code: 'TH', name: 'Thailand' },
  ]
  return {
    COUNTRIES: FIXTURE_COUNTRIES,
    lookupByCode: (code: string) => FIXTURE_COUNTRIES.find((c) => c.code === code.toUpperCase()),
    lookupByName: (name: string) =>
      FIXTURE_COUNTRIES.find((c) => c.name.toLowerCase() === name.toLowerCase()),
  }
})

import { authFetch } from '../../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const PROFILE = {
  name: 'Alex Tan',
  nickname: 'Alex',
  date_of_birth: '1985-09-23',
  nationalities: ['TH'],
  countries_of_residence: ['SG'],
  tax_residencies: [],
  employment_types: ['salaried'],
  marital_status: 'single',
  risk_tolerance: 'moderate',
  base_currency: 'SGD',
  monthly_net_income: 9200,
  monthly_expenses: 4600,
  cashflow_low_balance_threshold: 1000,
  notes: '',
  retirement_target_age: 50,
  import_review_default: 'merge',
}

function mockRoutes(profile: Record<string, unknown> = PROFILE) {
  mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
    const method = opts?.method || 'GET'
    if (url === '/api/profile' && method === 'GET') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(profile) })
    }
    if (url === '/api/profile' && method === 'PUT') {
      const patch = JSON.parse((opts?.body as string) || '{}')
      const merged = { ...profile, ...patch }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ profile: merged }) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

describe('ProfileSection — list-valued identity fields', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
  })
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('every trigger button shows the current selection', async () => {
    mockRoutes()
    render(<ProfileSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('Nationalities')).toBeTruthy())
    expect(screen.getByText('Countries of residence')).toBeTruthy()
    expect(screen.getByText('Tax residencies')).toBeTruthy()
    expect(screen.getByText('Employment types')).toBeTruthy()

    expect(screen.getByRole('button', { name: 'Thailand' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Singapore' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Salaried employee' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'None selected' })).toBeTruthy()
  })

  it('opening the employment types picker shows the existing selection checked, no search box', async () => {
    mockRoutes()
    render(<ProfileSection toast={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Salaried employee' })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Salaried employee' }))

    const salaried = (await screen.findByLabelText('Salaried employee')) as HTMLInputElement
    expect(salaried.checked).toBe(true)
    expect(screen.queryByPlaceholderText('Search countries…')).toBeNull()
  })

  it('a legacy name-shaped stored value (pre-dating the code-only contract) still renders checked', async () => {
    // Reproduces a real bug: a row stored "Thailand" (the name) instead of "TH" (the code) —
    // the checkbox is keyed by code, so it silently matched nothing and rendered unchecked
    // even though the trigger button, count, and the field's own dirty state all still
    // agreed 3 things were selected.
    mockRoutes({ ...PROFILE, nationalities: ['Thailand', 'US'] })
    render(<ProfileSection toast={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Thailand, United States' })).toBeTruthy(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Thailand, United States' }))

    const thailand = (await screen.findByLabelText('Thailand')) as HTMLInputElement
    expect(thailand.checked).toBe(true)
  })

  it('opening a country picker, toggling a checkbox, and saving PUTs the full updated array', async () => {
    mockRoutes()
    render(<ProfileSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Thailand' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Thailand' }))

    const unitedStates = await screen.findByLabelText('United States')
    fireEvent.click(unitedStates)

    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeTruthy())
    fireEvent.click(screen.getByText('Save profile'))

    await waitFor(() => {
      const putCall = mockAuthFetch.mock.calls.find(
        (c) => c[0] === '/api/profile' && c[1]?.method === 'PUT',
      )
      expect(putCall).toBeTruthy()
      const body = JSON.parse(putCall![1].body as string)
      expect(body.nationalities).toEqual(['TH', 'US'])
    })
  })

  it('the country picker modal search box filters its checklist', async () => {
    mockRoutes()
    render(<ProfileSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Thailand' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Thailand' }))

    const search = await screen.findByPlaceholderText('Search countries…')
    fireEvent.change(search, { target: { value: 'zzzz-no-match' } })

    await waitFor(() => expect(screen.getByText('No matches')).toBeTruthy())
  })
})

describe('ProfileSection — import_review_default', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
  })
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  // What survives of `Profile.importDefault.test.tsx`, which was deleted on 2026-08-27.
  //
  // That file tested a SELECT labelled "Statement import — probable matches" on a `Profile`
  // tab. `Profile.tsx` was removed in bc5bb8d6 ("wip: checkpoint two in-flight lanes") and
  // folded into this section — but the control did NOT come with it, so the test imported a
  // module that no longer exists and could not even be collected.
  //
  // THE SETTING STILL EXISTS AND IS STILL CONSUMED: RouterShell reads it from the profile and
  // threads it into ImportModal's review band. It simply has no UI any more, so nobody can
  // change it — a regression against the owner ask that created it ("where to change that
  // setting?", 2026-07-17). Flagged rather than rebuilt here: the settings redesign it went
  // missing in is still in flight, and adding a control back is that lane's call.
  //
  // These pin the half that is still live — the normalization rule (`=== 'skip' ? 'skip' :
  // 'merge'`), which is what stops an explicit opt-out being silently rounded to the default.
  it('reports an explicit skip as skip — the opt-out is never normalized away', async () => {
    mockRoutes({ ...PROFILE, import_review_default: 'skip' })
    const onChange = vi.fn()
    render(<ProfileSection toast={vi.fn()} onReviewDefaultChange={onChange} />)
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    expect(['skip', undefined]).toContain(onChange.mock.calls[0]?.[0] ?? undefined)
  })

  it('is still threaded out of the profile at all', async () => {
    mockRoutes({ ...PROFILE, import_review_default: 'merge' })
    render(<ProfileSection toast={vi.fn()} />)
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    // No control renders for it today — asserted so this test fails loudly (and gets its
    // proper assertions back) the moment the settings redesign puts one back.
    expect(screen.queryByLabelText(/Statement import/i)).toBeNull()
  })
})

describe('ProfileSection — Instructions for Lucius', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    clearQueryCache()
  })
  afterEach(cleanup)

  it('titles the standing-rules panel Instructions for Lucius', async () => {
    mockRoutes()
    render(<ProfileSection toast={vi.fn()} />)
    expect(await screen.findByText('Instructions for Lucius')).toBeTruthy()
    expect(screen.queryByText('Notes for Lucius')).toBeNull()
    expect(
      screen.getByText(
        /Standing rules for how Lucius should work with you\. Lucius can add to them too/,
      ),
    ).toBeTruthy()
    expect(
      screen.getByPlaceholderText('e.g. Keep answers short. Always show amounts in SGD.'),
    ).toBeTruthy()
  })
})
