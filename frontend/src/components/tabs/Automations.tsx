import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from '@tanstack/react-router'
import {
  createAutomation,
  deleteAutomation,
  getAutomationRun,
  getAutomationOptions,
  listAutomationRuns,
  listAutomations,
  runAutomation,
  stopAutomationRun,
  updateAutomation,
} from '../../api/automations'
import type { Automation, AutomationDraft, AutomationRun } from '../../api/automations'
import { AUTOMATIONS_REVIEW_DRAFT_SPEC } from '../../lucius/screenCommands'
import { useRegisterScreenCommands } from '../../lucius/useScreenContract'
import { useDataVersion } from '../../hooks/useDataVersion'
import { useViewScroll } from '../../hooks/useViewScroll'
import { Button, ErrorState, Modal, PageHeader, TabSkeleton } from '../ui'
import { AutomationDetail } from './automations/AutomationDetail'
import { AutomationEditorModal } from './automations/AutomationEditorModal'
import { AutomationList, type AutomationSort } from './automations/AutomationList'
import { useKeepAwakeSettled } from './automations/KeepAwakeBanner'
import { ALL_TEMPLATES } from './automations/automationTemplates'
import {
  EMPTY_AUTOMATION_OPTIONS,
  missingTriggerFields,
  type AutomationOptions,
} from './automations/AutomationTriggerSummary'
import './automations.css'

interface AutomationsProps {
  onOpenChat?: (prefill: string) => void
  onOpenConversation?: (sessionId: string, selection: { model: string; effort: string }) => void
}

