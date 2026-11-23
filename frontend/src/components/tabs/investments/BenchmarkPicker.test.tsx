// @vitest-environment jsdom
//
// The benchmark picker (issue #223): the roster grouped by region, and any ticker on the market —
// searched, resolved, its history readied by the server, then handed back as the new benchmark.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

import { BenchmarkPicker } from './BenchmarkPicker'
import type { BenchmarkRosterItem } from '../../../types/portfolio'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const ok = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)

const ROSTER: BenchmarkRosterItem[] = [
  { key: 'SPY', label: 'SPY • S&P 500', symbol: 'SPY', group: 'US' },
  { key: 'VWRA', label: 'VWRA • Vanguard All-World', symbol: 'VWRA', group: 'World' },
  { key: 'EXS1', label: 'EXS1 • DAX', symbol: 'EXS1', group: 'Europe' },
  { key: '', label: 'No benchmark', symbol: '', group: '' },
]

function open(onPick = vi.fn()) {
  render(<BenchmarkPicker value="SPY" roster={ROSTER} onPick={onPick} />)
  fireEvent.click(screen.getByRole('button', { name: /Benchmark: vs SPY/ }))
  return onPick
}

describe('BenchmarkPicker', () => {
  it('lists the roster under its region headings and marks the current pick', () => {
    open()
    expect(screen.getByText('US')).toBeTruthy()
    expect(screen.getByText('Europe')).toBeTruthy()
    const spy = screen.getByRole('option', { name: /SPY • S&P 500/ })
    expect(spy.getAttribute('aria-selected')).toBe('true')
  })

  it('picks a roster entry by its key', () => {
    const onPick = open()
    fireEvent.click(screen.getByRole('option', { name: /EXS1 • DAX/ }))
    expect(onPick).toHaveBeenCalledWith('EXS1')
  })

  it('filters the roster as you type', () => {
    open()
    fireEvent.change(screen.getByRole('combobox', { name: 'Search benchmarks' }), {
      target: { value: 'dax' },
    })
    expect(screen.getByRole('option', { name: /EXS1 • DAX/ })).toBeTruthy()
    expect(screen.queryByRole('option', { name: /SPY/ })).toBeNull()
  })

  it('searches the market and readies a picked ticker before handing it back', async () => {
    const entry = {
      key: 'ACME.DE',
      label: 'ACME.DE • ACME Index',
      symbol: 'ACME.DE',
      group: 'Your benchmark',
    }
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/intel/assets/search')) {
        return ok([
          {
            symbol: 'ACME.DE',
            name: 'ACME Index',
            asset_class: 'equity',
            exchange: 'XETRA',
            price_source: 'yahoo',
            price_source_id: 'ACME.DE',
          },
        ])
      }
      if (url === '/api/intel/assets/resolve')
        return ok({ instrument_id: 'ins_acme', ticker: 'ACME.DE' })
      if (url === '/api/returns/benchmarks/prepare') return ok(entry)
      return ok({})
    })
    const onPick = open()
    fireEvent.change(screen.getByRole('combobox', { name: 'Search benchmarks' }), {
      target: { value: 'acme' },
    })
    const option = await screen.findByRole('option', { name: /ACME\.DE/ })
    expect(screen.getByText('Market')).toBeTruthy()
    fireEvent.click(option)
    await waitFor(() => expect(onPick).toHaveBeenCalledWith('ACME.DE', entry))
    const prepare = authFetch.mock.calls.find(([u]) => u === '/api/returns/benchmarks/prepare')!
    expect(JSON.parse((prepare[1] as RequestInit).body as string)).toEqual({
      instrument_id: 'ins_acme',
    })
  })

  it('says so when a market pick cannot be a benchmark', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/intel/assets/search')) {
        return ok([
          {
            symbol: 'ACMEX',
            name: 'ACME Unpriced',
            asset_class: 'equity',
            exchange: '',
            price_source: 'yahoo',
            price_source_id: 'ACMEX',
          },
        ])
      }
      if (url === '/api/intel/assets/resolve')
        return ok({ instrument_id: 'ins_x', ticker: 'ACMEX' })
      return Promise.resolve({
        ok: false,
        status: 422,
        json: () => Promise.resolve({}),
      } as Response)
    })
    const onPick = open()
    fireEvent.change(screen.getByRole('combobox', { name: 'Search benchmarks' }), {
      target: { value: 'acmex' },
    })
    fireEvent.click(await screen.findByRole('option', { name: /ACMEX/ }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(onPick).not.toHaveBeenCalled()
  })
})
