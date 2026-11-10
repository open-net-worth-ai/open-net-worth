import { useEffect, useMemo } from 'react'
import {
  useModels,
  effortChoicesFor,
  clampEffortChoice,
  effortChoiceLabel,
  type EffortChoice,
} from '../../../hooks/useModels'
import { ModelPicker } from '../../ui'
import { fUsd, sumSessionCost } from '../../../utils/costFormat'
import type { Message } from '../../../types/chat'
import { Check } from '../../../ui/icons'

// The model/effort/cost cluster: [cost] [model picker] [effort dropdown].
//
// Originally a ROW of its own above the composer (owner request 2026-07-11). It now rides
// INSIDE the composer's toolbar row, right-aligned against Send (owner request 2026-08-06) —
// both the dock and the side panel pass it as LuciusComposer's `controls`, so the whole input
// is one block instead of a control strip stacked on a text field.
//
// Model and effort now come from LuciusChatProvider's shared reactive state, so the dock,
// side panel, and send path update in the same render. Effort is a dropdown here (not the
// side panel's 4-button segmented control) because dock width is limited.

export type EffortLevel = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

interface Props {
  /** The conversation's raw stored model ('' | '<id>' | '<key>:<id>'). */
  storedModel: string
  onModelChange: (value: string) => void
  /** Conversation messages — for the running session-cost figure. */
  messages: Message[]
  effort: EffortChoice
  onEffortChange: (effort: EffortChoice) => void
}

export function ModelEffortCluster({
  storedModel,
  onModelChange,
  messages,
  effort,
  onEffortChange,
}: Props) {
  const modelInfo = useModels(true)
  // Explicit human picks stay authoritative even if their provider is later disabled; the
  // backend then rejects them loudly. Automation fallbacks are validated before they reach
  // this shared state, so only those safely collapse to the instance default.
  const activeModel = storedModel

  // Effort drives only the reasoning budget now — show exactly the tiers the model
  // accepts (plus a leading "Off" where the model can honestly disable reasoning), hide
  // the dial when none. Full ladder until models load (no flicker, no premature Off).
  const effortChoices = useMemo(
    () =>
      modelInfo.loaded
        ? effortChoicesFor(activeModel, modelInfo.models, modelInfo.defaultModel)
        : (['low', 'medium', 'high', 'xhigh', 'max'] as EffortChoice[]),
    [activeModel, modelInfo.loaded, modelInfo.models, modelInfo.defaultModel],
  )
  const showEffort = effortChoices.length > 0
  // Clamp a carried-over selection to the model's choices on a switch (a carried "Off"
  // falls to the lowest real tier where Off isn't offered), persisting it (shared
  // 'chat-effort' key) so the send path agrees.
  useEffect(() => {
    if (!showEffort) return
    const clamped = clampEffortChoice(effort, effortChoices)
    if (clamped !== effort) {
      onEffortChange(clamped)
    }
  }, [effort, effortChoices, onEffortChange, showEffort])

  const sessionCost = useMemo(() => sumSessionCost(messages), [messages])

  const modelTitle =
    (activeModel
      ? `This chat runs on ${activeModel}`
      : `This chat runs on the default model${
          modelInfo.defaultModel ? ` (${modelInfo.defaultModel})` : ''
        }`) +
    (modelInfo.source === 'fallback' ? ' — model list unavailable (showing known models)' : '')

  const current = clampEffortChoice(effort, effortChoices)

  return (
    <div className="lx-me-cluster">
      {sessionCost > 0 && (
        <span className="lx-me-cost chat-session-cost" title={`Session cost: ${fUsd(sessionCost)}`}>
          {fUsd(sessionCost)}
        </span>
      )}
      <ModelPicker
        className="lx-me-model"
        label="Chat model"
        size="sm"
        title={modelTitle}
        models={modelInfo.models}
        value={activeModel}
        defaultLabel="Default"
        defaultModelId={modelInfo.defaultModel}
        onChange={onModelChange}
        trailing={showEffort ? effortChoiceLabel(current) : null}
        panes={
          showEffort
            ? [
                {
                  key: 'effort',
                  label: 'Effort',
                  value: effortChoiceLabel(current),
                  content: (close) => (
                    <EffortChoices
                      value={current}
                      choices={effortChoices}
                      onChange={(lvl) => {
                        onEffortChange(lvl)
                        close()
                      }}
                    />
                  ),
                },
              ]
            : undefined
        }
      />
    </div>
  )
}

/** The effort tiers, as their OWN pane inside the model picker (ModelPicker `panes`).
 *
 * Was a peer <select> beside the model picker. Two controls put two different chevrons (a drawn
 * ▾ and the browser's native arrow) and two different menus (a styled popover and the OS list)
 * side by side in one 28px strip, and left the model name too little room to render without an
 * ellipsis. It is also the wrong shape for the data: the tiers a model accepts come FROM the
 * model (effortChoicesFor), so effort belongs under the model's own control — but on its own
 * pane, not stacked under a ~300-row list where five tiers would be buried.
 *
 * Reuses the list's own classes (.mp-list / .mp-opt / -name / -tick) so a tier row and a model
 * row are the same row.
 */
export function EffortChoices({
  value,
  choices,
  onChange,
}: {
  value: EffortChoice
  choices: EffortChoice[]
  onChange: (next: EffortChoice) => void
}) {
  return (
    <div className="mp-list" role="radiogroup" aria-label="Reasoning effort">
      {choices.map((lvl) => (
        <button
          key={lvl}
          type="button"
          role="radio"
          aria-checked={lvl === value}
          className={'mp-opt' + (lvl === value ? ' is-on' : '')}
          // Don't take focus off whatever had it (the composer behind the panel) to flip a tier.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onChange(lvl)}
        >
          <span className="mp-opt-name">{effortChoiceLabel(lvl)}</span>
          <span className="mp-opt-tick" aria-hidden="true">
            <Check size={14} />
          </span>
        </button>
      ))}
    </div>
  )
}
