// Instructions say WHAT Lucius does; the trigger section says WHEN — set here, never read out
// of the instructions' words (ADR 2026-10-11). Each run is told its trigger and its last
// completed run, so instructions stay right whatever cadence is picked later.
import { useEffect, useState } from 'react'
import type { Automation, AutomationDraft, AutomationTrigger } from '../../../api/automations'
import { Button, Modal, ModelPicker } from '../../ui'
import { useModels } from '../../../hooks/useModels'
import { PermissionModeControl, type PermissionMode } from '../../ui'
import { AutomationTriggerFields } from './AutomationTriggerFields'
import { cadencePrompt } from './cadencePrompt'
import {
  AutomationTriggerSummary,
  EMPTY_AUTOMATION_OPTIONS,
  missingTriggerFields,
  type AutomationOptions,
} from './AutomationTriggerSummary'

const DEFAULT_TRIGGER: AutomationTrigger = { kind: 'manual' }

export interface AutomationEditorModalProps {
  open: boolean
  onClose: () => void
  onSubmit: (draft: AutomationDraft) => Promise<unknown> | unknown
  automation?: Automation | null
  seed?: Partial<AutomationDraft> | null
  fromLucius?: boolean
  initialTrigger?: AutomationTrigger
  options?: AutomationOptions
  optionsError?: string | null
  onRetryOptions?: () => void
  onAskLucius?: (prompt: string) => void
}

interface FormState {
  name: string
  description: string
  instructions: string
  trigger: AutomationTrigger
  model: string
  effort: string
  permission_mode: PermissionMode
  fetch_hosts: string
}

function initialState(
  automation?: Automation | null,
  seed?: Partial<AutomationDraft> | null,
  initialTrigger?: AutomationTrigger,
): FormState {
  return {
    name: automation?.name ?? seed?.name ?? '',
    description: automation?.description ?? seed?.description ?? '',
    instructions: automation?.instructions ?? seed?.instructions ?? '',
    trigger: automation?.trigger ?? seed?.trigger ?? initialTrigger ?? DEFAULT_TRIGGER,
    model: automation?.model ?? seed?.model ?? '',
    effort: automation?.effort ?? seed?.effort ?? 'medium',
    permission_mode: automation?.permission_mode ?? seed?.permission_mode ?? 'manual',
    // The owner's list only: never pre-filled from a draft Lucius proposed.
    fetch_hosts: (automation?.fetch_hosts ?? []).join(', '),
  }
}

