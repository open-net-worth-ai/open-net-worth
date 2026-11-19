import { describe, expect, it } from 'vitest'
import type { Automation } from '../../../api/automations'
import { activeAutoCopies } from './autoCopy'

const auto = (id: string, instructions: string, status = 'active') =>
  ({ id, instructions, status }) as unknown as Automation

describe('activeAutoCopies', () => {
  it("finds the running Auto-copy of exactly this portfolio, not a longer name's or a paused one", () => {
    const list = [
      auto('a', 'Copy the followed portfolio ACME Capital in my broker account X.'),
      auto('b', 'Copy the followed portfolio ACME Capital Partners in my broker account X.'),
      auto('c', 'Copy the followed portfolio ACME Capital in my broker account Y.', 'paused'),
      auto('d', 'Summarise ACME Capital news every Monday.'),
    ]
    expect(activeAutoCopies(list, 'ACME Capital').map((a) => a.id)).toEqual(['a'])
  })
})
