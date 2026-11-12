// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { enableBankingReturnOf, enableBankingReturnUrl } from './bankPage'

describe('enableBankingReturnOf', () => {
  it('reads a consent return', () => {
    expect(enableBankingReturnOf({ state: 's', code: 'c' })).toEqual({
      state: 's',
      code: 'c',
      error: '',
    })
  })

  it('reads a cancelled consent, preferring the description', () => {
    expect(
      enableBankingReturnOf({ state: 's', error: 'access_denied', error_description: 'User left' }),
    ).toEqual({ state: 's', code: '', error: 'User left' })
  })

  it('ignores anything that is not an Enable Banking return', () => {
    expect(enableBankingReturnOf({})).toBeNull()
    expect(enableBankingReturnOf({ code: 'c' })).toBeNull()
    expect(enableBankingReturnOf({ state: 's' })).toBeNull()
    // GoCardless returns to ?connector=gocardless — never mistaken for this.
    expect(enableBankingReturnOf({ connector: 'gocardless', state: 's', code: 'c' })).toBeNull()
  })

  it('returns to the bare page: Enable Banking matches the registered URL exactly', () => {
    expect(enableBankingReturnUrl()).toBe(`${window.location.origin}/connectors`)
  })
})
