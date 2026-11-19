// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { enableBankingReturnOf, enableBankingReturnUrl } from './bankPage'

const EB = { connector: 'enable_banking' }

describe('enableBankingReturnOf', () => {
  it('reads a consent return', () => {
    expect(enableBankingReturnOf({ ...EB, state: 's', code: 'c' })).toEqual({
      state: 's',
      code: 'c',
      error: '',
    })
  })

  it('reads a cancelled consent, preferring the description', () => {
    expect(
      enableBankingReturnOf({
        ...EB,
        state: 's',
        error: 'access_denied',
        error_description: 'User left',
      }),
    ).toEqual({ state: 's', code: '', error: 'User left' })
  })

  it('reads a return whose own `?…` was appended after the marker', () => {
    expect(enableBankingReturnOf({ connector: 'enable_banking?state=s', code: 'c' })).toEqual({
      state: 's',
      code: 'c',
      error: '',
    })
  })

  it('ignores anything that is not an Enable Banking return', () => {
    expect(enableBankingReturnOf({})).toBeNull()
    expect(enableBankingReturnOf({ ...EB })).toBeNull()
    expect(enableBankingReturnOf({ ...EB, code: 'c' })).toBeNull()
    expect(enableBankingReturnOf({ ...EB, state: 's' })).toBeNull()
    // Any other app's OAuth-style redirect carries a state and a code too.
    expect(enableBankingReturnOf({ state: 's', code: 'c' })).toBeNull()
    // GoCardless returns to ?connector=gocardless — never mistaken for this.
    expect(enableBankingReturnOf({ connector: 'gocardless', state: 's', code: 'c' })).toBeNull()
  })

  it('returns to a path of its own: Enable Banking refuses a redirect URL with a query', () => {
    expect(enableBankingReturnUrl()).toBe(
      `${window.location.origin}/connectors/callback/enable_banking`,
    )
  })
})
