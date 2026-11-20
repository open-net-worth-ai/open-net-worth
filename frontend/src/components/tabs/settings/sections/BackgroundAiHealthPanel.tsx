// Settings → AI usage → Background AI tasks. How each background AI task (Intel analysis, news
// screening, briefings, …) has been doing over the last 24 hours, over GET
// /api/ai/background-health — an in-memory record that resets when the server restarts
// (backend/core/background_ai_health.py), so the panel says since when it counts.
//
// Why it exists: these calls failed in silence. On 2026-11-19 Claude refused Intel analysis for
// six days and the only sign was takes quietly going stale. Same ds.tsx primitives and table
// classes as ModelTelemetryPanel, so the two read as one surface.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { timeAgo } from '../../../../utils/format'
import { Badge, Btn, Empty, Panel, useSectionBusy } from '../ds'
import type { BadgeTone } from '../ds'

export interface BackgroundAiTask {
  source: string
  label: string
  status: 'working' | 'recovering' | 'failing'
  calls_24h: number
  failures_24h: number
  last_success_at: string | null
  last_failure_at: string | null
  problem: string | null
}

interface BackgroundAiHealth {
  since?: string
  tasks?: BackgroundAiTask[]
}

const HEALTH_PATH = '/api/ai/background-health'

const STATUS: Record<BackgroundAiTask['status'], { label: string; tone: BadgeTone }> = {
  working: { label: 'Working', tone: 'good' },
  recovering: { label: 'Recovering', tone: 'warn' },
  failing: { label: 'Failing', tone: 'bad' },
}

async function getJson<T>(path: string): Promise<T> {
  const r = await authFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

function TaskRow({ task }: { task: BackgroundAiTask }) {
  const status = STATUS[task.status] ?? STATUS.working
  return (
    <tr>
      <th className="rd-telemetry-model" scope="row">
        <span className="rd-telemetry-row-label">{task.label}</span>
        {task.problem ? <span className="rd-telemetry-row-samples">{task.problem}</span> : null}
      </th>
      <td className="rd-telemetry-reliability" data-label="Status">
        <Badge tone={status.tone}>{status.label}</Badge>
      </td>
      <td className="rd-telemetry-metric" data-label="Last 24 hours">
        {task.failures_24h} of {task.calls_24h} failed
      </td>
      <td className="rd-telemetry-metric" data-label="Last success">
        {task.last_success_at ? timeAgo(task.last_success_at) : 'Never'}
      </td>
    </tr>
  )
}

export function BackgroundAiHealthPanel() {
  const [retrying, setRetrying] = useState(false)
  const query = useCachedFetchState<BackgroundAiHealth>(
    HEALTH_PATH,
    () => getJson<BackgroundAiHealth>(HEALTH_PATH),
    { staleTime: 30_000 },
  )
  useSectionBusy(query.initialLoading && !query.data)

  const tasks = Array.isArray(query.data?.tasks) ? query.data.tasks : []
  const since = query.data?.since
  const showError = Boolean(query.error && !query.data)
  const showEmpty = !showError && !query.initialLoading && tasks.length === 0

  async function retry() {
    setRetrying(true)
    try {
      await query.refresh()
    } finally {
      setRetrying(false)
    }
  }

  return (
    <Panel
      title="Background AI tasks"
      sub="Work the AI does on its own — Intel analysis, news screening, briefings — over the last 24 hours"
    >
      <span className="rd-help">
        Counted since the server started{since ? ` (${timeAgo(since)})` : ''}; a restart clears it.
        A failing task skips its work until the AI answers again.
      </span>

      {showError ? (
        <Empty
          title="Could not load background AI tasks"
          body="Something went wrong reaching the server."
          action={
            <Btn size="xs" onClick={() => void retry()}>
              {retrying ? 'Retrying…' : 'Retry'}
            </Btn>
          }
        />
      ) : showEmpty ? (
        <Empty
          title="No background AI tasks have run yet"
          body="Each task appears here after its first AI call."
        />
      ) : (
        <div className="rd-telemetry-table-wrap">
          <table className="rd-telemetry-table" aria-label="Background AI tasks">
            <thead>
              <tr>
                <th scope="col">Task</th>
                <th scope="col">Status</th>
                <th scope="col">Last 24 hours</th>
                <th scope="col">Last success</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <TaskRow key={task.source} task={task} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
