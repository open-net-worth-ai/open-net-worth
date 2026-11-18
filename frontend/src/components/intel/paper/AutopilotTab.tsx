import { useState } from 'react'
import { Button, FormField, SegmentedControl, Select, TextArea } from '../../ui'
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

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })

/** A paper portfolio's autopilot: Manual (only you and your chat requests trade) or Auto (Lucius
 *  reviews it on a schedule, trades within its mandate, and leaves a memo of each review). Laid
 *  out as the other tabs are: the switch, the figures, then Settings and Reviews. */
export function AutopilotTab({
  autopilot: a,
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
  const auto = a.mode === 'auto'

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
  const trades = a.runs.reduce((n, r) => n + r.orders.length, 0)
  const last = a.runs[0]

  return (
    <div className="paper-sections">
      <div className="fol-sec">
        {!archived && (
          <div className="paper-ap-bar">
            <SegmentedControl
              ariaLabel="Autopilot"
              tone="primary"
              value={a.mode}
              onChange={(m: AutopilotMode) =>
                m === a.mode
                  ? undefined
                  : void act(
                      'mode',
                      () => setPaperAutopilot(portfolioId, { mode: m }),
                      m === 'auto' ? 'Autopilot on' : 'Autopilot off',
                    )
              }
              options={[
                { value: 'manual', label: 'Manual' },
                { value: 'auto', label: 'Auto' },
              ]}
            />
            {auto && (
              <Button
                size="sm"
                variant="secondary"
                disabled={a.running || !mandate}
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
        )}
        <p className={`paper-next${a.paused_reason ? ' bad' : ''}`}>{statusLine(a)}</p>
        {!archived && !mandate && (
          <div>
            <Button size="sm" variant="primary" onClick={onEditMandate}>
              Set a mandate
            </Button>
          </div>
        )}
        {error && <p className="paper-next bad">{error}</p>}
        {(auto || a.runs.length > 0) && (
          <div className="fol-keyfigs">
            <div className="fol-ret3 paper-ap-figs">
              <Figure
                label="Next review"
                value={auto && a.next_run_at && !a.running ? when(a.next_run_at) : '—'}
              />
              <Figure label="Reviews" value={String(a.runs.length)} />
              <Figure label="Trades placed" value={String(trades)} />
              <Figure label="Last review" value={last ? when(last.finished_at) : '—'} />
            </div>
          </div>
        )}
      </div>
      {auto && !archived && (
        <section className="fol-sec">
          <h2 className="fol-sec-title">Settings</h2>
          <Settings
            autopilot={a}
            saving={busy === 'save'}
            onSave={(body) => void act('save', () => setPaperAutopilot(portfolioId, body), 'Saved')}
          />
        </section>
      )}
      <section className="fol-sec">
        <h2 className="fol-sec-title">Reviews</h2>
        {a.runs.length ? (
          <ol className="paper-ap-reviews">
            {a.runs.map((r) => (
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

function statusLine(a: PaperAutopilot): string {
  if (a.mode === 'manual')
    return 'Only you and your chat requests trade. Auto has Lucius review this portfolio on a schedule and trade within its mandate.'
  if (a.paused_reason) return a.paused_reason
  if (a.running) return 'Lucius is reviewing it now. The memo appears below when he is done.'
  return 'Lucius reviews this portfolio on a schedule and trades within its mandate.'
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="fol-ret3-item">
      <span className="fol-ret3-l">{label}</span>
      <span className="fol-ret3-v">{value}</span>
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
    <li className="paper-ap-review">
      <div className="paper-ap-review-when">
        <span>{when(run.finished_at)}</span>
        <span className={failed ? 'paper-mandate-broken' : 'paper-ap-outcome'}>{outcome(run)}</span>
      </div>
      <div className="paper-ap-review-body">
        <p>{failed ? (run.error ?? 'Unknown error.') : run.memo}</p>
        {run.orders.map((o) => (
          <p key={o.id} className="paper-ap-review-order">
            {o.label}
          </p>
        ))}
      </div>
    </li>
  )
}
