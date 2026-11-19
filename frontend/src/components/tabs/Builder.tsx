import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  getBuilderRun,
  getBuilderStatus,
  startBuilderRun,
  stopBuilderRun,
  type BuilderEngineId,
  type BuilderEvent,
  type BuilderMode,
  type BuilderRun,
  type BuilderStatus,
} from '../../api/builder'
import { useBrand } from '../../brand-context'
import { FileCode, ShieldCheck, LuciusMark, Wrench } from '../../ui/icons'
import {
  Badge,
  Button,
  Cluster,
  Container,
  ErrorState,
  FormField,
  Modal,
  PageHeader,
  Panel,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TabSkeleton,
  TextArea,
} from '../ui'
import './builder.css'

const STARTERS = [
  {
    label: 'Add something new',
    prompt: 'Add a new dashboard card that helps me understand my monthly cash flow at a glance.',
  },
  {
    label: 'Change how it looks',
    prompt:
      'Refresh the visual style of this screen while keeping it consistent with the rest of Open Net Worth.',
  },
  {
    label: 'Improve a workflow',
    prompt: 'Make the transaction review flow faster and easier for someone who is not technical.',
  },
] as const

const MODE_OPTIONS: { value: BuilderMode; label: string }[] = [
  { value: 'plan', label: 'Plan' },
  { value: 'build', label: 'Build' },
]

const ACTIVE = new Set(['queued', 'running'])

function mergeEvents(current: BuilderEvent[], incoming: BuilderEvent[]): BuilderEvent[] {
  const bySequence = new Map(current.map((event) => [event.seq, event]))
  for (const event of incoming) bySequence.set(event.seq, event)
  return [...bySequence.values()].sort((a, b) => a.seq - b.seq)
}

function mergeRun(current: BuilderRun | null, incoming: BuilderRun): BuilderRun {
  return {
    ...incoming,
    events: mergeEvents(current?.events ?? [], incoming.events ?? []),
  }
}

function runLabel(status: BuilderRun['status']): string {
  if (status === 'succeeded') return 'Finished'
  if (status === 'failed') return 'Needs attention'
  if (status === 'cancelled') return 'Stopped'
  if (status === 'queued') return 'Queued'
  return 'Working'
}

function runTone(status: BuilderRun['status']): 'pos' | 'neg' | 'warn' | 'neutral' {
  if (status === 'succeeded') return 'pos'
  if (status === 'failed') return 'neg'
  if (status === 'cancelled') return 'warn'
  return 'neutral'
}

function eventGlyph(kind: BuilderEvent['kind']): ReactNode {
  if (kind === 'assistant') return <LuciusMark size="1em" aria-hidden="true" />
  if (kind === 'tool') return '›'
  if (kind === 'output') return '•'
  if (kind === 'error') return '!'
  return '○'
}

function reviewTitle(run: BuilderRun): string {
  if (run.status === 'succeeded') {
    return run.mode === 'plan' ? 'Plan ready to review' : 'Change ready to review'
  }
  if (run.status === 'failed') return 'Run needs attention'
  if (run.status === 'cancelled') return 'Run stopped'
  return 'Work in progress'
}

function reviewSummary(run: BuilderRun): string {
  if (run.error) return run.error
  const assistantResult = [...run.events].reverse().find((event) => event.kind === 'assistant')
  if (assistantResult) return assistantResult.text
  if (run.status === 'cancelled') return 'The coding engine stopped before finishing.'
  return run.mode === 'plan'
    ? 'Review the plan and technical activity below.'
    : 'Review the outcome and current workspace changes below.'
}

