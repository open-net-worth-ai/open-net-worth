// The Settings cost period picker's window and the query each cost endpoint is asked with.
// Shared by the AI spend panel and the TradingAgents panel so both read the SAME calendar days
// ("7 days" is today and the six before it, in this browser's zone), never `days=N`.

export type CostWindow = 'today' | 'yesterday' | '7' | '30' | '0'

export function localDateKey(daysAgo = 0): string {
  const date = new Date()
  date.setHours(12, 0, 0, 0)
  date.setDate(date.getDate() - daysAgo)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function costWindowQuery(window: CostWindow, timezone: string): string {
  if (window === 'today' || window === 'yesterday') return `range=${window}&tz=${timezone}`
  if (window === '7' || window === '30') {
    return `start=${localDateKey(Number(window) - 1)}&end=${localDateKey()}&tz=${timezone}`
  }
  return `days=0&tz=${timezone}`
}
