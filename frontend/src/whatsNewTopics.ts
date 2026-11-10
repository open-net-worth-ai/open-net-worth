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
  type LucideIcon,
} from 'lucide-react'

// Each highlight gets an icon from its own words; anything unrecognised is simply "new".
const TOPICS: { test: RegExp; icon: LucideIcon; hue: string }[] = [
  {
    test: /^(\w+:\s*)?(fix|fixed|fixes|corrected|resolved)\b/i,
    icon: Wrench,
    hue: 'var(--good)',
  },
  {
    test: /\b(connectors?|plaid|mcp|bank links?|brokers?|ibkr|interactive brokers|coinbase|kraken|robinhood)\b/i,
    icon: Plug,
    hue: 'var(--ibkr)',
  },
  { test: /\b(budget|envelopes?|ready to assign|assign)/i, icon: PiggyBank, hue: 'var(--apt)' },
  { test: /\b(import|statements?|pdfs?|csvs?)\b/i, icon: FileUp, hue: 'var(--ibkr)' },
  {
    test: /\b(invest\w*|holdings?|portfolios?|stocks?|lots?)\b/i,
    icon: TrendingUp,
    hue: 'var(--bond)',
  },
  {
    test: /\b(lucius|chat|conversations?|assistant)\b/i,
    icon: MessageCircle,
    hue: 'var(--crypto)',
  },
  {
    test: /\b(accounts?|transactions?|net worth|ledger)\b/i,
    icon: Landmark,
    hue: 'var(--recv)',
  },
  { test: /\b(phones?|iphone|android|mobile)\b/i, icon: Smartphone, hue: 'var(--muted)' },
  // Highlights rarely open with "Fixed"; they say what no longer goes wrong. Checked last, so a
  // feature that also mentions a side fix keeps its area's icon.
  { test: /\bno longer\b/i, icon: Wrench, hue: 'var(--good)' },
]

export function topicOf(item: string): { icon: LucideIcon; hue: string } {
  return TOPICS.find((topic) => topic.test.test(item)) ?? { icon: Sparkles, hue: 'var(--accent)' }
}
