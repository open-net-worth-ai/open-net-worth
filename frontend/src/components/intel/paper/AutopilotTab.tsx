import { useState } from 'react'
import { Button, FormField, SegmentedControl, Select, TextArea } from '../../ui'
import { fDate } from '../../../utils/format'
import {
  runPaperAutopilot,
  setPaperAutopilot,
  type AutopilotCadence,
  type AutopilotMode,
  type AutopilotRun,
  type PaperAutopilot,
} from '../../../api/paperTrading'

const CADENCE: { value: AutopilotCadence; label: string }[] = [
  { value: 'daily', label: 'Every weekday at 10:00' },
  { value: 'weekly', label: 'Every Monday at 10:00' },
]

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
  hasMandate,
  archived,
  onEditMandate,
  onNotice,
  portfolioId,
}: {
  autopilot: PaperAutopilot
  hasMandate: boolean
  archived: boolean
  onEditMandate: () => void
  onNotice: (text: string) => void
  portfolioId: string
}) {
  const [cadence, setCadence] = useState<AutopilotCadence>(autopilot.cadence)
  const [instructions, setInstructions] = useState(autopilot.instructions)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const auto = autopilot.mode === 'auto'
  const changed = cadence !== autopilot.cadence || instructions.trim() !== autopilot.instructions

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
      () => setPaperAutopilot(portfolioId, { mode, cadence, instructions: instructions.trim() }),
      mode === 'auto' ? 'Autopilot on' : 'Autopilot off',
    )

  return (
    <div className="paper-sections">
      {!archived && (
        <div className="paper-autopilot-mode">
          <SegmentedControl
            ariaLabel="Autopilot"
            tone="primary"
            value={autopilot.mode}
            onChange={(m) => (m === autopilot.mode ? undefined : setMode(m))}
            options={[
              { value: 'manual', label: 'Manual' },
              { value: 'auto', label: 'Auto' },
            ]}
          />
          <p className="paper-next">{statusLine(autopilot)}</p>
        </div>
      )}
      {!archived && !hasMandate && (
        <div className="paper-autopilot-need">
          <p className="paper-next">
            Set a mandate first: autopilot trades only within the limits you set.
          </p>
          <Button size="sm" variant="primary" onClick={onEditMandate}>
            Set a mandate
          </Button>
        </div>
      )}
      {!archived && hasMandate && auto && (
        <form
          className="paper-form paper-autopilot-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (changed)
              void act(
                'save',
                () =>
                  setPaperAutopilot(portfolioId, { cadence, instructions: instructions.trim() }),
                'Saved',
              )
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
          <div className="paper-actions">
            <Button
              size="sm"
              variant="secondary"
              type="submit"
              disabled={!changed}
              loading={busy === 'save'}
            >
              Save
            </Button>
            {auto && (
              <Button
                size="sm"
                variant="secondary"
                type="button"
                disabled={autopilot.running}
                loading={busy === 'run'}
                onClick={() =>
                  void act(
                    'run',
                    () => runPaperAutopilot(portfolioId),
                    'Review starts within a minute',
                  )
                }
              >
                Review now
              </Button>
            )}
          </div>
        </form>
      )}
      {error && <div className="paper-next bad">{error}</div>}
      <section className="fol-sec">
        <h2 className="fol-sec-title">Reviews</h2>
        {autopilot.runs.length ? (
          <div className="paper-feed">
            {autopilot.runs.map((r) => (
              <Review key={r.id} run={r} />
            ))}
          </div>
        ) : (
          <p className="fol-by">No reviews yet</p>
        )}
      </section>
    </div>
  )
}

function statusLine(a: PaperAutopilot): string {
  if (a.mode === 'manual')
    return 'Only you and your chat requests trade. Auto has Lucius review this portfolio on a schedule and trade within its mandate.'
  if (a.paused_reason) return a.paused_reason
  if (a.running) return 'Reviewing now…'
  const next = a.next_run_at ? `Next review ${when(a.next_run_at)}. ` : ''
  return `${next}Each review places at most ${a.max_orders_per_run} orders and never breaks the mandate.`
}

function Review({ run }: { run: AutopilotRun }) {
  return (
    <div className="paper-feed-item">
      <div>
        <div className={run.status === 'failed' ? 'paper-mandate-broken' : undefined}>
          {run.status === 'failed' ? `Didn't finish: ${run.error ?? 'unknown error'}` : run.memo}
        </div>
        {run.orders.map((o) => (
          <div key={o.id} className="note">
            {o.label}
          </div>
        ))}
      </div>
      <div className="when">{fDate(run.finished_at, 'short', 'auto')}</div>
    </div>
  )
}
