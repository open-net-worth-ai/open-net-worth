import { useState } from 'react'
import { Clock3, Zap } from 'lucide-react'
import { LuciusMark } from '../../../ui/icons'
import type { AutomationEventType, AutomationTrigger, ScheduleType } from '../../../api/automations'
import type { AutomationOptions } from './AutomationTriggerSummary'
import { EMPTY_AUTOMATION_OPTIONS, missingTriggerFields } from './AutomationTriggerSummary'
import {
  instrumentCurrency,
  transactionTriggerView,
  triggerAmountCurrency,
} from './automationTriggerPredicates'
import { Input, OptionSelect, Row, Select } from './automationFormControls'
import { TransactionConditionFields } from './AutomationConditionFields'
import { TickerConfirm, TickerPicker, type PickedInstrument } from './TickerPicker'

const DAYS = [
  ['mon', 'Monday'],
  ['tue', 'Tuesday'],
  ['wed', 'Wednesday'],
  ['thu', 'Thursday'],
  ['fri', 'Friday'],
  ['sat', 'Saturday'],
  ['sun', 'Sunday'],
] as const
const SCHEDULES = [
  ['manual', 'Manual'],
  ['interval', 'Hourly'],
  ['daily', 'Daily'],
  ['weekdays', 'Weekdays'],
  ['weekly', 'Weekly'],
  ['monthly', 'Monthly'],
  ['once', 'Once, on a date'],
  ['custom', 'Custom'],
] as const
// What each Run choice does — nothing that matches is dropped; it only changes how many runs.
const RUN_HINT: Record<'each' | 'hour' | 'day', string> = {
  each: 'A run for every match, one after another.',
  hour: 'Runs on the first match; anything later in the hour waits and comes in one run.',
  day: 'Runs on the first match; anything later in the day waits and comes in one run.',
}
const HOURLY_SECONDS = 3600
const EVENTS: Array<[AutomationEventType, string]> = [
  ['txn', 'A transaction posts'],
  ['balance', 'A balance crosses a threshold'],
  ['price', 'A price moves'],
]

