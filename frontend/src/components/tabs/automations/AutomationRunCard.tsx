import { Check, ChevronRight, FileText, Folder, Sparkles, X } from 'lucide-react'
import { useToolOwnerLogos } from '../../chat/toolActivity/useToolOwnerLogos'
import { useAccounts } from '../../../hooks/serverState/useAccounts'
import { AccIcon } from '../accounts/ds'
import { TYPE_COLOR, TYPE_ICON, type AccountType } from '../accounts/categories'
import type { AutomationRun } from '../../../api/automations'
import { money, tokenCount } from './AutomationCostPanel'
import { fDateTime } from '../../../utils/format'

/** Which of the four states a task row is in.
 *
 * `in_progress` used to render identically to `pending` — the run said it had started a
 * task and the card showed no difference, so the one row that answers "where is it right
 * now" was the one row you could not find. */
function stepState(step: { done?: boolean; status?: string }): string {
  if (step.done) return 'done'
  if (step.status === 'in_progress' || step.status === 'running') return 'doing'
  if (step.status === 'failed') return 'failed'
  return 'todo'
}

export function AutomationRunCard({ run, onClose }: { run: AutomationRun; onClose: () => void }) {
  const context = contextGroups(run.context ?? {})
  // ONLY the run's own checklist. No fallback to the tool trail: "Fetch emails / List
  // accounts / Set run plan" is a record of mechanics, not progress, and showing it under
  // the same heading taught the reader to distrust the section. A run from before
  // set_run_plan simply has no progress to show, and shows none. The trail is still on the
  // row for debugging.
  const progress = run.plan ?? []
  const duration = durationLabel(run.started_at, run.finished_at)
  // The CLI reports total_cost_usd even on a Pro/Max login, where it is a LIST PRICE for
  // usage the plan already covers — Anthropic's docs say it "isn't relevant for billing".
  // The backend stamps that as cost_basis="plan_included"; showing the number without the
  // stamp is what made a run read as a $14.20 charge.
  const planIncluded = run.stats?.cost_basis === 'plan_included'
  const files = run.files ?? []
  return (
    <article
      className={`a3-runcard${run.status === 'failed' ? ' bad' : ''}`}
      data-od-id="open-run"
      aria-label={`Run ${fDateTime(run.started_at, 'short', 'always')}`}
    >
      <div className="top">
        <span className="when">Run • {fDateTime(run.started_at, 'short', 'always')}</span>
        <span className="grow" />
        <button
          type="button"
          className="a3-iconbtn sm"
          onClick={onClose}
          aria-label="Close this run"
          title="Close this run"
        >
          <X size={14} />
        </button>
      </div>
      {run.error && (
        <div className="err" role="alert">
          {run.error}
        </div>
      )}
      {/* A cancelled run has no `error` on purpose — being stopped is not a failure, so the
          red banner would be wrong. But `summary` was the only thing carrying the
          explanation, and nothing rendered it: a run cancelled by a server restart showed a
          "Stopped" chip, a checklist frozen mid-task, and no reason anywhere (owner,
          2026-08-21). Neutral notice, not an alert. */}
      {run.status === 'cancelled' && run.summary.trim() !== '' && (
        <div className="note">{run.summary}</div>
      )}
      {progress.length === 0 ? (
        // A run that has not written its task list yet — or a short one that never will.
        // The section stays put and says so, rather than appearing out of nowhere a second
        // later and shoving the rest of the card down the page.
        <section className="rsec">
          <div className="rsec-h">
            <b>Progress</b>
          </div>
          <div className="a3-step-ghosts" aria-hidden="true">
            <span className="a3-step-ghost">
              <Check size={10} />
            </span>
            <span className="a3-step-link" />
            <span className="a3-step-ghost">
              <Check size={10} />
            </span>
            <span className="a3-step-link" />
            <span className="a3-step-ghost a3-step-ghost--pending" />
          </div>
          <div className="a3-step-hint">Task progress appears here for longer runs.</div>
        </section>
      ) : (
        <section className="rsec">
          <div className="rsec-h">
            <b>Progress</b>
            <span>
              {progress.filter((step) => step.done).length} of {progress.length}
            </span>
          </div>
          {progress.map((step, index) => (
            <div key={`${step.label}-${index}`} className={`a3-step ${stepState(step)}`}>
              <span className="box">
                {step.done ? <Check size={10} aria-hidden="true" /> : index + 1}
              </span>
              <span className="l">{step.label}</span>
            </div>
          ))}
        </section>
      )}
      {(run.working_dir || files.length > 0) && (
        <section className="rsec">
          <div className="rsec-h">
            <b>Working folder</b>
          </div>
          {run.working_dir && (
            <div className="a3-file">
              <span className="kind">
                <Folder size={12} />
              </span>
              <span className="n">{run.working_dir}</span>
            </div>
          )}
          {files.map((file, index) => {
            const name = typeof file === 'string' ? file : file.name
            return (
              <div className="a3-file" key={`${name}-${index}`}>
                <span className="kind">
                  <FileText size={12} aria-hidden="true" />
                </span>
                <span className="n">{name}</span>
              </div>
            )
          })}
        </section>
      )}
      <details className="rsec" open>
        <summary className="rsec-h as-btn">
          <b>Context</b>
          <ChevronRight size={11} />
        </summary>
        <div className="a3-context-groups">
          {context.hasAny ? (
            <>
              {context.connectors.length > 0 && (
                <ContextPills label="Connectors" values={context.connectors} brand />
              )}
              {context.accounts.length > 0 && (
                <ContextPills label="Accounts it read" values={context.accounts} accounts />
              )}
              {context.skills.length > 0 && <ContextFiles label="Skills" values={context.skills} />}
              {context.remembered.length > 0 && (
                <ContextFiles label="What it remembered" values={context.remembered} remembered />
              )}
            </>
          ) : (
            <p className="a3-muted">No context recorded.</p>
          )}
        </div>
      </details>
      <section className="rsec">
        <div className="rsec-h">
          <b>Cost of this run</b>
        </div>
        {run.cost_usd == null && run.input_tokens == null && run.output_tokens == null ? (
          <p className="a3-muted">No cost data yet.</p>
        ) : (
          <div className="a3-costgrid">
            <RunMetric
              label={planIncluded ? 'Total (plan-included)' : 'Total'}
              value={money(run.cost_usd)}
            />
            <RunMetric label="Tokens in" value={tokenCount(run.input_tokens)} />
            <RunMetric label="Tokens out" value={tokenCount(run.output_tokens)} />
            {/* Cache is almost the whole story on a long run and was previously hidden, so
                "42 in / 6.3K out" sat next to $14.20 and read as a billing error. It was
                2.1M cache reads. */}
            {run.cache_read_tokens != null && (
              <RunMetric label="Cache read" value={tokenCount(run.cache_read_tokens)} />
            )}
            {run.cache_creation_tokens != null && (
              <RunMetric label="Cache write" value={tokenCount(run.cache_creation_tokens)} />
            )}
            <RunMetric
              label="Model"
              value={
                [String(run.stats?.model || run.model || ''), titleCase(run.effort)]
                  .filter(Boolean)
                  .join(' • ') || '—'
              }
            />
            <RunMetric label="Took" value={duration ?? '—'} />
          </div>
        )}
        {planIncluded && (
          <p className="a3-muted">
            Your Claude plan already covers this usage — the figure is what it would have cost on
            API pricing, not a charge.
          </p>
        )}
      </section>
    </article>
  )
}

function RunMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="k">{label}</div>
      <div className="v">{value}</div>
    </div>
  )
}

function durationLabel(start: string, finish: string | null) {
  if (!finish) return null
  const duration = Date.parse(finish) - Date.parse(start)
  if (!Number.isFinite(duration) || duration < 0) return null
  if (duration < 60_000) return `${Math.round(duration / 1000)}s`
  return `${Math.floor(duration / 60_000)}m ${Math.round((duration % 60_000) / 1000)}s`
}

function ContextPills({
  label,
  values,
  brand = false,
  accounts = false,
}: {
  label: string
  values: string[]
  brand?: boolean
  accounts?: boolean
}) {
  const lookup = useToolOwnerLogos(brand ? values : [])
  const { data: book } = useAccounts()
  const typeByName = new Map(
    (accounts ? (book ?? []) : []).map((a) => [String(a.name ?? '').toLowerCase(), String(a.type)]),
  )
  return (
    <div className="ctxg">
      <span className="ctxk">{label}</span>
      <div className="a3-conn">
        {values.map((item) => {
          if (accounts) {
            const type = typeByName.get(item.toLowerCase()) as AccountType | undefined
            return (
              <span key={item} className="a3-conn-brand">
                {type && TYPE_ICON[type] && (
                  <span className="a3-conn-type" style={{ color: TYPE_COLOR[type] }}>
                    <AccIcon name={TYPE_ICON[type]} size={13} />
                  </span>
                )}
                {item}
              </span>
            )
          }
          if (!brand) return <span key={item}>{item}</span>
          const logo = lookup(item.toLowerCase().replace(/\s+/g, '_')).logo
          return (
            <span key={item} className="a3-conn-brand">
              {logo ? (
                <img src={logo} alt="" className="a3-conn-logo" />
              ) : (
                <i className="a3-conn-logo a3-conn-initial" aria-hidden="true">
                  {item.charAt(0).toUpperCase()}
                </i>
              )}
              {item}
            </span>
          )
        })}
      </div>
    </div>
  )
}

function ContextFiles({
  label,
  values,
  remembered = false,
}: {
  label: string
  values: string[]
  remembered?: boolean
}) {
  return (
    <div className="ctxg">
      <span className="ctxk">{label}</span>
      {values.map((item) => (
        <div className="a3-file" key={item}>
          <span className={`kind${remembered ? ' remembered' : ''}`}>
            {remembered ? <Sparkles size={11} /> : <FileText size={12} />}
          </span>
          <span className="n">{item}</span>
        </div>
      ))}
    </div>
  )
}

function contextGroups(context: Record<string, unknown>) {
  const connectors = strings(context.connectors)
  const accounts = strings(context.accounts ?? context.accounts_read)
  const skills = strings(context.skills)
  const remembered = strings(context.remembered ?? context.memory)
  return {
    connectors,
    accounts,
    skills,
    remembered,
    hasAny: connectors.length + accounts.length + skills.length + remembered.length > 0,
  }
}

function strings(value: unknown): string[] {
  if (Array.isArray(value))
    return value.map((item) =>
      typeof item === 'object' && item
        ? String(
            (item as { label?: unknown; name?: unknown }).label ??
              (item as { name?: unknown }).name ??
              item,
          )
        : String(item),
    )
  if (typeof value === 'string' && value) return [value]
  return []
}

function titleCase(value: string) {
  return value ? value[0]!.toUpperCase() + value.slice(1) : ''
}
