/** A recurrence rule in plain English, or null for any rule these rules can't say exactly.
 *  Strict on purpose: a schedule read back wrong is worse than the raw rule. rrule.js's toText()
 *  was tried first: it prints "at 9" for 9:30 and turns "the last Friday" into "on Friday". */

const WEEKDAYS: Record<string, string> = {
  MO: 'Monday',
  TU: 'Tuesday',
  WE: 'Wednesday',
  TH: 'Thursday',
  FR: 'Friday',
  SA: 'Saturday',
  SU: 'Sunday',
}
const WORKING_DAYS = ['MO', 'TU', 'WE', 'TH', 'FR']
const POSITIONS: Record<string, string> = {
  '1': 'first',
  '2': 'second',
  '3': 'third',
  '4': 'fourth',
  '-1': 'last',
}
const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]
const ALLOWED_BY_FREQ: Record<string, string[]> = {
  MINUTELY: [],
  HOURLY: [],
  DAILY: ['BYDAY', 'BYHOUR', 'BYMINUTE'],
  WEEKLY: ['BYDAY', 'BYHOUR', 'BYMINUTE'],
  MONTHLY: ['BYDAY', 'BYMONTHDAY', 'BYSETPOS', 'BYHOUR', 'BYMINUTE'],
  YEARLY: ['BYMONTH', 'BYMONTHDAY', 'BYHOUR', 'BYMINUTE'],
}

type Parts = Record<string, string>

export function clockLabel(hour: number, minute: number) {
  const period = hour >= 12 ? 'PM' : 'AM'
  return `${hour % 12 || 12}:${String(minute).padStart(2, '0')} ${period}`
}

function joined(items: string[]) {
  if (items.length < 2) return items.join('')
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

function ordinal(value: number) {
  const mod100 = value % 100
  if (mod100 >= 11 && mod100 <= 13) return `${value}th`
  return `${value}${{ 1: 'st', 2: 'nd', 3: 'rd' }[value % 10] ?? 'th'}`
}

function numbers(value: string, min: number, max: number): number[] | null {
  const parsed = value.split(',').map((item) => (/^-?\d+$/.test(item) ? Number(item) : NaN))
  return parsed.every((item) => item >= min && item <= max) ? parsed : null
}

function parse(rrule: string): Parts | null {
  const parts: Parts = {}
  for (const field of rrule.split(';')) {
    const [key, value, extra] = field.split('=')
    if (!key || !value || extra !== undefined || key in parts) return null
    parts[key] = value
  }
  return parts
}

function plainDays(value: string): string[] | null {
  const days = value.split(',')
  return days.every((day) => day in WEEKDAYS) ? days : null
}

function timeSuffix(parts: Parts): string | null {
  if (!parts.BYHOUR) return parts.BYMINUTE ? null : ''
  const hours = numbers(parts.BYHOUR, 0, 23)
  const minutes = numbers(parts.BYMINUTE ?? '0', 0, 59)
  if (!hours || !minutes || minutes.length !== 1) return null
  return ` at ${joined(hours.map((hour) => clockLabel(hour, minutes[0])))}`
}

function everyUnit(interval: number, unit: string) {
  return interval === 1 ? `Every ${unit}` : `Every ${interval} ${unit}s`
}

function dailyText(parts: Parts, interval: number): string | null {
  if (!parts.BYDAY) return everyUnit(interval, 'day')
  return interval === 1 && parts.BYDAY === WORKING_DAYS.join(',') ? 'Weekdays' : null
}

function weeklyText(parts: Parts, interval: number): string | null {
  if (!parts.BYDAY) return everyUnit(interval, 'week')
  const days = plainDays(parts.BYDAY)
  if (!days) return null
  const names = joined(days.map((day) => WEEKDAYS[day]))
  return interval === 1 ? `Every ${names}` : `${everyUnit(interval, 'week')} on ${names}`
}

/** "the 5th", "the last day", "the first Monday", "the last weekday". */
function monthlyDay(parts: Parts): string | null {
  if (parts.BYMONTHDAY) {
    if (parts.BYDAY || parts.BYSETPOS) return null
    const days = numbers(parts.BYMONTHDAY, -1, 31)
    if (!days || days.includes(0)) return null
    return `the ${joined(days.map((day) => (day === -1 ? 'last day' : ordinal(day))))}`
  }
  if (!parts.BYDAY) return parts.BYSETPOS ? null : ''
  if (parts.BYSETPOS) {
    const position = POSITIONS[parts.BYSETPOS]
    if (!position) return null
    if (parts.BYDAY === WORKING_DAYS.join(',')) return `the ${position} weekday`
    return WEEKDAYS[parts.BYDAY] ? `the ${position} ${WEEKDAYS[parts.BYDAY]}` : null
  }
  const match = /^(-?\d)([A-Z]{2})$/.exec(parts.BYDAY)
  if (!match || !POSITIONS[match[1]] || !WEEKDAYS[match[2]]) return null
  return `the ${POSITIONS[match[1]]} ${WEEKDAYS[match[2]]}`
}

function monthlyText(parts: Parts, interval: number): string | null {
  const day = monthlyDay(parts)
  if (day === null) return null
  return day ? `${everyUnit(interval, 'month')} on ${day}` : everyUnit(interval, 'month')
}

function yearlyText(parts: Parts, interval: number): string | null {
  const month = numbers(parts.BYMONTH ?? '', 1, 12)
  const day = numbers(parts.BYMONTHDAY ?? '', 1, 31)
  if (interval !== 1 || month?.length !== 1 || day?.length !== 1) return null
  return `Every year on ${MONTHS[month[0] - 1]} ${day[0]}`
}

export function rruleText(rrule: string): string | null {
  const parts = parse(rrule)
  const allowed = parts && ALLOWED_BY_FREQ[parts.FREQ]
  if (!parts || !allowed) return null
  if (Object.keys(parts).some((key) => !['FREQ', 'INTERVAL', ...allowed].includes(key))) return null
  const interval = numbers(parts.INTERVAL ?? '1', 1, 9999)?.[0]
  if (!interval || parts.INTERVAL?.includes(',')) return null
  if (parts.FREQ === 'MINUTELY') return everyUnit(interval, 'minute')
  if (parts.FREQ === 'HOURLY') return everyUnit(interval, 'hour')
  const text = {
    DAILY: dailyText,
    WEEKLY: weeklyText,
    MONTHLY: monthlyText,
    YEARLY: yearlyText,
  }[parts.FREQ]?.(parts, interval)
  const time = timeSuffix(parts)
  return text == null || time === null ? null : `${text}${time}`
}
