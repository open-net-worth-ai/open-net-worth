import '../../tabs/automations.css'

import { useState } from 'react'
import { ChevronRight, Clock3, Pencil, Play, Zap } from 'lucide-react'
import { Button, FormField, Modal, Switch, TextArea, groupModels, modelRefOf } from '../../ui'
import { FieldSelect } from '../../tabs/connectors/VoicePicker'
import type { SelectOptGroup } from '../../ui'
import type { ModelOption } from '../../../api/models'
import { useModels } from '../../../hooks/useModels'
import { fDateTime } from '../../../utils/format'
import {
  runPaperAutopilot,
  setPaperAutopilot,
  type AutopilotCadence,
  type AutopilotEffort,
  type AutopilotRun,
  type PaperAutopilot,
  type PaperMandate,
} from '../../../api/paperTrading'

const CADENCE: { value: AutopilotCadence; label: string }[] = [
  { value: 'daily', label: 'Every weekday at 10:00' },
  { value: 'weekly', label: 'Every Monday at 10:00' },
]

const EFFORT: { value: AutopilotEffort; label: string }[] = [
  { value: 'low', label: 'Low effort' },
  { value: 'medium', label: 'Medium effort' },
  { value: 'high', label: 'High effort' },
]

/** The model choices as Select groups: Default first (naming what it is now), then each
 *  provider's models. A stored model the list no longer offers stays selectable. */
function modelName(models: ModelOption[], ref: string): string {
  const m = models.find((x) => modelRefOf(x) === ref || x.id === ref)
  return m ? `${m.id} · ${m.provider_label ?? m.provider_key ?? ''}`.replace(/ · $/, '') : ref
}

function modelOptions(
  models: ModelOption[],
  defaultRef: string | undefined,
  current: string,
): SelectOptGroup[] {
  const name = (ref: string) => modelName(models, ref)
  const groups: SelectOptGroup[] = [
    {
      group: 'Default',
      options: [{ value: '', label: defaultRef ? `Default (${name(defaultRef)})` : 'Default' }],
    },
    // "Preferred" repeats models listed under their provider: one place each.
    ...groupModels(models)
      .filter((g) => g.label !== 'Preferred')
      .map((g) => ({
        group: g.label,
        options: g.models.map((m) => ({ value: modelRefOf(m), label: m.label ?? m.id })),
      })),
  ]
  const known = groups.some((g) => g.options.some((o) => o.value === current))
  if (current && !known)
    groups.push({ group: 'Saved', options: [{ value: current, label: current }] })
  return groups
}

const at = (iso: string) => fDateTime(iso, 'short', 'always')
const effortLabel = (e?: string) =>
  `${(e || 'medium')[0]!.toUpperCase()}${(e || 'medium').slice(1)} effort`
/** "Default", or the model's own name without its provider. */
const modelLabel = (m?: string) => (m ? (m.split(':').pop() ?? m) : 'Default')

