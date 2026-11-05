import { conditionIsComplete, readConditions, transactionLabel } from './txnConditions'
import { CONFLICTING_CONDITIONS, conditionConflicts } from './txnConditionConflicts'
import { Clock3, Zap } from 'lucide-react'
import type { AutomationTrigger } from '../../../api/automations'
import { fDateTime } from '../../../utils/format'
import { rruleText } from './rruleText'
import {
  moneyLabel,
  transactionTriggerView,
  triggerAmountCurrency,
} from './automationTriggerPredicates'

export interface AutomationOption {
  id: string
  label: string
}

export interface AutomationOptions {
  accounts: AutomationOption[]
  instruments: AutomationOption[]
  payees?: AutomationOption[]
  base_currency?: string
}

const WEEKDAYS: Record<string, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
}

function optionLabel(options: AutomationOption[], id: unknown, fallback: string) {
  if (typeof id !== 'string' || !id) return fallback
  return options.find((option) => option.id === id)?.label ?? id
}

function timeLabel(value?: string) {
  if (!value) return '—'
  const [rawHour = '0', rawMinute = '0'] = value.split(':')
  const hour = Number(rawHour)
  const minute = Number(rawMinute)
  const period = hour >= 12 ? 'PM' : 'AM'
  return `${hour % 12 || 12}:${String(minute).padStart(2, '0')} ${period}`
}