export function AutomationEditorModal({
  open,
  onClose,
  onSubmit,
  automation,
  seed,
  fromLucius = false,
  initialTrigger,
  options = EMPTY_AUTOMATION_OPTIONS,
  optionsError,
  onRetryOptions,
  onAskLucius,
}: AutomationEditorModalProps) {
  const [form, setForm] = useState(() => initialState(automation, seed, initialTrigger))
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  useEffect(() => {
    if (!open) return
    setForm(initialState(automation, seed, initialTrigger))
    setSubmitError(null)
    setSubmitting(false)
  }, [open, automation, seed, initialTrigger])

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }))
  }

  const missing = missingTriggerFields(form.trigger)
  const valid = Boolean(form.name.trim() && form.instructions.trim() && missing.length === 0)
  // The REAL cross-provider list, same as every other picker. This used to read
  // /api/automations/options, which returned only the instance default, the per-task
  // overrides, and models another Automation already stored — so a model you had not
  // used elsewhere could not be selected here at all.
  const modelInfo = useModels(true)

  const submit = async () => {
    if (!valid || submitting) return
    setSubmitting(true)
    setSubmitError(null)
    const description = form.description.trim() || firstSentence(form.instructions)
    try {
      await onSubmit({
        name: form.name.trim(),
        description,
        instructions: form.instructions.trim(),
        trigger: form.trigger,
        model: form.model,
        effort: form.effort,
        permission_mode: form.permission_mode,
        always_allowed: automation?.always_allowed ?? seed?.always_allowed,
        fetch_hosts: form.fetch_hosts.split(/[\s,]+/).filter(Boolean),
        // A finished one is never re-sent as `completed`; a new trigger re-arms it server-side.
        status:
          automation?.status === 'completed'
            ? undefined
            : (automation?.status ?? seed?.status ?? 'active'),
      })
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Automation could not be saved.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open={open}
      size="lg"
      className="a3-editor-modal"
      title={
        automation ? 'Edit automation' : fromLucius ? 'Confirm Lucius’s draft' : 'New automation'
      }
      onClose={onClose}
      footer={
        <>
          <Button size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!valid}
            loading={submitting}
            onClick={submit}
          >
            {automation ? 'Save changes' : 'Create automation'}
          </Button>
        </>
      }
    >
      <div className="a3-editor-stack">
        {fromLucius && (
          <div className="a3-draft-note">
            <span aria-hidden="true">✦</span>
            <span>
              Drafted from what you typed. Nothing runs until you create it — change anything first.
            </span>
          </div>
        )}
        <div>
          <label className="a3-field-label" htmlFor="automation-name">
            Name <span className="req">*</span>
          </label>
          <input
            id="automation-name"
            className="rd-input"
            data-autofocus
            value={form.name}
            placeholder="Daily briefing"
            onChange={(event) => update('name', event.target.value)}
          />
        </div>
        {automation && (
          <div>
            <label className="a3-field-label" htmlFor="automation-description">
              Description
            </label>
            <input
              id="automation-description"
              className="rd-input"
              value={form.description}
              placeholder="Generated from the instructions when left blank."
              onChange={(event) => update('description', event.target.value)}
            />
          </div>
        )}
        <div>
          <label className="a3-field-label" htmlFor="automation-instructions">
            Instructions <span className="req">*</span>
          </label>
          <div className="a3-instr-box">
            <textarea
              id="automation-instructions"
              value={form.instructions}
              placeholder="What Lucius should do each time it runs — “Summarize my spending since the last run and send it to me.” When it runs is set below."
              onChange={(event) => update('instructions', event.target.value)}
            />
            <div className="a3-instr-foot">
              <PermissionModeControl
                value={form.permission_mode}
                onChange={(mode) => update('permission_mode', mode)}
              />
              <span className="grow" />
              <ModelPicker
                id="automation-model"
                label="Model"
                size="sm"
                models={modelInfo.models}
                value={form.model}
                defaultLabel="Default"
                defaultModelId={modelInfo.defaultModel}
                onChange={(next) => update('model', next)}
              />
              <select
                id="automation-effort"
                aria-label="Effort"
                className="a3-mini-select"
                value={form.effort}
                onChange={(event) => update('effort', event.target.value)}
              >
                <option value="low">Low effort</option>
                <option value="medium">Medium effort</option>
                <option value="high">High effort</option>
              </select>
            </div>
          </div>
        </div>
        <div>
          <span className="a3-field-label">
            When it runs <span className="req">*</span>
          </span>
          <AutomationTriggerSummary trigger={form.trigger} options={options} />
        </div>
        <AutomationTriggerFields
          trigger={form.trigger}
          options={options}
          optionsError={optionsError}
          onRetryOptions={onRetryOptions}
          onAskForCadence={
            onAskLucius
              ? () =>
                  onAskLucius(cadencePrompt(form.name, form.instructions, automation ?? undefined))
              : undefined
          }
          onChange={(trigger) => update('trigger', trigger)}
        />
        <div>
          <label className="a3-field-label" htmlFor="automation-fetch-hosts">
            Websites it may open without asking
          </label>
          <input
            id="automation-fetch-hosts"
            className="rd-input"
            value={form.fetch_hosts}
            placeholder="store.steampowered.com, receipts.example.com"
            onChange={(event) => update('fetch_hosts', event.target.value)}
          />
          <p className="a3-field-hint">
            Lucius asks before opening any other link it found in an email or a page, because a
            planted link can carry your data out. Subdomains are included.
          </p>
        </div>
        {submitError && (
          <div className="a3-inline-error" role="alert">
            {submitError}
          </div>
        )}
      </div>
    </Modal>
  )
}

function firstSentence(value: string) {
  const sentence = value.trim().split(/(?<=[.!?])\s|\n/)[0] ?? ''
  return sentence.length > 160 ? `${sentence.slice(0, 157).trim()}…` : sentence
}