export function Automations({ onOpenChat, onOpenConversation }: AutomationsProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const search = location.search as { automation?: string; run?: string; template?: string }
  const selectedAutomationId = typeof search.automation === 'string' ? search.automation : null
  const selectedRunId = typeof search.run === 'string' ? search.run : null
  const templateKey = typeof search.template === 'string' ? search.template : null
  const [automations, setAutomations] = useState<Automation[]>([])
  const automationsRef = useRef(automations)
  useEffect(() => {
    automationsRef.current = automations
  }, [automations])
  const [options, setOptions] = useState<AutomationOptions>(EMPTY_AUTOMATION_OPTIONS)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const keepAwakeSettled = useKeepAwakeSettled()
  const [loadError, setLoadError] = useState<string | null>(null)
  const [runs, setRuns] = useState<AutomationRun[]>([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<AutomationSort>('next')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [editor, setEditor] = useState<{
    automation?: Automation
    seed?: Partial<AutomationDraft>
    fromLucius?: boolean
  } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Automation | null>(null)
  const [clearingFinished, setClearingFinished] = useState(false)
  const [clearBusy, setClearBusy] = useState(false)
  const [pendingConversation, setPendingConversation] = useState<{
    automationId: string
    runId: string
  } | null>(null)
  const openConversation = useCallback(
    (sessionId: string, automation: Automation) =>
      onOpenConversation?.(sessionId, {
        model: automation.model || '',
        effort: automation.effort || 'medium',
      }),
    [onOpenConversation],
  )

  // Deep link from a Connectors provider section's "Automate with a template"
  // (?template=<key>): opens the editor prefilled, then clears the param so a
  // refresh/back doesn't reopen it. An unknown key is a silent no-op — no error, no warning.
  useEffect(() => {
    if (!templateKey) return
    const template = ALL_TEMPLATES.find((t) => t.key === templateKey)
    if (template) setEditor({ seed: template })
    void navigate({
      to: '/automations',
      search: ((previous: Record<string, unknown>) => ({
        ...previous,
        template: undefined,
      })) as never,
      replace: true,
    } as never)
  }, [templateKey, navigate])

  useRegisterScreenCommands([
    {
      spec: AUTOMATIONS_REVIEW_DRAFT_SPEC,
      handler: (args) => {
        const draft = commandDraft(args.automation)
        if (!draft) {
          setActionError(
            'Lucius’s draft could not be opened because it is incomplete. Ask Lucius to review it again.',
          )
          return
        }
        setActionError(null)
        setEditor({ seed: draft, fromLucius: true })
      },
    },
  ])

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const response = await listAutomations()
      setAutomations(response.automations)
    } catch (error) {
      setLoadError(messageOf(error, 'Automations could not be loaded.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const loadOptions = useCallback(async () => {
    setOptionsError(null)
    try {
      setOptions(await getAutomationOptions())
    } catch (error) {
      setOptionsError(messageOf(error, 'Automation choices could not be loaded.'))
    }
  }, [])

  // The choices (accounts, payees, instruments) change with any write — a new account, a payee,
  // or the instrument a saved price trigger was just pinned to — so reload them on every change.
  const anyDataV = useDataVersion('*')
  useEffect(() => {
    void loadOptions()
  }, [loadOptions, anyDataV])

  const rememberScroll = useViewScroll(
    selectedAutomationId ? `automation:${selectedAutomationId}` : 'list',
  )

  // Load the history for the selected Automation. Deliberately does NOT depend on
  // selectedRunId: selecting a run is a pure UI move within history that is already
  // loaded, and re-running this on every tap re-fetched the whole list behind a
  // skeleton — the detail page blinked away and back each time a row was tapped.
  useEffect(() => {
    if (!selectedAutomationId) {
      setRuns([])
      setRunsError(null)
      return
    }
    let current = true
    setRunsLoading(true)
    setRunsError(null)
    void listAutomationRuns(selectedAutomationId)
      .then((response) => {
        if (current) setRuns(response.runs)
      })
      .catch((error) => {
        if (current) setRunsError(messageOf(error, 'Run history could not be loaded.'))
      })
      .finally(() => {
        if (current) setRunsLoading(false)
      })
    return () => {
      current = false
    }
  }, [selectedAutomationId])

  // A deep link can name a run that fell off the fetched page. Adopt it SILENTLY —
  // no spinner, no clearing of what is already on screen — by appending just that
  // one run to the list.
  useEffect(() => {
    if (!selectedAutomationId || !selectedRunId) return
    if (runs.some((run) => run.id === selectedRunId)) return
    let current = true
    void getAutomationRun(selectedAutomationId, selectedRunId)
      .then((selectedRun) => {
        if (!current) return
        if (selectedRun.id !== selectedRunId) return
        if (selectedRun.automation_id !== selectedAutomationId) return
        setRuns((prev) =>
          prev.some((run) => run.id === selectedRun.id) ? prev : [...prev, selectedRun],
        )
      })
      .catch(() => {
        // Keep the run absent so the existing not-found state remains authoritative.
      })
    return () => {
      current = false
    }
  }, [selectedAutomationId, selectedRunId, runs])

  const hasLiveRuns = runs.some((run) => run.status === 'running' || run.status === 'waiting')

  // Which conversations are being written to RIGHT NOW used to be derived here (from this
  // tab's own per-automation run poll) and pushed onto the lucius bus for the chat panel to
  // follow. That made the live set correct only while sitting on THIS tab with THIS
  // automation selected — everywhere else, including the Lucius panel the feature exists
  // for, it read empty. `LuciusChatProvider` now polls GET /api/sessions/live directly and
  // is the sole producer; this tab is purely a consumer of the same bus (see
  // frontend/src/state/luciusChat.tsx).

  useEffect(() => {
    const awaitingSession = pendingConversation?.automationId === selectedAutomationId
    // Poll whenever a run is OPEN, not only while one is live. A run's conversation keeps
    // going after the run itself ends — ask "done?" in the dock and Lucius answers, calls
    // update_run_task, and the backend writes it correctly. With the old guard the poll had
    // already stopped (hasLiveRuns false the moment the run finished), so the card sat on
    // whatever it last saw and only a full page refresh revealed the truth. Live
    // 2026-08-11: three update_run_task calls, backend wrote all three, screen unchanged.
    const runOpen = Boolean(selectedRunId)
    if (!selectedAutomationId || (!hasLiveRuns && !awaitingSession && !runOpen)) return
    let current = true
    let refreshing = false
    let terminalRefreshDone = false
    const refresh = async () => {
      if (refreshing) return
      refreshing = true
      try {
        const response = await listAutomationRuns(selectedAutomationId)
        const stillLive = response.runs.some(
          (run) => run.status === 'running' || run.status === 'waiting',
        )
        let refreshedAutomations: Automation[] | null = null
        if (!stillLive && !terminalRefreshDone) {
          terminalRefreshDone = true
          try {
            refreshedAutomations = (await listAutomations()).automations
          } catch {
            // The terminal run is still authoritative; a future page load retries list metadata.
          }
        }
        if (current) {
          if (refreshedAutomations) setAutomations(refreshedAutomations)
          setRuns(response.runs)
          const awaitedRun = pendingConversation
            ? response.runs.find((run) => run.id === pendingConversation.runId)
            : null
          if (awaitedRun?.session_id) {
            setPendingConversation(null)
            const automation = (refreshedAutomations ?? automationsRef.current).find(
              (row) => row.id === selectedAutomationId,
            )
            if (automation) openConversation(awaitedRun.session_id, automation)
          } else if (
            awaitedRun &&
            awaitedRun.status !== 'running' &&
            awaitedRun.status !== 'waiting'
          ) {
            setPendingConversation(null)
          }
        }
      } catch {
        // Keep the last durable history visible; the next tick retries.
      } finally {
        refreshing = false
      }
    }
    const timer = window.setInterval(() => {
      void refresh()
    }, 1_000)
    return () => {
      current = false
      window.clearInterval(timer)
    }
  }, [selectedAutomationId, selectedRunId, hasLiveRuns, pendingConversation, openConversation])

  // Lucius created/edited/deleted/ran an Automation (data_changed 'automations'). Refetch in
  // place — no skeleton — so the list and the open detail's history reflect the write.
  const automationsDataV = useDataVersion('automations')
  useEffect(() => {
    if (automationsDataV === 0) return
    let current = true
    void listAutomations()
      .then((response) => {
        if (current) setAutomations(response.automations)
      })
      .catch(() => {
        // Keep the last list; the next change or a reload retries.
      })
    if (selectedAutomationId) {
      void listAutomationRuns(selectedAutomationId)
        .then((response) => {
          if (current) setRuns(response.runs)
        })
        .catch(() => {})
    }
    return () => {
      current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [automationsDataV])

  const setSelection = (automationId: string | null, runId: string | null = null) => {
    void navigate({
      to: '/automations',
      search: ((previous: Record<string, unknown>) => ({
        ...previous,
        automation: automationId || undefined,
        run: automationId && runId ? runId : undefined,
      })) as never,
      replace: automationId === null,
    } as never)
  }

  const save = async (draft: AutomationDraft) => {
    if (editor?.automation) {
      const saved = await updateAutomation(editor.automation.id, draft)
      setAutomations((rows) => rows.map((row) => (row.id === saved.id ? saved : row)))
    } else {
      const saved = await createAutomation(draft)
      setAutomations((rows) => [saved, ...rows])
      setSelection(saved.id)
    }
    setEditor(null)
  }

  const toggle = async (automation: Automation, requested?: 'active' | 'paused') => {
    const status = requested ?? (automation.status === 'active' ? 'paused' : 'active')
    const previous = automation.status
    setActionError(null)
    setBusyId(automation.id)
    setAutomations((rows) =>
      rows.map((row) => (row.id === automation.id ? { ...row, status } : row)),
    )
    try {
      const saved = await updateAutomation(automation.id, { status })
      setAutomations((rows) => rows.map((row) => (row.id === automation.id ? saved : row)))
    } catch (error) {
      setAutomations((rows) =>
        rows.map((row) => (row.id === automation.id ? { ...row, status: previous } : row)),
      )
      setActionError(messageOf(error, 'Automation could not be updated.'))
    } finally {
      setBusyId(null)
    }
  }

  const runNow = async (automation: Automation) => {
    setActionError(null)
    setBusyId(automation.id)
    try {
      const run = await runAutomation(automation.id)
      setRuns((rows) => [run, ...rows.filter((row) => row.id !== run.id)])
      setSelection(automation.id, run.id)
      if (run.session_id) {
        setPendingConversation(null)
        openConversation(run.session_id, automation)
      } else if (run.status === 'running' || run.status === 'waiting') {
        setPendingConversation({ automationId: automation.id, runId: run.id })
      }
    } catch (error) {
      setActionError(messageOf(error, 'Automation could not be started.'))
    } finally {
      setBusyId(null)
    }
  }

  const stopRun = async (run: AutomationRun) => {
    // No `if (!run.session_id) return` — that silent guard is what made the button dead:
    // nothing was sent and nothing was said. The automation id is always known, and the
    // server resolves which run is actually live.
    setActionError(null)
    try {
      const result = await stopAutomationRun(run.automation_id)
      if (!result.stopped) {
        setActionError(
          result.reason === 'not_running'
            ? 'That run had already finished — nothing to stop.'
            : 'Run could not be stopped.',
        )
      }
    } catch (error) {
      setActionError(messageOf(error, 'Run could not be stopped.'))
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setBusyId(deleteTarget.id)
    setActionError(null)
    try {
      await deleteAutomation(deleteTarget.id)
      setAutomations((rows) => rows.filter((row) => row.id !== deleteTarget.id))
      if (selectedAutomationId === deleteTarget.id) setSelection(null)
      setDeleteTarget(null)
    } catch (error) {
      setActionError(messageOf(error, 'Automation could not be deleted.'))
    } finally {
      setBusyId(null)
    }
  }

  const finishedAutomations = automations.filter((row) => row.status === 'completed')

  const clearFinished = async () => {
    setClearBusy(true)
    setActionError(null)
    const cleared: string[] = []
    try {
      for (const automation of finishedAutomations) {
        await deleteAutomation(automation.id)
        cleared.push(automation.id)
      }
      setClearingFinished(false)
    } catch (error) {
      setActionError(messageOf(error, 'Some finished automations could not be cleared.'))
    } finally {
      setAutomations((rows) => rows.filter((row) => !cleared.includes(row.id)))
      if (selectedAutomationId && cleared.includes(selectedAutomationId)) setSelection(null)
      setClearBusy(false)
    }
  }

  const selected = selectedAutomationId
    ? (automations.find((automation) => automation.id === selectedAutomationId) ?? null)
    : null
  const unknownAutomation = !loading && !loadError && selectedAutomationId && !selected
  const unknownRun =
    !runsLoading && !runsError && selectedRunId && !runs.some((run) => run.id === selectedRunId)

  return (
    <div className="automations-page">
      {/* The shared PageHeader every other tab uses. This one carried its own type scale
          (15.5px title, 12.5px subtitle) and read a size smaller than Overview or Plan.
          The sticky/blur container stays — only the typography is handed to the primitive.
          Dropped entirely once an automation is open: its own <h1> already carries the
          title, and the detail's breadcrumb (desktop only — a phone backs out by swipe or
          the tab bar) is the way back, so repeating "Automations" here is redundancy. */}
      {/* Up while the list loads too — only the data is a skeleton; the title never pops in. */}
      {!selectedAutomationId && (
        <div className="rd-masthead" data-od-id="automations-masthead">
          <PageHeader
            className="a3-masthead-head"
            title="Automations"
            subtitle="Run on a schedule or when something happens. Every run is a conversation you can pick back up."
          />
        </div>
      )}
      {actionError && (
        <div className="a3-action-error" role="alert">
          {actionError}
        </div>
      )}
      {loading || !keepAwakeSettled ? (
        <div className="a3-wrap">
          <TabSkeleton variant="automations" label="Loading automations…" header={false} />
        </div>
      ) : loadError ? (
        <div className="a3-wrap">
          <ErrorState
            title="Automations couldn’t load"
            message={loadError}
            onRetry={() => void load()}
          />
        </div>
      ) : unknownAutomation ? (
        <div className="a3-wrap">
          <ErrorState
            title="Automation not found"
            message="This automation does not exist or is no longer available."
            onRetry={() => setSelection(null)}
          />
        </div>
      ) : selected && runsLoading ? (
        <div className="a3-wrap">
          <TabSkeleton variant="automations" label="Loading automation history…" />
        </div>
      ) : selected && runsError ? (
        <div className="a3-wrap">
          <ErrorState
            title="Run history couldn’t load"
            message={runsError}
            onRetry={() => setSelection(selected.id)}
          />
        </div>
      ) : selected && unknownRun ? (
        <div className="a3-wrap">
          <ErrorState
            title="Run not found"
            message="This run does not belong to the selected automation or is no longer available."
            onRetry={() => setSelection(selected.id)}
          />
        </div>
      ) : selected ? (
        <AutomationDetail
          automation={selected}
          runs={runs}
          options={options}
          selectedRunId={selectedRunId}
          busy={busyId === selected.id}
          onBack={() => setSelection(null)}
          onCloseRun={() => setSelection(selected.id)}
          onSelectRun={(run) => {
            setSelection(selected.id, run.id)
            if (run.session_id) {
              setPendingConversation(null)
              openConversation(run.session_id, selected)
            } else if (run.status === 'running' || run.status === 'waiting') {
              setPendingConversation({ automationId: selected.id, runId: run.id })
            }
          }}
          onEdit={() => setEditor({ automation: selected })}
          onDelete={() => setDeleteTarget(selected)}
          onRunNow={() => void runNow(selected)}
          onStopRun={(run) => void stopRun(run)}
          onStatusChange={(status) => void toggle(selected, status)}
          onSaveToolLists={async (lists) => {
            setBusyId(selected.id)
            setActionError(null)
            try {
              const saved = await updateAutomation(selected.id, lists)
              setAutomations((rows) => rows.map((row) => (row.id === saved.id ? saved : row)))
            } catch (error) {
              setActionError(messageOf(error, 'Tool permissions could not be saved.'))
            } finally {
              setBusyId(null)
            }
          }}
        />
      ) : (
        <AutomationList
          automations={automations}
          options={options}
          query={query}
          onQueryChange={setQuery}
          sort={sort}
          onSortChange={setSort}
          busyId={busyId}
          onOpen={(automation) => {
            rememberScroll()
            setSelection(automation.id)
          }}
          onRunNow={(automation) => void runNow(automation)}
          onToggle={(automation) => void toggle(automation)}
          onEdit={(automation) => setEditor({ automation })}
          onDuplicate={(automation) =>
            setEditor({
              seed: {
                ...automation,
                status: automation.status === 'completed' ? 'active' : automation.status,
                name: `${automation.name} (copy)`,
              },
            })
          }
          onDelete={setDeleteTarget}
          onNew={() => setEditor({ seed: { trigger: { kind: 'manual' } } })}
          onAskLucius={(text) => onOpenChat?.(text)}
          onTemplate={(template) => setEditor({ seed: template })}
          onClearFinished={() => setClearingFinished(true)}
        />
      )}

      <AutomationEditorModal
        open={editor !== null}
        automation={editor?.automation}
        seed={editor?.seed}
        fromLucius={editor?.fromLucius}
        options={options}
        optionsError={optionsError}
        onRetryOptions={() => void loadOptions()}
        onClose={() => setEditor(null)}
        onSubmit={save}
        onAskLucius={(prompt) => onOpenChat?.(prompt)}
      />
      <Modal
        open={deleteTarget !== null}
        title="Delete this automation?"
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button
              variant="danger"
              loading={busyId === deleteTarget?.id}
              onClick={() => void confirmDelete()}
            >
              Delete automation
            </Button>
          </>
        }
      >
        <p>
          “{deleteTarget?.name}” stops running and its run history goes with it. This can’t be
          undone.
        </p>
      </Modal>
      <Modal
        open={clearingFinished}
        title={`Clear ${finishedAutomations.length} finished automation${finishedAutomations.length === 1 ? '' : 's'}?`}
        onClose={() => setClearingFinished(false)}
        footer={
          <>
            <Button onClick={() => setClearingFinished(false)}>Cancel</Button>
            <Button variant="danger" loading={clearBusy} onClick={() => void clearFinished()}>
              Clear finished
            </Button>
          </>
        }
      >
        <p>
          These have no runs left on their schedule. Clearing deletes them and their run history.
          This can’t be undone.
        </p>
      </Modal>
    </div>
  )
}

function messageOf(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function commandDraft(value: unknown): AutomationDraft | null {
  if (!isRecord(value)) return null
  const name = typeof value.name === 'string' ? value.name.trim() : ''
  const instructions = typeof value.instructions === 'string' ? value.instructions.trim() : ''
  const trigger = normalizeAutomationTrigger(value.trigger)
  if (!name || !instructions || !trigger) return null
  if (missingTriggerFields(trigger).length > 0) return null
  const draft: AutomationDraft = { name, instructions, trigger }
  if (typeof value.description === 'string') draft.description = value.description
  if (
    value.permission_mode === 'manual' ||
    value.permission_mode === 'auto' ||
    value.permission_mode === 'skip'
  ) {
    draft.permission_mode = value.permission_mode
  }
  if (typeof value.model === 'string') draft.model = value.model
  if (typeof value.effort === 'string') draft.effort = value.effort
  if (value.status === 'active' || value.status === 'paused') draft.status = value.status
  return draft
}

function normalizeAutomationTrigger(value: unknown): AutomationDraft['trigger'] | null {
  if (!isRecord(value)) return null
  if (value.kind === undefined && value.type === 'manual') return { kind: 'manual' }
  if (
    value.kind === undefined &&
    ['daily', 'weekdays', 'weekly', 'monthly', 'interval', 'once', 'rrule'].includes(
      String(value.type ?? ''),
    )
  ) {
    let trigger: Record<string, unknown> = { ...value, kind: 'schedule' as const }
    if (
      value.type === 'once' &&
      value.at === undefined &&
      typeof value.date === 'string' &&
      typeof value.time === 'string'
    ) {
      const { date, time, ...rest } = trigger
      trigger = { ...rest, at: `${date}T${time}` }
    }
    return isAutomationTrigger(trigger) ? trigger : null
  }
  return isAutomationTrigger(value) ? value : null
}

function isAutomationTrigger(value: unknown): value is AutomationDraft['trigger'] {
  if (!isRecord(value)) return false
  if (value.kind === 'manual') return true
  if (value.kind === 'schedule') {
    return ['daily', 'weekdays', 'weekly', 'monthly', 'interval', 'once', 'rrule'].includes(
      String(value.type ?? ''),
    )
  }
  return (
    value.kind === 'event' &&
    ['txn', 'balance', 'due', 'sync', 'price'].includes(String(value.event ?? '')) &&
    isRecord(value.when) &&
    ['each', 'hour', 'day'].includes(String(value.limit ?? ''))
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
