// Setup nudges — the small card floating in a corner of the app, on every screen, that points
// a new owner at the setups that make Lucius useful away from the app: Telegram, Gmail, and the
// briefing / report / digest Automations.
//
// Several CASES, each its own card, each shown only while its own setup is missing (its STATUS
// decides — a broken connector gets "fix", a missing one "set up", a working one nothing).
// Cadence, so it neither nags nor goes silent:
//   • at most ONE card at a time, the first eligible case in CASE order;
//   • after any dismiss or click-through, no card for QUIET_DAYS;
//   • a dismissed case comes back once after SNOOZE_DAYS; a second dismiss retires it;
//   • a broken connector skips the quiet days (it is news, not a suggestion);
//   • nothing but a broken connector for SETTLE_DAYS after first run — Lucius is still setting
//     up the book then, and a suggestion beside his welcome is one guide too many.
// When on a visit the card may appear (after the screen's own intro or tour) is SetupNudgeCard's.
// Every dismiss / click is one `profile.seen_intros` key, `nudge-<case>-<yyyymmdd>`, so the
// cadence is the same on every device and survives a reload.
import type { Automation } from '../../api/automations'
import type { Connector } from '../tabs/connectors/types'

export type NudgeCase =
  | 'telegram-error'
  | 'email-error'
  | 'telegram'
  | 'gmail'
  | 'daily-briefing'
  | 'mail-digest'
  | 'reports'

export interface NudgeAction {
  label: string
  /** An in-app path: `/connectors?connector=…` or `/automations?template=…`. */
  to: string
}

export interface Nudge {
  case: NudgeCase
  title: string
  body: string
  primary: NudgeAction
  secondary?: NudgeAction
}

export const QUIET_DAYS = 3
export const SNOOZE_DAYS = 14
export const SETTLE_DAYS = 3
const MAX_DISMISSALS = 2
const DAY_MS = 86_400_000

const KEY_RE = /^nudge-([a-z-]+)-(\d{8})$/

/** `nudge-<case>-<yyyymmdd>` for `at`, in the browser's local calendar. */
export function nudgeKey(c: NudgeCase, at: Date): string {
  const y = at.getFullYear()
  const m = String(at.getMonth() + 1).padStart(2, '0')
  const d = String(at.getDate()).padStart(2, '0')
  return `nudge-${c}-${y}${m}${d}`
}

interface Touch {
  case: string
  at: number
}

function touches(seen: readonly string[]): Touch[] {
  const out: Touch[] = []
  for (const key of seen) {
    const m = KEY_RE.exec(key)
    if (!m) continue
    const s = m[2]!
    const at = new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8)).getTime()
    out.push({ case: m[1]!, at })
  }
  return out
}

type Health = 'missing' | 'working' | 'error'

function health(connectors: readonly Connector[], kind: string): Health {
  const row = connectors.find((c) => c.kind === kind || c.id === kind)
  if (!row || row.status === 'not_configured') return 'missing'
  return row.status === 'error' ? 'error' : 'working'
}

/** Whether an Automation made from the template (or written by hand to do the same job)
 *  exists. Templates leave no marker on the row, so this reads the job's own words: every
 *  report template names its skill in the instructions, and the name is the template's. */
/** Any live Automation whose name or instructions carries one of `phrases` — an owner's own
 *  "Morning briefing" is a daily briefing as much as the template's "Daily briefing" is. */
function hasAutomation(automations: readonly Automation[], ...phrases: string[]): boolean {
  const ps = phrases.map((p) => p.toLowerCase())
  return automations.some((a) => {
    if (a.status === 'completed') return false
    const text = `${a.name}\n${a.instructions}`.toLowerCase()
    return ps.some((p) => text.includes(p))
  })
}

const tg = (label: string): NudgeAction => ({ label, to: '/connectors?connector=telegram' })
const mail = (label: string): NudgeAction => ({ label, to: '/connectors?connector=email' })
const tpl = (label: string, key: string): NudgeAction => ({
  label,
  to: `/automations?template=${key}`,
})

