import { describe, expect, it } from 'vitest'
import {
  FileUp,
  Landmark,
  MessageCircle,
  PiggyBank,
  Plug,
  Smartphone,
  Sparkles,
  TrendingUp,
  Wrench,
} from 'lucide-react'
import { topicOf } from './whatsNewTopics'

const icon = (text: string) => topicOf(text).icon

describe('whatsNewTopics', () => {
  it('reads a fix from its opening word, with or without an area label', () => {
    expect(icon('Fixed a Dashboard net worth that showed the old currency')).toBe(Wrench)
    expect(icon('Budget: fixed the carry-over at month end')).toBe(Wrench)
    expect(icon('Corrected the import of split rows')).toBe(Wrench)
    expect(icon('Resolved a crash on launch')).toBe(Wrench)
    expect(icon('The keyboard no longer pushes Settings off the screen')).toBe(Wrench)
    expect(icon('Split transactions show by payee, and a memo is no longer dropped')).toBe(Landmark)
  })

  it('picks the first area the text names, in a fixed order', () => {
    expect(icon('Covering an overspent envelope shows its cost')).toBe(PiggyBank)
    expect(icon('Import reads multi-page PDF statements')).toBe(FileUp)
    expect(icon('Import transactions from your accounts')).toBe(FileUp)
    expect(icon('Holdings show today’s move')).toBe(TrendingUp)
    expect(icon('Ask Lucius to compare months')).toBe(MessageCircle)
    expect(icon('Split one transaction across categories')).toBe(Landmark)
    expect(icon('Connect several banks through one Plaid connector')).toBe(Plug)
    expect(icon('Kraken and Coinbase are now in the connector directory')).toBe(Plug)
    expect(icon('The installed iPhone app opens faster')).toBe(Smartphone)
  })

  it('matches whole words only, so a word inside another word is not a topic', () => {
    expect(icon('A new time slot picker')).toBe(Sparkles)
    expect(icon('Pilot mode for reminders')).toBe(Sparkles)
    expect(icon('A chatty tooltip is quieter')).toBe(Sparkles)
    expect(icon('A prefix for every reminder')).toBe(Sparkles)
  })

  it('anything unrecognised is simply new', () => {
    expect(icon('Dark mode follows your system')).toBe(Sparkles)
  })
})
