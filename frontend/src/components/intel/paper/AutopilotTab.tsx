import { useState } from 'react'
import {
  Button,
  CollapsibleSection,
  FormField,
  SegmentedControl,
  Select,
  Tag,
  TextArea,
} from '../../ui'
import { Spark } from '../../../lucius/identity'
import {
  runPaperAutopilot,
  setPaperAutopilot,
  type AutopilotCadence,
  type AutopilotMode,
  type AutopilotRun,
  type PaperAutopilot,
  type PaperMandate,
} from '../../../api/paperTrading'

const CADENCE: { value: AutopilotCadence; label: string }[] = [
  { value: 'daily', label: 'Every weekday at 10:00' },
  { value: 'weekly', label: 'Every Monday at 10:00' },
]
// Monday first, as the schedule reads: which days a review runs on.
const WEEK = ['M', 'T', 'W', 'T', 'F', 'S', 'S']
const reviewsOn = (c: AutopilotCadence) => (c === 'daily' ? [0, 1, 2, 3, 4] : [0])
const mondayIndex = (iso: string) => (new Date(iso).getDay() + 6) % 7

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })

/** A paper portfolio's autopilot: Manual (only you and your chat requests trade) or Auto (Lucius
 *  reviews it on a schedule, trades within its mandate, and leaves a memo of each review). */