export function Builder({ pollIntervalMs = 1000 }: { pollIntervalMs?: number }) {
  const brand = useBrand()
  const [status, setStatus] = useState<BuilderStatus | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [engine, setEngine] = useState<BuilderEngineId>('codex')
  const [mode, setMode] = useState<BuilderMode>('plan')
  const [prompt, setPrompt] = useState('')
  const [run, setRun] = useState<BuilderRun | null>(null)
  const [actionError, setActionError] = useState('')
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [confirmingBuild, setConfirmingBuild] = useState(false)
  const pollAbort = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    setLoadError(false)
    try {
      const next = await getBuilderStatus()
      setStatus(next)
      const firstAvailable = next.engines.find((item) => item.installed)
      if (firstAvailable) setEngine(firstAvailable.id)
      if (next.active_run_id) {
        try {
          setRun(await getBuilderRun(next.active_run_id))
        } catch {
          // A process-local run can disappear after a server restart; readiness still works.
        }
      }
    } catch {
      setLoadError(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const lastSequence = run?.events.at(-1)?.seq ?? 0
  useEffect(() => {
    if (!run || !ACTIVE.has(run.status)) return
    let disposed = false
    const controller = new AbortController()
    pollAbort.current = controller
    const timer = window.setTimeout(async () => {
      try {
        const next = await getBuilderRun(run.id, lastSequence, controller.signal)
        if (!disposed) setRun((current) => mergeRun(current, next))
      } catch (error) {
        if (!disposed && !controller.signal.aborted) {
          setActionError(error instanceof Error ? error.message : 'Could not refresh this run.')
        }
      }
    }, pollIntervalMs)
    return () => {
      disposed = true
      window.clearTimeout(timer)
      controller.abort()
      if (pollAbort.current === controller) pollAbort.current = null
    }
  }, [lastSequence, pollIntervalMs, run])

  const selectedEngine = useMemo(
    () => status?.engines.find((item) => item.id === engine) ?? null,
    [engine, status],
  )
  const hasInstalledEngine = status?.engines.some((item) => item.installed) ?? false
  const active = !!run && ACTIVE.has(run.status)
  const canStart =
    !!prompt.trim() && !!selectedEngine?.installed && !!status?.workspace_valid && !active

  async function start() {
    if (!canStart) return
    setConfirmingBuild(false)
    setStarting(true)
    setActionError('')
    try {
      const created = await startBuilderRun({ engine, mode, prompt: prompt.trim() })
      setRun(created)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not start the coding engine.')
    } finally {
      setStarting(false)
    }
  }

  function requestStart() {
    if (mode === 'build') {
      setConfirmingBuild(true)
      return
    }
    void start()
  }

  async function stop() {
    if (!run) return
    pollAbort.current?.abort()
    setStopping(true)
    setActionError('')
    try {
      const stopped = await stopBuilderRun(run.id)
      setRun((current) => mergeRun(current, stopped))
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not stop this run.')
    } finally {
      setStopping(false)
    }
  }

  if (loadError) {
    return (
      <Container size="default" className="builder-page">
        <ErrorState message="Could not check coding engines." onRetry={() => void load()} />
      </Container>
    )
  }
  if (!status)
    return (
      <Container size="wide" className="builder-page">
        <Stack gap="--space-6">
          <TabSkeleton variant="builder" label="Checking local coding engines…" />
        </Stack>
      </Container>
    )

  const appName = brand.app_name || 'Open Net Worth'
  const progressEvents =
    run?.events.filter((event) => event.kind !== 'tool' && event.kind !== 'output') ?? []
  const technicalEvents =
    run?.events.filter((event) => event.kind === 'tool' || event.kind === 'output') ?? []

  return (
    <Container size="wide" className="builder-page">
      <Stack gap="--space-6">
        <PageHeader
          title={`Build ${appName}`}
          subtitle="Describe the outcome. Your local coding engine can plan it—or make it real in this checkout."
          actions={
            <Badge tone={status.workspace_valid ? 'pos' : 'neg'}>
              {status.workspace_valid ? 'Workspace ready' : 'Workspace unavailable'}
            </Badge>
          }
        />

        <div className="builder-thesis">
          <div className="builder-thesis__mark" aria-hidden="true">
            <LuciusMark size={20} strokeWidth={1.5} />
          </div>
          <div className="builder-thesis__copy">
            <Text as="h2" size="2xl" weight={600}>
              Make it yours.
            </Text>
            <Text as="p" size="sm" tone="muted">
              Start in plain language. Open the activity trail when you want the technical detail.
            </Text>
          </div>
          <div
            className={`builder-rail${active ? ' builder-rail--live' : ''}`}
            aria-label="Build sequence"
          >
            <span>
              <b>1</b> Engine
            </span>
            <i aria-hidden="true" />
            <span>
              <b>2</b> Authority
            </span>
            <i aria-hidden="true" />
            <span>
              <b>3</b> Run
            </span>
          </div>
        </div>

        <div className="builder-layout">
          <Stack gap="--space-5" className="builder-main">
            {!hasInstalledEngine ? (
              <Panel>
                <Stack gap="--space-5">
                  <div>
                    <Text as="h2" size="lg" weight={600}>
                      Set up a coding engine
                    </Text>
                    <Text as="p" size="sm" tone="muted">
                      Install one engine, sign in locally, then come back here. Open Net Worth uses
                      that engine&apos;s existing login.
                    </Text>
                  </div>
                  <div className="builder-setup__engines">
                    {status.engines.map((item) => (
                      <div key={item.id} className="builder-engine">
                        <div className="builder-engine__icon" aria-hidden="true">
                          <FileCode size={18} strokeWidth={1.5} />
                        </div>
                        <div className="builder-engine__body">
                          <Text weight={600}>Install {item.label}</Text>
                          <Text as="p" size="sm" tone="muted">
                            {item.guidance}
                          </Text>
                        </div>
                      </div>
                    ))}
                  </div>
                  <Cluster justify="between" align="center" className="builder-submit">
                    <Text size="xs" tone="faint">
                      Open Net Worth never receives your engine credentials.
                    </Text>
                    <Button variant="primary" onClick={() => void load()}>
                      Check again
                    </Button>
                  </Cluster>
                </Stack>
              </Panel>
            ) : (
              <Panel className="builder-compose">
                <Stack gap="--space-5">
                  <div>
                    <Text as="h2" size="lg" weight={600}>
                      What should change?
                    </Text>
                    <Text as="p" size="sm" tone="muted">
                      Pick a starting point, or describe your own outcome.
                    </Text>
                  </div>
                  <Cluster gap="--space-2" className="builder-starters">
                    {STARTERS.map((starter) => (
                      <Button
                        key={starter.label}
                        size="sm"
                        variant="ghost"
                        onClick={() => setPrompt(starter.prompt)}
                        disabled={active}
                      >
                        {starter.label}
                      </Button>
                    ))}
                  </Cluster>
                  <FormField label={`What should ${appName} change?`} htmlFor="builder-prompt">
                    <TextArea
                      id="builder-prompt"
                      rows={7}
                      value={prompt}
                      onChange={(event) => setPrompt(event.target.value)}
                      placeholder="For example: Add a simple way to compare this month with last month…"
                      disabled={active}
                    />
                  </FormField>

                  <div className="builder-authority">
                    <div className="builder-authority__control">
                      <Text as="div" size="xs" tone="faint" weight={600}>
                        How far should it go?
                      </Text>
                      <SegmentedControl
                        options={MODE_OPTIONS}
                        value={mode}
                        onChange={setMode}
                        ariaLabel="Builder authority"
                      />
                    </div>
                    <div
                      className={`builder-authority__explain builder-authority__explain--${mode}`}
                    >
                      {mode === 'plan' ? (
                        <ShieldCheck size={18} strokeWidth={1.5} aria-hidden="true" />
                      ) : (
                        <Wrench size={18} strokeWidth={1.5} aria-hidden="true" />
                      )}
                      <Text size="sm">
                        {mode === 'plan'
                          ? 'Plan reads the app and recommends an approach. It cannot change files.'
                          : `Build can edit files and run local commands in this app's fixed workspace.`}
                      </Text>
                    </div>
                  </div>

                  {actionError && (
                    <Text as="p" size="sm" tone="bad">
                      {actionError}
                    </Text>
                  )}
                  <Cluster justify="between" align="center" className="builder-submit">
                    <Text size="xs" tone="faint">
                      No release actions are provided in Builder.
                    </Text>
                    {active ? (
                      <Button variant="danger" onClick={() => void stop()} loading={stopping}>
                        Stop run
                      </Button>
                    ) : (
                      <Button
                        variant="primary"
                        onClick={requestStart}
                        loading={starting}
                        disabled={!canStart}
                      >
                        {mode === 'plan' ? 'Plan this change' : 'Build this change'}
                      </Button>
                    )}
                  </Cluster>
                </Stack>
              </Panel>
            )}

            {run && (
              <Panel
                title="Activity"
                badge={<Badge tone={runTone(run.status)}>{runLabel(run.status)}</Badge>}
              >
                {!active && (
                  <div
                    className={`builder-result builder-result--${runTone(run.status)}`}
                    role="status"
                  >
                    <Text as="h2" size="lg" weight={600}>
                      {reviewTitle(run)}
                    </Text>
                    <Text as="p" size="sm" tone="muted">
                      {reviewSummary(run)}
                    </Text>
                  </div>
                )}
                {run.events.length === 0 ? (
                  <Text size="sm" tone="muted">
                    Waiting for the coding engine…
                  </Text>
                ) : (
                  <>
                    {progressEvents.length > 0 && (
                      <ol className="builder-events" aria-live="polite">
                        {progressEvents.map((event) => (
                          <li
                            key={event.seq}
                            className={`builder-event builder-event--${event.kind}`}
                          >
                            <span className="builder-event__glyph" aria-hidden="true">
                              {eventGlyph(event.kind)}
                            </span>
                            <Text as="div" size="sm" className="builder-event__text">
                              {event.text}
                            </Text>
                          </li>
                        ))}
                      </ol>
                    )}
                    {technicalEvents.length > 0 && (
                      <details className="builder-technical">
                        <summary>Show technical activity ({technicalEvents.length})</summary>
                        <ol className="builder-events">
                          {technicalEvents.map((event) => (
                            <li
                              key={event.seq}
                              className={`builder-event builder-event--${event.kind}`}
                            >
                              <span className="builder-event__glyph" aria-hidden="true">
                                {eventGlyph(event.kind)}
                              </span>
                              <Text as="div" size="sm" className="builder-event__text">
                                {event.text}
                              </Text>
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}
                  </>
                )}
              </Panel>
            )}
          </Stack>

          <Stack gap="--space-5" className="builder-side">
            {hasInstalledEngine && (
              <Panel title="Coding engine" subtitle="Uses your local subscription login.">
                <Stack gap="--space-4">
                  <FormField label="Engine" htmlFor="builder-engine">
                    <Select
                      id="builder-engine"
                      value={engine}
                      onChange={(event) => setEngine(event.target.value as BuilderEngineId)}
                      disabled={active}
                      options={status.engines.map((item) => ({
                        value: item.id,
                        label: item.installed ? item.label : `${item.label} — not installed`,
                        disabled: !item.installed,
                      }))}
                    />
                  </FormField>
                  <Stack gap="--space-3">
                    {status.engines.map((item) => (
                      <div
                        key={item.id}
                        className={`builder-engine${item.id === engine ? ' builder-engine--selected' : ''}`}
                      >
                        <div className="builder-engine__icon" aria-hidden="true">
                          <FileCode size={18} strokeWidth={1.5} />
                        </div>
                        <div className="builder-engine__body">
                          <Cluster justify="between" align="center">
                            <Text weight={600}>{item.label}</Text>
                            <Badge tone={item.installed ? 'pos' : 'muted'}>
                              {item.installed ? 'Ready' : 'Not installed'}
                            </Badge>
                          </Cluster>
                          {item.installed ? (
                            <Text as="div" size="xs" tone="faint" className="builder-mono">
                              {item.version || item.command}
                            </Text>
                          ) : (
                            <Text as="p" size="xs" tone="muted">
                              {item.guidance}
                            </Text>
                          )}
                          {item.installed && (
                            <Text as="div" size="xs" tone="faint">
                              {item.build_is_sandboxed
                                ? 'Workspace sandbox available'
                                : 'Uses Claude Code edit permissions'}
                            </Text>
                          )}
                        </div>
                      </div>
                    ))}
                  </Stack>
                </Stack>
              </Panel>
            )}

            <Panel title="Workspace" subtitle="Fixed by the server; the browser cannot change it.">
              <div className="builder-workspace">
                <span className="builder-workspace__dot" aria-hidden="true" />
                <Text size="sm" className="builder-mono">
                  {status.workspace}
                </Text>
              </div>
              {status.error && (
                <Text as="p" size="sm" tone="bad">
                  {status.error}
                </Text>
              )}
            </Panel>

            {run && run.workspace_changes.length > 0 && (
              <Panel
                title="Workspace changes"
                subtitle="Current Git status; it may include work from before this run."
              >
                <ul className="builder-changes">
                  {run.workspace_changes.map((change) => (
                    <li key={change}>
                      <Text size="xs" className="builder-mono">
                        {change}
                      </Text>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </Stack>
        </div>

        <Modal
          open={confirmingBuild}
          onClose={() => setConfirmingBuild(false)}
          size="sm"
          title={`Let ${selectedEngine?.label ?? 'this engine'} edit ${appName}?`}
          footer={
            <Cluster justify="end" gap="--space-2">
              <Button variant="ghost" onClick={() => setConfirmingBuild(false)}>
                Go back
              </Button>
              <Button variant="primary" onClick={() => void start()} loading={starting}>
                Start building
              </Button>
            </Cluster>
          }
        >
          <Stack gap="--space-4">
            <Text as="p" size="sm" tone="muted">
              Build can edit files and run local commands inside the fixed Open Net Worth workspace.
            </Text>
            <div className="builder-confirm-boundary">
              <Text as="div" size="xs" tone="faint" weight={600}>
                FIXED WORKSPACE
              </Text>
              <Text as="div" size="sm" className="builder-mono">
                {status.workspace}
              </Text>
              <Text as="p" size="sm">
                The engine is instructed not to commit, push, open a pull request, or deploy.
                Builder does not provide those release actions.
              </Text>
            </div>
          </Stack>
        </Modal>
      </Stack>
    </Container>
  )
}
