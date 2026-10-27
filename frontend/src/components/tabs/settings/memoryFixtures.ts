// Synthetic memory facts shared by the memory UI tests — never a real row.
import type { MemoryFact } from '../../../hooks/useMemoryFacts'

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString()

let n = 0
function fact(over: Partial<MemoryFact> & { content: string }): MemoryFact {
  n += 1
  return {
    id: `f${n}`,
    state: 'live',
    source: 'extracted',
    updated_at: daysAgo(n),
    created_at: daysAgo(n),
    ...over,
  }
}

export function sampleFacts(): MemoryFact[] {
  n = 0
  return [
    fact({ section: 'you', topic: 'Household', content: 'Lives with two cats' }),
    fact({ section: 'money', topic: 'Housing', content: 'Rent is paid on the 1st' }),
    fact({ section: 'money', topic: 'Housing', content: 'Lease ends in March' }),
    fact({ section: 'money', topic: 'Housing', content: 'Landlord prefers email' }),
    fact({ section: 'money', topic: 'Housing', content: 'Deposit is two months of rent' }),
    fact({ section: 'plans', topic: 'Holiday', content: 'Saving for a trip in spring' }),
    fact({
      section: 'open_items',
      content: 'Call the insurer about the renewal',
      expires_at: '2099-01-15',
    }),
    fact({ section: 'other', topic: 'Reading', content: 'Likes science-fiction audiobooks' }),
    fact({ state: 'unsorted', content: 'Prefers metric units' }),
    fact({
      state: 'retired',
      content: 'Works as a nurse',
      retired_reason: 'profile',
      retired_at: daysAgo(10),
    }),
    fact({
      state: 'retired',
      content: 'Mentioned a gym membership once',
      retired_reason: 'unused',
      retired_at: daysAgo(45),
    }),
  ]
}