function ordinal(value: number) {
  const mod100 = value % 100
  const suffix =
    mod100 >= 11 && mod100 <= 13
      ? 'th'
      : (({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[value % 10] ?? 'th')
  return `${value}${suffix}`
}

export function automationTriggerLabel(
  trigger: AutomationTrigger,
  options: AutomationOptions = EMPTY_AUTOMATION_OPTIONS,
): string {
  if (trigger.kind === 'manual') return 'Only when you ask'
  if (trigger.kind === 'schedule') {
    if (trigger.type === 'daily') return `Every day at ${timeLabel(trigger.time)}`
    if (trigger.type === 'weekdays') return `Weekdays at ${timeLabel(trigger.time)}`
    if (trigger.type === 'weekly')
      return `Every ${WEEKDAYS[String(trigger.day)] ?? trigger.day ?? '—'} at ${timeLabel(trigger.time)}`
    if (trigger.type === 'monthly')
      return `Monthly on the ${ordinal(Number(trigger.day) || 1)} at ${timeLabel(trigger.time)}`
    if (trigger.type === 'interval') {
      return intervalLabel(Number(trigger.seconds || 0))
    }
    if (trigger.type === 'once')
      return trigger.at ? `Once on ${fDateTime(trigger.at, 'short', 'always')}` : 'Once'
    if (!trigger.rrule) return 'Custom cadence'
    return rruleText(trigger.rrule) ?? `Custom cadence • ${trigger.rrule}`
  }

  const when = trigger.when
  let label: string
  if (trigger.event === 'txn') {
    const view = transactionTriggerView(when)
    if (view.custom) {
      label = `Custom transaction${view.exactConditions ? ` • ${view.exactConditions}` : ''}`
    } else {
      label = transactionLabel(readConditions(when), options)
    }
  } else if (trigger.event === 'balance') {
    label = `When ${optionLabel(options.accounts, when.account, 'an account')} ${when.direction === 'above' ? 'rises above' : 'drops below'} ${when.amount ? moneyLabel(triggerAmountCurrency('balance', when, options), when.amount) : '—'}`
  } else {
    const symbol = optionLabel(options.instruments, when.symbol, 'a ticker')
    const pct = `${when.value || '—'}% in a day`
    if (when.direction === 'move') label = `When ${symbol} moves more than ${pct}`
    else if (when.direction === 'rise') label = `When ${symbol} rises more than ${pct}`
    else if (when.direction === 'fall') label = `When ${symbol} falls more than ${pct}`
    else if (!when.direction) label = `When ${symbol}’s price moves`
    else
      label = `When ${symbol} ${when.direction === 'above' ? 'rises above' : 'falls below'} ${when.value ? moneyLabel(triggerAmountCurrency('price', when, options), when.value) : '—'}`
  }
  if (trigger.limit === 'hour') return `${label} • at most once an hour`
  if (trigger.limit === 'day') return `${label} • at most once a day`
  return label
}

/** A custom cadence's raw rule, when the label above reads it in words instead of showing it. */
export function automationCustomRule(trigger: AutomationTrigger): string | null {
  if (trigger.kind !== 'schedule' || trigger.type !== 'rrule' || !trigger.rrule) return null
  return rruleText(trigger.rrule) ? trigger.rrule : null
}

/** The trigger in words, with a custom cadence's rule quietly below them. */
export function AutomationTriggerText({
  trigger,
  options = EMPTY_AUTOMATION_OPTIONS,
}: {
  trigger: AutomationTrigger
  options?: AutomationOptions
}) {
  const rule = automationCustomRule(trigger)
  return (
    <>
      <span className="a3-trigger-words">{automationTriggerLabel(trigger, options)}</span>
      {rule && <span className="a3-trigger-rule">{rule}</span>}
    </>
  )
}

export function missingTriggerFields(trigger: AutomationTrigger): string[] {
  if (trigger.kind === 'manual') return []
  if (trigger.kind === 'schedule') {
    if (trigger.type === 'once') return trigger.at ? [] : ['date and time']
    if (trigger.type === 'interval') return trigger.seconds ? [] : ['interval']
    if (trigger.type === 'rrule') return trigger.rrule ? [] : ['custom cadence']
    const missing: string[] = []
    if (!trigger.time) missing.push('time')
    if (trigger.type === 'weekly' && !trigger.day) missing.push('weekday')
    if (trigger.type === 'monthly' && !trigger.day) missing.push('day')
    return missing
  }
  if (trigger.event === 'balance') {
    return [
      !trigger.when.account && 'account',
      !trigger.when.direction && 'direction',
      !trigger.when.amount && 'amount',
    ].filter(Boolean) as string[]
  }
  if (trigger.event === 'txn') {
    const conditions = readConditions(trigger.when)
    return [
      !conditions.every(conditionIsComplete) && 'condition',
      conditionConflicts(conditions).length > 0 && CONFLICTING_CONDITIONS,
    ].filter(Boolean) as string[]
  }
  if (trigger.event === 'price') {
    return [
      !trigger.when.symbol && 'ticker',
      !trigger.when.direction && 'direction',
      !trigger.when.value && 'threshold',
    ].filter(Boolean) as string[]
  }
  return []
}

export const EMPTY_AUTOMATION_OPTIONS: AutomationOptions = {
  accounts: [],
  instruments: [],
  payees: [],
}

/** The trigger read back as a sentence, above its fields — and what still blocks saving. */
export function AutomationTriggerSummary({
  trigger,
  options = EMPTY_AUTOMATION_OPTIONS,
}: {
  trigger: AutomationTrigger
  options?: AutomationOptions
}) {
  const missing = missingTriggerFields(trigger)
  const toSet = missing.filter((field) => field !== CONFLICTING_CONDITIONS)
  const conflicting = missing.length > toSet.length
  const Icon = trigger.kind === 'event' ? Zap : Clock3
  return (
    <div
      className={`a3-trigger-summary${missing.length ? ' issue' : ''}`}
      data-od-id="trigger-summary"
    >
      <span className="a3-trigger-summary-ic">
        <Icon size={14} strokeWidth={1.6} aria-hidden="true" />
      </span>
      <span className="grow">
        <span className="a3-trigger-summary-text">
          <AutomationTriggerText trigger={trigger} options={options} />
        </span>
        {toSet.length > 0 && (
          <span className="a3-trigger-missing">Set the {joinFields(toSet)} below.</span>
        )}
        {conflicting && (
          <span className="a3-trigger-missing a3-trigger-error">
            These conditions can never all be true, so it would never run — fix them below.
          </span>
        )}
      </span>
    </div>
  )
}

function joinFields(fields: string[]) {
  if (fields.length < 2) return fields[0] ?? ''
  if (fields.length === 2) return `${fields[0]} and ${fields[1]}`
  return `${fields.slice(0, -1).join(', ')} and ${fields.at(-1)}`
}

function intervalLabel(seconds: number) {
  if (!seconds) return 'Every —'
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600
    return `Every ${hours === 1 ? 'hour' : `${hours} hours`}`
  }
  if (seconds % 60 === 0) {
    const minutes = seconds / 60
    return `Every ${minutes === 1 ? 'minute' : `${minutes} minutes`}`
  }
  return `Every ${seconds === 1 ? 'second' : `${seconds} seconds`}`
}
