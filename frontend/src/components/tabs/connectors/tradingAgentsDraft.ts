// The TradingAgents setup form's editable state: what GET /config becomes in the inputs, the
// local range checks (mirrored from services/trading_agents/settings.py, which still decides),
// and the PATCH that carries only what changed. Numbers are strings while typed, so a blank
// or half-typed field is never silently coerced.
import { TA_LIMITS } from '../../../types/tradingAgentsConstants'
import type {
  TaAnalyst,
  TaConfig,
  TaConfigPatch,
  TaContextLevel,
} from '../../../types/tradingAgents'

export interface TaDraft {
  deepModel: string
  quickModel: string
  analysts: TaAnalyst[]
  debateRounds: string
  riskRounds: string
  toolRounds: string
  maxCalls: string
  concurrency: string
  perRunCap: string
  monthlyCap: string
  language: string
  secUserAgent: string
  contextLevel: TaContextLevel
  alphaVantageKey: string
  fredKey: string
  clearAlphaVantage: boolean
  clearFred: boolean
}

export type TaDraftErrors = Partial<Record<keyof TaConfig, string>>

export function draftFromConfig(cfg: TaConfig): TaDraft {
  return {
    deepModel: cfg.deep_model ?? '',
    quickModel: cfg.quick_model ?? '',
    analysts: cfg.analysts,
    debateRounds: String(cfg.max_debate_rounds),
    riskRounds: String(cfg.max_risk_rounds),
    toolRounds: String(cfg.max_tool_rounds),
    maxCalls: String(cfg.max_llm_calls),
    concurrency: cfg.bridge_concurrency === null ? '' : String(cfg.bridge_concurrency),
    perRunCap: String(cfg.per_run_cap_usd),
    monthlyCap: String(cfg.monthly_cap_usd),
    language: cfg.output_language,
    secUserAgent: cfg.sec_edgar_user_agent,
    contextLevel: cfg.context_level,
    alphaVantageKey: '',
    fredKey: '',
    clearAlphaVantage: false,
    clearFred: false,
  }
}

function range(raw: string, min: number, max: number, whole: boolean): string | undefined {
  const text = raw.trim()
  const n = Number(text)
  if (text === '' || !Number.isFinite(n) || (whole && !Number.isInteger(n)) || n < min || n > max) {
    return `${whole ? 'A whole number' : 'A number'} from ${min} to ${max}.`
  }
  return undefined
}

export function validateDraft(draft: TaDraft): TaDraftErrors {
  const L = TA_LIMITS
  const errors: TaDraftErrors = {
    max_debate_rounds: range(
      draft.debateRounds,
      L.max_debate_rounds.min,
      L.max_debate_rounds.max,
      true,
    ),
    max_risk_rounds: range(draft.riskRounds, L.max_risk_rounds.min, L.max_risk_rounds.max, true),
    max_tool_rounds: range(draft.toolRounds, L.max_tool_rounds.min, L.max_tool_rounds.max, true),
    max_llm_calls: range(draft.maxCalls, L.max_llm_calls.min, L.max_llm_calls.max, true),
    bridge_concurrency:
      draft.concurrency.trim() === ''
        ? undefined
        : range(draft.concurrency, L.bridge_concurrency.min, L.bridge_concurrency.max, true),
    per_run_cap_usd: range(draft.perRunCap, L.per_run_cap_usd.min, L.per_run_cap_usd.max, false),
    monthly_cap_usd: range(draft.monthlyCap, L.monthly_cap_usd.min, L.monthly_cap_usd.max, false),
    output_language:
      draft.language.length > L.output_language.max
        ? `At most ${L.output_language.max} characters.`
        : undefined,
    sec_edgar_user_agent:
      draft.secUserAgent.length > L.sec_edgar_user_agent.max
        ? `At most ${L.sec_edgar_user_agent.max} characters.`
        : undefined,
    analysts: draft.analysts.length === 0 ? 'Pick at least one analyst.' : undefined,
  }
  for (const key of Object.keys(errors) as (keyof TaConfig)[]) {
    if (errors[key] === undefined) delete errors[key]
  }
  return errors
}

/** A secret field's PATCH value: `null` clears, a typed key replaces, blank keeps (omitted). */
function secret(typed: string, clear: boolean): string | null | undefined {
  if (clear) return null
  return typed.trim() ? typed.trim() : undefined
}

/** Only the settings that differ from `cfg`. A blank model or concurrency resets to its default. */
export function patchFromDraft(draft: TaDraft, cfg: TaConfig): TaConfigPatch {
  const patch: TaConfigPatch = {}
  const set = <K extends keyof TaConfigPatch>(key: K, next: TaConfigPatch[K], was: unknown) => {
    if (JSON.stringify(next) !== JSON.stringify(was)) patch[key] = next
  }
  set('deep_model', draft.deepModel || null, cfg.deep_model)
  set('quick_model', draft.quickModel || null, cfg.quick_model)
  set('analysts', draft.analysts, cfg.analysts)
  set('max_debate_rounds', Number(draft.debateRounds), cfg.max_debate_rounds)
  set('max_risk_rounds', Number(draft.riskRounds), cfg.max_risk_rounds)
  set('max_tool_rounds', Number(draft.toolRounds), cfg.max_tool_rounds)
  set('max_llm_calls', Number(draft.maxCalls), cfg.max_llm_calls)
  set(
    'bridge_concurrency',
    draft.concurrency.trim() ? Number(draft.concurrency) : null,
    cfg.bridge_concurrency,
  )
  set('per_run_cap_usd', Number(draft.perRunCap), cfg.per_run_cap_usd)
  set('monthly_cap_usd', Number(draft.monthlyCap), cfg.monthly_cap_usd)
  set('output_language', draft.language, cfg.output_language)
  set('sec_edgar_user_agent', draft.secUserAgent, cfg.sec_edgar_user_agent)
  set('context_level', draft.contextLevel, cfg.context_level)
  const av = secret(draft.alphaVantageKey, draft.clearAlphaVantage)
  if (av !== undefined) patch.alpha_vantage_api_key = av
  const fred = secret(draft.fredKey, draft.clearFred)
  if (fred !== undefined) patch.fred_api_key = fred
  return patch
}