/** Every case whose setup is missing right now, in the order they are offered. */
export function eligibleNudges(
  connectors: readonly Connector[],
  automations: readonly Automation[],
): Nudge[] {
  const telegram = health(connectors, 'telegram')
  const email = health(connectors, 'email')
  const where = telegram === 'working' ? 'on Telegram' : 'here in chat'
  const out: Nudge[] = []

  if (telegram === 'error')
    out.push({
      case: 'telegram-error',
      title: 'Telegram stopped working',
      body: "Lucius can't reach you there right now, so briefings and alerts aren't arriving.",
      primary: tg('Fix Telegram'),
    })
  if (email === 'error')
    out.push({
      case: 'email-error',
      title: 'Email stopped syncing',
      body: "Lucius can't read your inbox right now, so bank and card mail isn't being logged.",
      primary: mail('Fix email'),
    })
  if (telegram === 'missing')
    out.push({
      case: 'telegram',
      title: 'Take Lucius with you',
      body: 'Chat with Lucius from Telegram, and get your briefings and alerts on your phone.',
      primary: tg('Set up Telegram'),
    })
  if (email === 'missing')
    out.push({
      case: 'gmail',
      title: 'Let Lucius read your money mail',
      body: 'Connect Gmail and Lucius logs bank, card and trade emails, and flags bills and failed payments.',
      primary: mail('Connect Gmail'),
    })
  if (!hasAutomation(automations, 'daily briefing', 'morning briefing'))
    out.push({
      case: 'daily-briefing',
      title: 'Get a morning briefing',
      body: `A short update on yesterday's money and investments, every morning ${where}.`,
      primary: tpl('Add daily briefing', 'daily-briefing'),
    })
  if (email === 'working' && !hasAutomation(automations, 'email finance digest'))
    out.push({
      case: 'mail-digest',
      title: 'Turn your inbox into a digest',
      body: `Twice a day Lucius logs your bank and card mail and tells you what needs you, ${where}.`,
      primary: tpl('Add email digest', 'mail_digest'),
    })
  const weekly = !hasAutomation(automations, 'weekly report')
  const monthly = !hasAutomation(automations, 'monthly report')
  if (weekly || monthly) {
    const actions = [
      weekly ? tpl('Add weekly report', 'weekly-report') : null,
      monthly ? tpl('Add monthly report', 'monthly-report') : null,
    ].filter((a): a is NudgeAction => a !== null)
    out.push({
      case: 'reports',
      title:
        weekly && monthly
          ? 'Weekly and monthly reports'
          : weekly
            ? 'A weekly report'
            : 'A monthly report',
      body: `How the ${weekly && monthly ? 'week and month' : weekly ? 'week' : 'month'} went — spending, bills and investments — ${where}.`,
      primary: actions[0]!,
      secondary: actions[1],
    })
  }
  return out
}

const URGENT: ReadonlySet<NudgeCase> = new Set(['telegram-error', 'email-error'])

/** The one card to show now, or null — `eligibleNudges` filtered by the cadence above. */
export function pickNudge(
  eligible: readonly Nudge[],
  seen: readonly string[],
  now: Date,
  /** When first run finished (`/api/setup`'s `onboarding_completed_at`); null for a book set
   *  up before the stamp existed, which is long settled. */
  setupDoneAt: string | null = null,
): Nudge | null {
  const t = touches(seen)
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const lastAny = t.reduce((max, x) => Math.max(max, x.at), -Infinity)
  const done = setupDoneAt ? Date.parse(setupDoneAt) : NaN
  const settling = now.getTime() - done < SETTLE_DAYS * DAY_MS
  const quiet = settling || today - lastAny < QUIET_DAYS * DAY_MS
  for (const n of eligible) {
    const mine = t.filter((x) => x.case === n.case)
    if (mine.length >= MAX_DISMISSALS) continue
    const last = mine.reduce((max, x) => Math.max(max, x.at), -Infinity)
    if (today - last < SNOOZE_DAYS * DAY_MS) continue
    if (quiet && !URGENT.has(n.case)) continue
    return n
  }
  return null
}

/** `seen` with one more touch of `c` today — one key per case per day, so a double click
 *  never spends the second dismissal. */
export function withTouch(seen: readonly string[], c: NudgeCase, now: Date): string[] {
  return [...new Set([...seen, nudgeKey(c, now)])]
}