/** A paper portfolio's autopilot, laid out as an Automation's page is (the same classes): the
 *  on/off switch and Review now, then what it works within and its schedule on the left, and
 *  its history on the right, where a review opens to show Lucius's memo and its trades. */
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
  const [editing, setEditing] = useState(false)
  const models = useModels(true)
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

  return (
    <div className="paper-ap">
      <div className="a3-detail-bar">
        <div className="a3-detail-state">
          {!archived && (
            <Switch
              id={`paper-autopilot-${portfolioId}`}
              checked={auto}
              disabled={busy === 'mode' || (!auto && !mandate)}
              disabledReason={!auto && !mandate ? 'Set a mandate first' : undefined}
              onChange={() =>
                void act(
                  'mode',
                  () => setPaperAutopilot(portfolioId, { mode: auto ? 'manual' : 'auto' }),
                  auto ? 'Autopilot off' : 'Autopilot on',
                )
              }
              aria-label={auto ? 'Turn autopilot off' : 'Turn autopilot on'}
            />
          )}
          <span className={a.paused_reason ? 'paper-mandate-broken' : undefined}>
            {stateLine(a)}
          </span>
        </div>
        {!archived && (
          <div className="a3-detail-actions">
            <button
              type="button"
              className="a3-iconbtn"
              aria-label="Edit autopilot"
              title="Edit autopilot"
              onClick={() => setEditing(true)}
            >
              <Pencil size={14} aria-hidden="true" />
            </button>
            {auto && (
              <Button
                size="sm"
                variant="primary"
                iconLeft={<Play size={13} aria-hidden="true" />}
                disabled={a.running}
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
      </div>
      {error && <p className="paper-next bad">{error}</p>}
      <div className="a3-cols">
        <div className="a3-col-setup">
          <section className="a3-block">
            <div className="a3-block-h">Works within</div>
            {mandate ? (
              <Mandate mandate={mandate} onEdit={archived ? undefined : onEditMandate} />
            ) : (
              <>
                <p className="a3-muted">
                  Autopilot needs a mandate: the goal and the limits every trade keeps.
                </p>
                {!archived && (
                  <div>
                    <Button size="sm" variant="secondary" onClick={onEditMandate}>
                      Set a mandate
                    </Button>
                  </div>
                )}
              </>
            )}
          </section>
          <section className="a3-block">
            <div className="a3-block-h">Schedule</div>
            <div className="a3-trigger-detail">
              <Clock3 size={14} aria-hidden="true" />
              <span>{CADENCE.find((c) => c.value === a.cadence)?.label}</span>
            </div>
          </section>
          <section className="a3-block">
            <div className="a3-block-h">Model</div>
            <div className="a3-model-detail">
              <Zap size={14} aria-hidden="true" />
              <b>
                {a.model
                  ? modelName(models.models, a.model)
                  : models.defaultModel
                    ? `Default (${modelName(models.models, models.defaultModel)})`
                    : 'Default'}
              </b>
              <span>•</span>
              <span>{effortLabel(a.effort)}</span>
            </div>
          </section>
          <section className="a3-block">
            <div className="a3-block-h">Instructions</div>
            {a.instructions ? (
              <p className="paper-ap-goal">{a.instructions}</p>
            ) : (
              <p className="a3-muted">None. Lucius follows the mandate.</p>
            )}
          </section>
          <section className="a3-block">
            <div className="a3-block-h">How it trades</div>
            <p className="a3-muted">
              {auto
                ? 'Lucius trades only this paper portfolio, with pretend money, and never breaks its mandate. Two failed reviews in a row pause it.'
                : 'Off: only you, and Lucius when you ask him in chat, trade this portfolio. On: Lucius reviews it on a schedule, trades within its mandate, and writes down why.'}
            </p>
          </section>
        </div>
        <section className="a3-block a3-col-history">
          <div className="a3-block-h">
            History <span className="count">{reviewCount(a.runs.length)}</span>
          </div>
          <History runs={a.runs} auto={auto} />
        </section>
      </div>
      {editing && (
        <SettingsDialog
          autopilot={a}
          saving={busy === 'save'}
          onClose={() => setEditing(false)}
          onSave={(body) =>
            void act(
              'save',
              async () => {
                await setPaperAutopilot(portfolioId, body)
                setEditing(false)
              },
              'Saved',
            )
          }
        />
      )}
    </div>
  )
}

function stateLine(a: PaperAutopilot): string {
  if (a.mode === 'manual') return 'Autopilot off'
  if (a.paused_reason) return a.paused_reason
  if (a.running) return 'Reviewing now'
  return a.next_run_at ? `Next review: ${at(a.next_run_at)}` : 'Autopilot on'
}

const reviewCount = (n: number) => (n === 1 ? '1 review' : `${n} reviews`)

function Mandate({ mandate: m, onEdit }: { mandate: PaperMandate; onEdit?: () => void }) {
  const limits = [
    m.max_position_pct != null && `At most ${m.max_position_pct}% in one holding`,
    m.max_sector_pct != null && `At most ${m.max_sector_pct}% in one sector`,
    m.min_cash_pct != null && `At least ${m.min_cash_pct}% in cash`,
    m.max_positions != null && `At most ${m.max_positions} holdings`,
    m.stop_loss_pct != null && `Sell a holding down ${m.stop_loss_pct}% from cost`,
    !!m.universe?.length && `Buys only ${m.universe.join(', ')}`,
  ].filter(Boolean) as string[]
  return (
    <>
      {m.goal && <p className="paper-ap-goal">{m.goal}</p>}
      {limits.length > 0 && (
        <ul className="paper-ap-limits">
          {limits.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}
      {onEdit && (
        <div>
          <button type="button" className="a3-linkbtn" onClick={onEdit}>
            Edit mandate
          </button>
        </div>
      )}
    </>
  )
}

/** The autopilot's settings, edited as an Automation's are: in a dialog, not on the page. */
function SettingsDialog({
  autopilot,
  saving,
  onClose,
  onSave,
}: {
  autopilot: PaperAutopilot
  saving: boolean
  onClose: () => void
  onSave: (body: {
    cadence: AutopilotCadence
    instructions: string
    model: string
    effort: AutopilotEffort
  }) => void
}) {
  const [cadence, setCadence] = useState<AutopilotCadence>(autopilot.cadence)
  const [instructions, setInstructions] = useState(autopilot.instructions)
  const [model, setModel] = useState(autopilot.model)
  const [effort, setEffort] = useState<AutopilotEffort>(autopilot.effort)
  const models = useModels(true)
  const changed =
    cadence !== autopilot.cadence ||
    instructions.trim() !== autopilot.instructions ||
    model !== autopilot.model ||
    effort !== autopilot.effort
  return (
    <Modal
      open
      onClose={onClose}
      title="Edit autopilot"
      size="md"
      footer={
        <>
          <Button size="sm" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!changed}
            loading={saving}
            onClick={() => onSave({ cadence, instructions: instructions.trim(), model, effort })}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="paper-form">
        <FormField label="Schedule" htmlFor="pp-ap-cadence">
          <FieldSelect
            id="pp-ap-cadence"
            value={cadence}
            options={CADENCE}
            onChange={(e) => setCadence(e.target.value as AutopilotCadence)}
          />
        </FormField>
        <div className="paper-ap-model">
          <FormField label="Model" htmlFor="pp-ap-model">
            <FieldSelect
              id="pp-ap-model"
              value={model}
              options={modelOptions(models.models, models.defaultModel, model)}
              onChange={(e) => setModel(e.target.value)}
            />
          </FormField>
          <FormField label="Effort" htmlFor="pp-ap-effort">
            <FieldSelect
              id="pp-ap-effort"
              value={effort}
              options={EFFORT}
              onChange={(e) => setEffort(e.target.value as AutopilotEffort)}
            />
          </FormField>
        </div>
        <FormField label="Instructions (optional)" htmlFor="pp-ap-notes">
          <TextArea
            id="pp-ap-notes"
            rows={3}
            maxLength={600}
            placeholder="Anything Lucius should keep in mind, such as: add to winners on pullbacks; leave the fund alone."
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </FormField>
      </div>
    </Modal>
  )
}

function outcome(r: AutopilotRun): string {
  if (r.status === 'failed') return "Didn't finish"
  if (!r.orders.length) return 'No trades'
  return r.orders.length === 1 ? '1 trade' : `${r.orders.length} trades`
}

/** Each review as an Automation run row; the open one shows Lucius's memo and its trades. */
function History({ runs, auto }: { runs: AutopilotRun[]; auto: boolean }) {
  const [open, setOpen] = useState<string | null>(runs[0]?.id ?? null)
  if (!runs.length)
    return (
      <div className="a3-history-empty">
        {auto ? 'The first review shows here once it runs.' : 'No reviews yet.'}
      </div>
    )
  return (
    <div className="a3-hist">
      {runs.map((r) => {
        const on = open === r.id
        return (
          <div key={r.id}>
            <button
              type="button"
              className={`a3-hist-row${on ? ' on' : ''}`}
              aria-expanded={on}
              onClick={() => setOpen(on ? null : r.id)}
            >
              <span className={`rd-dot ${r.status === 'failed' ? 'bad' : 'good'}`} />
              <span className="when">{at(r.finished_at)}</span>
              <span className="note">{outcome(r)}</span>
              <ChevronRight
                size={13}
                className={`a3-hist-chevron paper-ap-chev${on ? ' on' : ''}`}
                aria-hidden="true"
              />
            </button>
            {on && (
              <div className="paper-ap-run">
                <p>{r.status === 'failed' ? (r.error ?? 'Unknown error.') : r.memo}</p>
                {r.watching && r.status !== 'failed' && (
                  <p className="paper-ap-watching">Watching: {r.watching}</p>
                )}
                {(r.looked_up?.length ?? 0) > 0 && (
                  <p className="paper-ap-watching">
                    Looked up: {(r.looked_up ?? []).map(lookupLabel).join('; ')}
                  </p>
                )}
                <div className="a3-muted">
                  {r.trigger ? `Started by: ${r.trigger}` : 'Started on schedule'} • Ran on{' '}
                  {modelLabel(r.model)} • {effortLabel(r.effort)}
                </div>
                {r.orders.length > 0 && (
                  <ul className="paper-ap-trades">
                    {r.orders.map((o) => (
                      <li key={o.id}>{o.label}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

const LOOKUP_KIND: Record<string, string> = {
  web_search: 'searched',
  x_search: 'searched X for',
  yahoo_get_price_history: 'price history of',
  coingecko_get_price_history: 'price history of',
}

function lookupLabel(l: { tool: string; query: string }): string {
  return `${LOOKUP_KIND[l.tool] ?? l.tool} “${l.query}”`
}