export function AutopilotTab({
  autopilot,
  mandate,
  archived,
  onEditMandate,
  onNotice,
  portfolioId,
}: {
  autopilot: PaperAutopilot
  mandate: PaperMandate | null
  archived: boolean
  onEditMandate: () => void
  onNotice: (text: string) => void
  portfolioId: string
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const auto = autopilot.mode === 'auto'

  async function act(key: string, fn: () => Promise<unknown>, done: string) {
    setBusy(key)
    setError(null)
    try {
      await fn()
      onNotice(done)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }
  const setMode = (mode: AutopilotMode) =>
    void act(
      'mode',
      () => setPaperAutopilot(portfolioId, { mode }),
      mode === 'auto' ? 'Autopilot on' : 'Autopilot off',
    )

  return (
    <div className="paper-sections">
      <Hero
        autopilot={autopilot}
        mandate={mandate}
        archived={archived}
        busy={busy}
        onMode={setMode}
        onReview={() =>
          void act('run', () => runPaperAutopilot(portfolioId), 'Review starts within a minute')
        }
        onEditMandate={onEditMandate}
      />
      {error && <div className="paper-next bad">{error}</div>}
      {auto && !archived && (
        <CollapsibleSection title="Settings">
          <Settings
            autopilot={autopilot}
            saving={busy === 'save'}
            onSave={(body) => void act('save', () => setPaperAutopilot(portfolioId, body), 'Saved')}
          />
        </CollapsibleSection>
      )}
      <section className="fol-sec">
        <h2 className="fol-sec-title">Reviews</h2>
        {autopilot.runs.length ? (
          <ol className="paper-ap-timeline">
            {autopilot.runs.map((r) => (
              <Review key={r.id} run={r} />
            ))}
          </ol>
        ) : (
          <p className="fol-by">
            {auto ? 'The first review appears here once it runs.' : 'No reviews yet'}
          </p>
        )}
      </section>
    </div>
  )
}

function Hero({
  autopilot: a,
  mandate,
  archived,
  busy,
  onMode,
  onReview,
  onEditMandate,
}: {
  autopilot: PaperAutopilot
  mandate: PaperMandate | null
  archived: boolean
  busy: string | null
  onMode: (m: AutopilotMode) => void
  onReview: () => void
  onEditMandate: () => void
}) {
  const auto = a.mode === 'auto'
  const state = !auto ? 'off' : a.paused_reason ? 'paused' : a.running ? 'running' : 'on'
  const trades = a.runs.reduce((n, r) => n + r.orders.length, 0)
  const last = a.runs[0]
  return (
    <div className="paper-ap-hero" data-state={state}>
      <div className="paper-ap-head">
        <span className="paper-ap-orb" aria-hidden="true">
          <Spark size={22} filled={auto} />
        </span>
        <div className="paper-ap-id">
          <div className="paper-ap-title">{TITLE[state]}</div>
          <div className="paper-ap-sub">{subLine(a)}</div>
        </div>
        {!archived && (
          <div className="paper-ap-controls">
            {auto && (
              <Button
                size="sm"
                variant="secondary"
                disabled={a.running || !mandate}
                loading={busy === 'run'}
                onClick={onReview}
              >
                Review now
              </Button>
            )}
            <SegmentedControl
              ariaLabel="Autopilot"
              tone="primary"
              value={a.mode}
              onChange={(m) => (m === a.mode ? undefined : onMode(m))}
              options={[
                { value: 'manual', label: 'Manual' },
                { value: 'auto', label: 'Auto' },
              ]}
            />
          </div>
        )}
      </div>
      {!mandate && !archived ? (
        <div className="paper-ap-need">
          <span>Set a mandate first: autopilot trades only within the limits you set.</span>
          <Button size="sm" variant="primary" onClick={onEditMandate}>
            Set a mandate
          </Button>
        </div>
      ) : (
        <div className="paper-ap-stats">
          <Stat label="Reviews" value={String(a.runs.length)} />
          <Stat label="Trades placed" value={String(trades)} />
          <Stat
            label="Last review"
            value={last ? when(last.finished_at) : '—'}
            sub={last ? outcome(last) : undefined}
          />
          <div className="paper-ap-stat">
            <span className="paper-ap-stat-l">Reviews on</span>
            <Week cadence={a.cadence} next={auto ? a.next_run_at : null} dim={!auto} />
          </div>
        </div>
      )}
      {mandate && <MandateLine mandate={mandate} />}
    </div>
  )
}

const TITLE: Record<string, string> = {
  off: 'You are trading this portfolio',
  on: 'Lucius is running this portfolio',
  running: 'Lucius is reviewing it now',
  paused: 'Autopilot is paused',
}

function subLine(a: PaperAutopilot): string {
  if (a.mode === 'manual')
    return 'Only you and your chat requests trade. Auto has Lucius review it on a schedule and trade within its mandate.'
  if (a.paused_reason) return a.paused_reason
  if (a.running)
    return 'Reading the portfolio and the market. The memo appears below when he is done.'
  return a.next_run_at
    ? `Next review ${when(a.next_run_at)}. He never breaks the mandate.`
    : 'He never breaks the mandate.'
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="paper-ap-stat">
      <span className="paper-ap-stat-l">{label}</span>
      <span className="paper-ap-stat-v">{value}</span>
      {sub && <span className="paper-ap-stat-s">{sub}</span>}
    </div>
  )
}

/** The week, Monday first: the days a review runs are filled, the next one ringed. */
function Week({
  cadence,
  next,
  dim,
}: {
  cadence: AutopilotCadence
  next: string | null
  dim: boolean
}) {
  const on = reviewsOn(cadence)
  const nextDay = next ? mondayIndex(next) : -1
  return (
    <div
      className="paper-ap-week"
      role="img"
      data-dim={dim ? '' : undefined}
      aria-label={cadence === 'daily' ? 'Every weekday' : 'Every Monday'}
    >
      {WEEK.map((d, i) => (
        <span
          key={i}
          className="paper-ap-day"
          data-on={on.includes(i) ? '' : undefined}
          data-next={i === nextDay ? '' : undefined}
        >
          {d}
        </span>
      ))}
    </div>
  )
}

function MandateLine({ mandate: m }: { mandate: PaperMandate }) {
  const parts = [
    m.max_position_pct != null && `max ${m.max_position_pct}% per holding`,
    m.max_sector_pct != null && `max ${m.max_sector_pct}% per sector`,
    m.min_cash_pct != null && `at least ${m.min_cash_pct}% cash`,
    m.max_positions != null && `at most ${m.max_positions} holdings`,
    m.stop_loss_pct != null && `stop loss ${m.stop_loss_pct}%`,
    !!m.universe?.length && `buys only ${m.universe.join(', ')}`,
  ].filter(Boolean) as string[]
  if (!m.goal && !parts.length) return null
  return (
    <div className="paper-ap-mandate">
      {m.goal && <span className="paper-ap-goal">{m.goal}</span>}
      {parts.length > 0 && (
        <span className="paper-ap-limits">
          {parts.map((p) => (
            <span key={p} className="paper-ap-limit">
              {p}
            </span>
          ))}
        </span>
      )}
    </div>
  )
}

function Settings({
  autopilot,
  saving,
  onSave,
}: {
  autopilot: PaperAutopilot
  saving: boolean
  onSave: (body: { cadence: AutopilotCadence; instructions: string }) => void
}) {
  const [cadence, setCadence] = useState<AutopilotCadence>(autopilot.cadence)
  const [instructions, setInstructions] = useState(autopilot.instructions)
  const changed = cadence !== autopilot.cadence || instructions.trim() !== autopilot.instructions
  return (
    <form
      className="paper-form paper-autopilot-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (changed) onSave({ cadence, instructions: instructions.trim() })
      }}
    >
      <FormField label="How often" htmlFor="pp-ap-cadence">
        <Select
          id="pp-ap-cadence"
          value={cadence}
          options={CADENCE}
          onChange={(e) => setCadence(e.target.value as AutopilotCadence)}
        />
      </FormField>
      <FormField label="Anything Lucius should keep in mind (optional)" htmlFor="pp-ap-notes">
        <TextArea
          id="pp-ap-notes"
          rows={2}
          maxLength={600}
          placeholder="Add to winners on pullbacks; leave the fund alone."
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
        />
      </FormField>
      <div>
        <Button size="sm" variant="primary" type="submit" disabled={!changed} loading={saving}>
          Save
        </Button>
      </div>
    </form>
  )
}

function outcome(r: AutopilotRun): string {
  if (r.status === 'failed') return "Didn't finish"
  if (!r.orders.length) return 'No trades'
  return r.orders.length === 1 ? '1 trade' : `${r.orders.length} trades`
}

function Review({ run }: { run: AutopilotRun }) {
  const failed = run.status === 'failed'
  return (
    <li
      className="paper-ap-entry"
      data-state={failed ? 'failed' : run.orders.length ? 'traded' : 'quiet'}
    >
      <span className="paper-ap-dot" aria-hidden="true" />
      <div className="paper-ap-entry-head">
        <span className="paper-ap-when">{when(run.finished_at)}</span>
        <Tag tone={failed ? 'warn' : run.orders.length ? 'accent' : 'muted'}>{outcome(run)}</Tag>
      </div>
      <p className="paper-ap-memo">{failed ? (run.error ?? 'Unknown error.') : run.memo}</p>
      {run.orders.length > 0 && (
        <div className="paper-ap-orders">
          {run.orders.map((o) => (
            <span
              key={o.id}
              className="paper-ap-order"
              data-side={/^(Sold|Sell)/.test(o.label) ? 'sell' : 'buy'}
            >
              {o.label}
            </span>
          ))}
        </div>
      )}
    </li>
  )
}