export function AutomationTriggerFields({
  trigger,
  onChange,
  options = EMPTY_AUTOMATION_OPTIONS,
  optionsError,
  onRetryOptions,
  onAskForCadence,
}: {
  trigger: AutomationTrigger
  onChange: (trigger: AutomationTrigger, touchedPath: string) => void
  options?: AutomationOptions
  optionsError?: string | null
  onRetryOptions?: () => void
  /** Hand the cadence to Lucius — the caller supplies the draft's context. */
  onAskForCadence?: () => void
}) {
  const missing = new Set(missingTriggerFields(trigger))
  // A ticker resolved from the market search this session — not in `options` until they reload.
  const [picked, setPicked] = useState<PickedInstrument[]>([])
  const [choosingTicker, setChoosingTicker] = useState(false)
  const instruments = [...options.instruments, ...picked]
  // Lucius may store an instrument id; the box shows (and the quote looks up) its ticker.
  const rawSymbol = trigger.kind === 'event' ? String(trigger.when.symbol ?? '') : ''
  const tickerText = instruments.find((i) => i.id === rawSymbol)?.label ?? rawSymbol
  const eventMode = trigger.kind === 'event'
  const pickedSchedule = schedulePickerValue(trigger)
  const amountCurrency =
    trigger.kind === 'event' ? triggerAmountCurrency(trigger.event, trigger.when, options) : ''
  const transactionView =
    trigger.kind === 'event' && trigger.event === 'txn'
      ? transactionTriggerView(trigger.when)
      : null
  const setKind = (kind: 'schedule' | 'event') => {
    if (trigger.kind === kind) return
    onChange(
      kind === 'event'
        ? { kind: 'event', event: 'txn', when: {}, limit: 'hour' }
        : { kind: 'schedule', type: 'weekdays', time: '08:00' },
      'trigger.kind',
    )
  }
  const setScheduleType = (value: string) => {
    if (value === 'manual') return onChange({ kind: 'manual' }, 'trigger.kind')
    if (value === 'custom') {
      onAskForCadence?.()
      return
    }
    const type = value as ScheduleType
    const next: Extract<AutomationTrigger, { kind: 'schedule' }> = { kind: 'schedule', type }
    if (type === 'daily' || type === 'weekdays') next.time = '08:00'
    if (type === 'weekly') Object.assign(next, { day: 'mon', time: '08:00' })
    if (type === 'monthly') Object.assign(next, { day: 1, time: '08:00' })
    if (type === 'interval') next.seconds = HOURLY_SECONDS
    onChange(next, trigger.kind === 'schedule' ? 'trigger.type' : 'trigger.kind')
  }
  const setSchedule = (
    patch: Partial<Extract<AutomationTrigger, { kind: 'schedule' }>>,
    path: string,
  ) => trigger.kind === 'schedule' && onChange({ ...trigger, ...patch }, path)
  const setWhen = (patch: Record<string, unknown>, path: string) =>
    trigger.kind === 'event' && onChange({ ...trigger, when: { ...trigger.when, ...patch } }, path)
  const setEventType = (event: AutomationEventType) =>
    onChange(
      {
        kind: 'event',
        event,
        when: {},
        limit: event === 'txn' ? 'hour' : event === 'balance' || event === 'price' ? 'day' : 'each',
      },
      'trigger.event',
    )

  return (
    <div className="a3-trigger-details" id="a3-trigger-details" data-od-id="trigger-details">
      <div className="a3-seg" role="radiogroup" aria-label="Trigger type" data-od-id="trigger-row">
        <button
          type="button"
          role="radio"
          aria-checked={!eventMode}
          className={`a3-seg-btn${!eventMode ? ' on' : ''}`}
          onClick={() => setKind('schedule')}
        >
          <Clock3 size={13} aria-hidden="true" /> Schedule
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={eventMode}
          className={`a3-seg-btn${eventMode ? ' on' : ''}`}
          onClick={() => setKind('event')}
        >
          <Zap size={13} aria-hidden="true" /> Event
        </button>
      </div>
      {optionsError && (
        <div className="a3-option-error" role="alert">
          <span>{optionsError}</span>
          {onRetryOptions && (
            <button type="button" className="a3-linkbtn" onClick={onRetryOptions}>
              Retry
            </button>
          )}
        </div>
      )}
      {eventMode && trigger.kind === 'event' ? (
        <div className="a3-trows">
          <Row label="Event" htmlFor="automation-event" required>
            <Select
              id="automation-event"
              value={transactionView?.custom ? 'custom' : trigger.event}
              options={transactionView?.custom ? [...EVENTS, ['custom', 'Custom']] : EVENTS}
              onChange={(value) => {
                if (value !== 'custom') setEventType(value as AutomationEventType)
              }}
            />
          </Row>
          {transactionView?.custom && (
            <Row label="Conditions" htmlFor="automation-custom-conditions">
              <output id="automation-custom-conditions" className="a3-muted">
                {transactionView.exactConditions}
              </output>
            </Row>
          )}
          {trigger.event === 'txn' && !transactionView?.custom && (
            <TransactionConditionFields
              when={trigger.when}
              accounts={options.accounts}
              payees={options.payees ?? []}
              baseCurrency={options.base_currency}
              onChange={setWhen}
            />
          )}
          {trigger.event === 'balance' && (
            <>
              <Row label="Account" htmlFor="automation-account" required>
                <OptionSelect
                  needed={missing.has('account')}
                  id="automation-account"
                  value={trigger.when.account}
                  options={options.accounts}
                  placeholder="Choose an account"
                  onChange={(value) => setWhen({ account: value }, 'trigger.when.account')}
                />
              </Row>
              <Row label="Direction" htmlFor="automation-direction" required>
                <Select
                  needed={missing.has('direction')}
                  id="automation-direction"
                  value={String(trigger.when.direction ?? '')}
                  placeholder="Choose a direction"
                  options={CROSS_DIRECTION}
                  onChange={(value) => setWhen({ direction: value }, 'trigger.when.direction')}
                />
              </Row>
              <Row label="Amount" htmlFor="automation-amount" required unit={amountCurrency}>
                <Input
                  needed={missing.has('amount')}
                  id="automation-amount"
                  type="number"
                  value={trigger.when.amount}
                  onChange={(value) => setWhen({ amount: value }, 'trigger.when.amount')}
                />
              </Row>
            </>
          )}
          {trigger.event === 'price' && (
            <>
              <Row
                label="Ticker"
                htmlFor="automation-symbol"
                required
                below={
                  <TickerConfirm
                    ticker={choosingTicker ? '' : tickerText}
                    onCurrency={(ticker, currency) => {
                      if (!instrumentCurrency(instruments, ticker))
                        setPicked((list) => [...list, { id: ticker, label: ticker, currency }])
                    }}
                    onSearchAgain={() => {
                      setWhen({ symbol: '' }, 'trigger.when.symbol')
                      document.getElementById('automation-symbol')?.focus()
                    }}
                  />
                }
              >
                <TickerPicker
                  needed={missing.has('holding')}
                  id="automation-symbol"
                  value={tickerText}
                  onOpenChange={setChoosingTicker}
                  instruments={instruments}
                  onChange={(ticker, instrument) => {
                    if (instrument) {
                      setPicked((list) => [...list, instrument])
                      // A market pick just created the instrument: reload the choices so the
                      // summary above names its currency too.
                      onRetryOptions?.()
                    }
                    setWhen({ symbol: ticker }, 'trigger.when.symbol')
                  }}
                />
              </Row>
              <Row label="Direction" htmlFor="automation-price-direction" required>
                <Select
                  needed={missing.has('direction')}
                  id="automation-price-direction"
                  value={String(trigger.when.direction ?? '')}
                  placeholder="Choose a direction"
                  options={PRICE_DIRECTION}
                  onChange={(value) => setWhen({ direction: value }, 'trigger.when.direction')}
                />
              </Row>
              <Row
                label="Threshold"
                htmlFor="automation-value"
                required
                unit={
                  PERCENT_PRICE_DIRECTIONS.has(String(trigger.when.direction ?? ''))
                    ? '% vs. prior close'
                    : triggerAmountCurrency('price', trigger.when, { ...options, instruments }) ||
                      undefined
                }
              >
                <Input
                  needed={missing.has('threshold')}
                  id="automation-value"
                  type="number"
                  min={0}
                  placeholder={
                    PERCENT_PRICE_DIRECTIONS.has(String(trigger.when.direction ?? ''))
                      ? 'e.g. 5'
                      : 'Price'
                  }
                  value={trigger.when.value}
                  onChange={(value) => setWhen({ value }, 'trigger.when.value')}
                />
              </Row>
            </>
          )}
          <Row
            label="Run"
            htmlFor="automation-limit"
            below={<span className="a3-muted">{RUN_HINT[trigger.limit]}</span>}
          >
            <Select
              id="automation-limit"
              value={trigger.limit}
              options={[
                ['each', 'each time'],
                ['hour', 'at most once an hour'],
                ['day', 'at most once a day'],
              ]}
              onChange={(value) =>
                onChange({ ...trigger, limit: value as 'each' | 'hour' | 'day' }, 'trigger.limit')
              }
            />
          </Row>
        </div>
      ) : (
        <div className="a3-trows">
          <Row
            label="Repeats"
            htmlFor="automation-schedule-type"
            required
            below={
              onAskForCadence ? (
                <button
                  type="button"
                  className="a3-linkbtn a3-lucius-link"
                  onClick={onAskForCadence}
                >
                  <LuciusMark size={12} aria-hidden="true" />
                  Create via Lucius for a custom cadence
                </button>
              ) : undefined
            }
          >
            <Select
              id="automation-schedule-type"
              value={pickedSchedule}
              options={SCHEDULES}
              onChange={setScheduleType}
            />
          </Row>
          {trigger.kind === 'schedule' && trigger.type === 'weekly' && (
            <Row label="On" htmlFor="automation-weekday" required>
              <Select
                id="automation-weekday"
                value={String(trigger.day ?? 'mon')}
                options={DAYS}
                onChange={(value) => setSchedule({ day: value }, 'trigger.day')}
              />
            </Row>
          )}
          {trigger.kind === 'schedule' && trigger.type === 'monthly' && (
            <Row label="On the" htmlFor="automation-monthday" required>
              <Select
                id="automation-monthday"
                value={String(trigger.day ?? 1)}
                options={Array.from(
                  { length: 28 },
                  (_, index) => [String(index + 1), ordinal(index + 1)] as const,
                )}
                onChange={(value) => setSchedule({ day: Number(value) }, 'trigger.day')}
              />
            </Row>
          )}
          {trigger.kind === 'schedule' &&
            ['daily', 'weekdays', 'weekly', 'monthly'].includes(trigger.type) && (
              <Row label="At" htmlFor="automation-hour" required>
                <TimePick
                  value={trigger.time ?? '09:00'}
                  needed={missing.has('time')}
                  onChange={(value) => setSchedule({ time: value }, 'trigger.time')}
                />
              </Row>
            )}
          {trigger.kind === 'schedule' && trigger.type === 'once' && (
            <>
              <Row label="On" htmlFor="automation-once-date" required>
                <Input
                  needed={missing.has('date and time')}
                  id="automation-once-date"
                  type="date"
                  value={(trigger.at ?? '').split('T')[0]}
                  onChange={(value) =>
                    setSchedule(
                      { at: `${value}T${(trigger.at ?? '').split('T')[1] || '09:00'}` },
                      'trigger.at',
                    )
                  }
                />
              </Row>
              <Row label="At" htmlFor="automation-once-hour" required>
                <TimePick
                  value={(trigger.at ?? '').split('T')[1] || '09:00'}
                  needed={missing.has('date and time')}
                  idPrefix="automation-once"
                  onChange={(value) =>
                    setSchedule(
                      { at: `${(trigger.at ?? '').split('T')[0] || ''}T${value}` },
                      'trigger.at',
                    )
                  }
                />
              </Row>
            </>
          )}
        </div>
      )}
    </div>
  )
}

