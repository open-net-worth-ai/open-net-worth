// Formatters for the Notable Price Movement timeline — kept out of the component file so it
// exports components only (react-refresh) and so they're unit-testable on their own.
import { fDate } from '../../utils/format'

/** "12:21 AM ET" — the instant in the venue's own timezone (falls back to the viewer's). */
export function clockIn(iso: string | null, tz: string | null, tzShort: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const opts: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' }
  let text: string
  try {
    text = d.toLocaleTimeString('en-US', tz ? { ...opts, timeZone: tz } : opts)
  } catch {
    text = d.toLocaleTimeString('en-US', opts)
  }
  // Non-breaking inside the clock ("10:14 AM" never splits); the ONE breakable space sits
  // before the tz label, so a narrow date column wraps to "10:14 AM" / "CEST" and nothing else.
  const clock = text.replace(/\s+/g, '\u00A0')
  return tzShort ? `${clock} ${tzShort}` : clock
}

/** The paragraph's own timestamp beside the sources pill — the reader's cue that a paragraph
 *  may predate what the row now shows (owner ask 2026-09-21). Relative inside 24 hours
 *  ("Generated 3 hours ago"); beyond that the venue clock, with the date when the paragraph
 *  was written on a different exchange-local day than the session it describes
 *  ("Generated Sep 19, 7:02 AM ET"). "" without a stamp. `now` is injectable for tests. */
export function generatedLabel(
  iso: string | null,
  sessionDate: string,
  tz: string | null,
  tzShort: string | null,
  now: number = Date.now(),
): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const ageMin = Math.floor((now - t) / 60_000)
  if (ageMin >= 0 && ageMin < 24 * 60) {
    if (ageMin < 1) return 'Generated just now'
    if (ageMin < 60) return `Generated ${ageMin} min ago`
    const h = Math.floor(ageMin / 60)
    return `Generated ${h} ${h === 1 ? 'hour' : 'hours'} ago`
  }
  const clock = clockIn(iso, tz, tzShort)
  if (!clock) return ''
  const d = new Date(iso)
  let day: string
  try {
    day = d.toLocaleDateString('en-CA', tz ? { timeZone: tz } : undefined) // YYYY-MM-DD
  } catch {
    day = d.toLocaleDateString('en-CA')
  }
  return day === sessionDate ? `Generated ${clock}` : `Generated ${fDate(day)}, ${clock}`
}

/** Hover text for a notable move: how it compares with the instrument's usual day. */
export function notableTitle(changePct: number | null, usualMovePct?: number | null): string {
  if (changePct == null) return 'A notable move'
  if (!usualMovePct) return 'A notable move for this kind of asset'
  const times = Math.abs(changePct) / usualMovePct
  return `About ${times.toFixed(1)}× its usual daily move (${usualMovePct.toFixed(1)}%)`
}
