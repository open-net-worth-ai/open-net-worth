import { useCallback, useState } from 'react'
import { saveTaConfig, TaError } from '../../../hooks/useTradingAgents'
import type { TaConfig } from '../../../types/tradingAgents'
import { useSecretReveal } from '../../ui'
import { unrevealed, useConnectorSave } from './formSave'
import { revealConnectorSecret } from './secrets'
import { draftFromConfig, patchFromDraft, validateDraft } from './tradingAgentsDraft'
import type { TaDraft, TaDraftErrors } from './tradingAgentsDraft'

export type TaPatch = <K extends keyof TaDraft>(key: K, value: TaDraft[K]) => void

/** The setup form's editable state: the draft follows the server only while nothing is being
 *  edited (`synced` is the baseline it was last seeded from), so an untouched form takes a newer
 *  baseline and an edited form keeps its edits. */
function useDraftState(cfg: TaConfig | undefined) {
  const [draft, setDraft] = useState<TaDraft | null>(null)
  const [synced, setSynced] = useState<string | null>(null)
  const baseline = cfg ? draftFromConfig(cfg) : null
  const baselineJson = JSON.stringify(baseline)
  const dirty = draft !== null && JSON.stringify(draft) !== synced
  if (baseline && baselineJson !== synced && !dirty) {
    setSynced(baselineJson)
    setDraft(baseline)
  }
  const seed = (saved: TaConfig) => {
    setDraft(draftFromConfig(saved))
    setSynced(JSON.stringify(draftFromConfig(saved)))
  }
  return { draft, setDraft, baseline, seed }
}

/** The draft, the reveal state of the two optional keys, and the footer Save that PATCHes only
 *  what changed. */
export function useTaDraft(cfg: TaConfig | undefined, refresh: () => Promise<unknown>) {
  const { draft, setDraft, baseline, seed } = useDraftState(cfg)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<TaDraftErrors>({})

  const patch = useCallback<TaPatch>(
    (key, value) => {
      setDraft((d) => (d ? { ...d, [key]: value } : d))
      setSaveErr(null)
    },
    [setDraft],
  )

  const avReveal = useSecretReveal({
    saved: !!cfg?.has_alpha_vantage_api_key,
    value: draft?.alphaVantageKey ?? '',
    onChange: (v) =>
      setDraft((d) => (d ? { ...d, alphaVantageKey: v, clearAlphaVantage: false } : d)),
    reveal: useCallback(() => revealConnectorSecret('trading_agents', 'alpha_vantage_api_key'), []),
  })
  const fredReveal = useSecretReveal({
    saved: !!cfg?.has_fred_api_key,
    value: draft?.fredKey ?? '',
    onChange: (v) => setDraft((d) => (d ? { ...d, fredKey: v, clearFred: false } : d)),
    reveal: useCallback(() => revealConnectorSecret('trading_agents', 'fred_api_key'), []),
  })

  async function save(): Promise<boolean> {
    if (!draft || !cfg) return false
    const errors = validateDraft(draft)
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) {
      setSaveErr('Fix the highlighted fields.')
      return false
    }
    setSaving(true)
    setSaveErr(null)
    try {
      seed(await saveTaConfig(patchFromDraft(draft, cfg)))
      await refresh()
      return true
    } catch (e) {
      if (e instanceof TaError && e.field) {
        setFieldErrors({ [e.field]: e.message })
        setSaveErr('Fix the highlighted fields.')
      } else {
        setSaveErr(e instanceof TaError ? e.message : 'Could not save, could not reach the server.')
      }
      return false
    } finally {
      setSaving(false)
    }
  }

  useConnectorSave({
    ready: !!cfg && !!draft,
    values: draft && {
      ...draft,
      alphaVantageKey: unrevealed(draft.alphaVantageKey, avReveal),
      fredKey: unrevealed(draft.fredKey, fredReveal),
    },
    baseline,
    saving,
    error: saveErr,
    save,
  })

  return { draft, setDraft, patch, saving, saveErr, fieldErrors, avReveal, fredReveal }
}