const CROSS_DIRECTION = [
  ['', 'Choose a direction'],
  ['below', 'Drops below'],
  ['above', 'Rises above'],
] as const
const PRICE_DIRECTION = [
  ['', 'Choose a direction'],
  ['below', 'Falls below a price'],
  ['above', 'Rises above a price'],
  ['fall', 'Falls more than %'],
  ['rise', 'Rises more than %'],
  ['move', 'Moves more than % (either way)'],
] as const
const PERCENT_PRICE_DIRECTIONS = new Set(['move', 'rise', 'fall'])

function TimePick({
  value,
  onChange,
  needed = false,
  idPrefix = 'automation',
}: {
  value: string
  onChange: (value: string) => void
  needed?: boolean
  idPrefix?: string
}) {
  const [hour24 = '09', minute = '00'] = value.split(':')
  const numericHour = Number(hour24)
  const period = numericHour >= 12 ? 'PM' : 'AM'
  const hour = String(numericHour % 12 || 12).padStart(2, '0')
  const apply = (nextHour: string, nextMinute: string, nextPeriod: string) => {
    const baseHour = (Number(nextHour) % 12) + (nextPeriod === 'PM' ? 12 : 0)
    onChange(`${String(baseHour).padStart(2, '0')}:${nextMinute}`)
  }
  return (
    <div className="a3-timepick">
      <select
        id={`${idPrefix}-hour`}
        className={`a3-mini-select${needed ? ' needed' : ''}`}
        value={hour}
        onChange={(event) => apply(event.target.value, minute, period)}
      >
        {Array.from({ length: 12 }, (_, index) => String(index + 1).padStart(2, '0')).map(
          (item) => (
            <option key={item}>{item}</option>
          ),
        )}
      </select>
      <span className="sep">:</span>
      <select
        aria-label="Minute"
        className={`a3-mini-select${needed ? ' needed' : ''}`}
        value={minute}
        onChange={(event) => apply(hour, event.target.value, period)}
      >
        {Array.from({ length: 12 }, (_, index) => String(index * 5).padStart(2, '0')).map(
          (item) => (
            <option key={item}>{item}</option>
          ),
        )}
      </select>
      <select
        aria-label="AM or PM"
        className={`a3-mini-select${needed ? ' needed' : ''}`}
        value={period}
        onChange={(event) => apply(hour, minute, event.target.value)}
      >
        <option>AM</option>
        <option>PM</option>
      </select>
    </div>
  )
}

function ordinal(value: number) {
  const mod100 = value % 100
  const suffix =
    mod100 >= 11 && mod100 <= 13
      ? 'th'
      : (({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[value % 10] ?? 'th')
  return `${value}${suffix}`
}

function schedulePickerValue(trigger: AutomationTrigger): string {
  if (trigger.kind === 'manual') return 'manual'
  if (trigger.kind !== 'schedule') return 'manual'
  if (trigger.type === 'rrule') return 'custom'
  if (trigger.type === 'interval' && Number(trigger.seconds) !== HOURLY_SECONDS) {
    return 'custom'
  }
  return trigger.type
}
