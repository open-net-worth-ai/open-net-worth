// @vitest-environment jsdom
//
// CDS-UI-1/2 (docs/qa/acceptance/connectors-data-sources.md): a cross-cutting regression guard
// over all 8 provider config sections — none of them may render "Sync now" or fire a request
// to a `*/sync` URL. Sync now the mechanism is gone; fetching is a Lucius turn now.
//
// The one deliberate exception (owner decision, 2026-10-20) lives on the Imports & sync page, not
// on a connector card: YNAB's scheduled two-way sync has its own "Sync now" under
// /api/ynab/sync (imports/SyncPanel.test.tsx). The YNAB card only READS that status for its
// one-line summary, so for YNAB exactly that one GET is allowed, and no button.

import type { JSX } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { PlaidSection } from './PlaidSection'
import { SimpleFinSection } from './SimpleFinSection'
import { LunchMoneySection } from './LunchMoneySection'
import { LunchFlowSection } from './LunchFlowSection'
import { YnabSection } from './YnabSection'
import { EnableBankingSection } from './EnableBankingSection'
import { AlpacaSection } from './AlpacaSection'
import { IbkrSection } from './IbkrSection'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

// Post-L2 shape: no `stats`/`linked_accounts` on any provider's public() — a fetch tool's own
// result carries counts now; nothing here is a persisted running total.
const STATUS_BY_PATH: Record<string, unknown> = {
  '/api/plaid/status': {
    configured: true,
    enabled: true,
    environment: 'sandbox',
    has_secret: true,
    client_id: 'x',
    has_access_token: true,
    last_synced_at: null,
    last_status: 'ok',
    last_error: null,
  },
  '/api/simplefin/status': {
    configured: true,
    enabled: true,
    has_access_url: true,
    last_synced_at: null,
    last_status: 'ok',
    last_error: null,
  },
  '/api/lunch-money/status': {
    configured: true,
    enabled: true,
    has_access_token: true,
    last_synced_at: null,
    last_status: 'ok',
    last_error: null,
  },
  '/api/lunch-flow/status': {
    configured: true,
    enabled: true,
    has_credentials: true,
    client_id: 'x',
    email: '',
    has_link: true,
    user_id: 'u1',
    last_synced_at: null,
    last_status: 'ok',
    last_error: null,
    last_caution: null,
    connections: [],
  },
  '/api/ynab/status': {
    configured: true,
    enabled: true,
    has_access_token: true,
    budget_id: null,
    budget_name: null,
    budget_currency: null,
    last_synced_at: null,
    last_status: 'ok',
    last_error: null,
  },
  '/api/ynab/sync': {
    token_saved: true,
    enabled: false,
    interval: '1h',
    intervals: ['15m', '30m', '1h', '6h', 'daily', 'manual'],
    plan_id: null,
    plan_name: null,
    running: false,
    last_run: null,
    next_run_at: null,
    conflicts: [],
  },
  '/api/enable-banking/status': {
    configured: true,
    enabled: true,
    has_credentials: true,
    application_id: 'x',
    aspsp: '',
    country: 'FI',
    has_session: false,
    last_synced_at: null,
    last_status: 'ok',
    last_error: null,
  },
  '/api/alpaca/status': {
    configured: true,
    enabled: true,
    environment: 'paper',
    has_keys: true,
    last_synced_at: null,
    last_status: 'ok',
    last_error: null,
    cash: null,
    buying_power: null,
  },
  '/api/ibkr/status': {
    configured: true,
    has_token: true,
    flex_query_id: '123',
    enabled: true,
    last_synced_at: null,
    last_status: 'ok',
    last_error: null,
  },
}

const SECTIONS: Array<{ name: string; provider: string; Component: () => JSX.Element }> = [
  { name: 'Plaid', provider: 'plaid', Component: PlaidSection },
  { name: 'SimpleFIN', provider: 'simplefin', Component: SimpleFinSection },
  { name: 'Lunch Money', provider: 'lunch_money', Component: LunchMoneySection },
  { name: 'Lunch Flow', provider: 'lunch_flow', Component: LunchFlowSection },
  { name: 'YNAB', provider: 'ynab', Component: YnabSection },
  { name: 'Enable Banking', provider: 'enable_banking', Component: () => <EnableBankingSection /> },
  { name: 'Alpaca', provider: 'alpaca', Component: AlpacaSection },
  { name: 'Interactive Brokers', provider: 'ibkr', Component: IbkrSection },
]

describe.each(SECTIONS)(
  '$name — no Sync now, no */sync request (CDS-UI-1/2)',
  ({ provider, Component }) => {
    it('renders with no "Sync now" text and issues no /sync request', async () => {
      mockAuthFetch.mockReset()
      mockAuthFetch.mockImplementation((url: string) => {
        if (STATUS_BY_PATH[url]) return Promise.resolve(okJson(STATUS_BY_PATH[url]))
        if (url === '/api/ynab/budgets') return Promise.resolve(okJson({ budgets: [] }))
        if (url.startsWith('/api/connectors/'))
          return Promise.resolve(okJson({ status: 'ok', accounts: [] }))
        if (url === '/api/accounts') return Promise.resolve(okJson([]))
        return Promise.resolve(okJson({}))
      })

      render(<Component />)

      await waitFor(() => expect(screen.getByText('Accounts')).toBeTruthy())
      expect(screen.queryByRole('button', { name: /sync now/i })).toBeNull()
      expect(screen.queryByText(/sync now/i)).toBeNull()
      expect(screen.queryByText(/poll interval/i)).toBeNull()
      const syncCalls = mockAuthFetch.mock.calls.filter(
        (c) => String(c[0]).includes('/sync') && String(c[0]) !== '/api/ynab/sync',
      )
      expect(syncCalls).toEqual([])
      // Sanity: the status endpoint for THIS provider was actually hit (proves the mock wired
      // through and the assertion above isn't vacuously true from an early error state).
      expect(
        mockAuthFetch.mock.calls.some(
          (c) => String(c[0]) === `/api/${provider.replace('_', '-')}/status`,
        ),
      ).toBe(true)
    })
  },
)
