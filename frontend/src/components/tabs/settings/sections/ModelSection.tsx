// Settings → AI Model. Ported from
// design_handoffs/settings_handoff/prototype/redesign/settings-model.jsx.
//
// Provider credentials and model routing. Usage telemetry lives in Settings → AI usage;
// chat and memory preferences live in Settings → Chat & Memory.
//
// The one deliberate departure from the prototype source: vision capability. The handoff's
// `TEXT_ONLY` array is explicitly a stand-in ("capability must come from the provider; an
// unknown model ID is UNKNOWN, not incapable"). The app already has exactly that contract —
// `ModelOption.vision` is backend-derived and `modelRejectsImages` only reports true when the
// provider positively said no — so this uses it instead of a hardcoded list.
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { authFetch } from '../../../../api/client'
import type { ModelOption } from '../../../../api/models'
import {
  MODEL_CONFIG_CHANGED_EVENT,
  modelRejectsImages,
  useModels,
} from '../../../../hooks/useModels'
import { getCachedQueryData, useCachedFetchState } from '../../../../state/queryCache'
import { openReportProblem } from '../../../../state/reportProblem'
import { useCliDetectionPoll } from '../../../../hooks/useCliDetectionPoll'
import { CliSubscriptionHelp } from '../../../shared/CliSubscriptionHelp'
import type { CliHostPlatform } from '../../../shared/CliSubscriptionHelp'
import { NO_LOCAL_MODELS_NOTE, isLocalOnlyProvider } from '../../../shared/privateCloudCopy'
import { ModelPicker } from '../../../ui'
import { ChevronRight, Eye, EyeOff, TriangleAlert, Plus } from '../../../../ui/icons'
import { Badge, Btn, Empty, Field, Modal, Panel, Select, Toggle, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
import { TurnTimeLimitPanel } from './TurnTimeLimitPanel'

// ── shapes ───────────────────────────────────────────────────────────────────
interface CatalogEntry {
  key: string
  label: string
  transport: string
  base_url: string
  needs_api_key: boolean
  editable_base_url: boolean
  /** Presentation only — the fixed host a pinned provider talks to, when base_url is "". */
  display_base_url?: string
  model_hints: string[]
  cli_binary?: string
}

interface ConfiguredProvider {
  has_api_key: boolean
  base_url: string
  model: string
  enabled?: boolean
  key_status?: 'valid' | 'invalid' | 'unreachable' | 'unchecked'
  checked_at?: string | null
  label?: string
}

// A local CLI enrols by DETECTION, not by stored credentials — it has no api_key or base_url
// to save, so `configured_providers` has no row for it. The Settings list still shows it (it is
// a usable provider, and may be the instance default), which means the row renderer must be
// handed something. This is what "configured" means for a transport that configures itself.
const DETECTED_CLI_CFG: ConfiguredProvider = {
  has_api_key: false,
  base_url: '',
  model: '',
  enabled: true,
}

interface ProvidersResponse {
  catalog: CatalogEntry[]
  configured_providers: Record<string, ConfiguredProvider>
  detected_clis: Record<string, boolean>
  detected_cli_paths: Record<string, string>
  /** What each CLI runs when told nothing — read from the CLI's own config. */
  cli_default_models?: Record<string, string>
  cli_host_platform?: CliHostPlatform
}

const CLI_DEFAULT_NOTE: Record<string, string> = {
  claude_cli: 'Claude Code default',
  codex_cli: 'Default in Codex',
}

/** Tags the row a CLI would run on its own, so picking it is a recognised choice. */
export function cliDefaultNote(
  m: ModelOption,
  cliDefaults: Record<string, string> | undefined,
): string | undefined {
  const key = m.provider_key
  return key && CLI_DEFAULT_NOTE[key] && cliDefaults?.[key] === m.id
    ? CLI_DEFAULT_NOTE[key]
    : undefined
}

interface ModelConfigPublic {
  /** The stored instance default: "<provider_key>:<model_id>". The three below are a
   *  DERIVED view of it, kept for the rows that still show an endpoint/transport. */
  default_model: string
  provider: string
  model: string
  base_url: string
  task_models?: Record<string, string | null>
}

interface PreferredModelsConfig {
  models: string[]
}

// Must stay in step with the backend's routable task list (VALID_TASKS). "Chat" is not a
// task — chat runs on the default provider — and dedup judgments live in Duplicate Detection.
const TASK_GROUPS: [string, [string, string, string, boolean?][]][] = [
  [
    'Assistant',
    [
      ['memory', 'Memory', 'Extracts and consolidates facts after chat turns'],
      ['suggestions', 'Chat suggestions', 'Generates follow-up suggestions in the chat panel'],
      [
        'title',
        'Conversation titles',
        'Names each new conversation from its first message — a tiny call, so a fast model is best',
      ],
      [
        'chat_vision',
        'Image reading',
        'Reads images you paste into chat — needs a vision model',
        true,
      ],
    ],
  ],
  [
    'Insights',
    [
      ['intel', 'Intel & watchlist', 'Scans market signals and watchlist alerts'],
      ['briefing', 'Daily briefing', 'Composes the morning wealth briefing'],
      [
        'news_screen',
        'News screening',
        'Filters incoming headlines down to what touches your holdings',
      ],
      [
        'analysis',
        'Alert analysis',
        'Writes the why/impact/action behind each pushed Intel alert — the highest-stakes step, so reliability matters more than cost here',
      ],
      [
        'price_monitor',
        'Price alerts',
        'Writes up a price alert whose trigger was already decided by a threshold check',
      ],
    ],
  ],
  [
    'Statement import',
    [
      // ONE row, not five (owner, 2026-08-09: "merge all of these into one settings only").
      // It listed CSV columns / Row extraction / Scanned pages / Statement details / Your
      // instructions separately, on the theory that cheap schema inference and strong vision
      // deserved independent routing. In practice all five were always set to the same model,
      // so the split bought nothing but five chances to leave one behind.
      //
      // Deliberately NOT marked vision-only: a scanned page falls back to the instance
      // default image model when this pin can't see, so pinning a text model here is a
      // legitimate choice rather than a broken one.
      [
        'statement_import',
        'Statement import',
        'Reads statements end to end — columns, rows, details, and your written instructions. Scanned pages fall back to your default image model.',
      ],
    ],
  ],
]
const TASK_ROWS = TASK_GROUPS.flatMap(([, rows]) => rows)
const VISION_TASKS = TASK_ROWS.filter((r) => r[3])

// Two states, plainly named: GREEN = you can use it, RED = you cannot. "Unreachable" was
// amber, which read as a caution when it is just as broken as a rejected key — neither one
// can answer a turn. Wording avoids our internal vocabulary ("valid", "detected", "configured"
// say nothing to someone who just wants to know if it works).
const STATUS_BADGE: Record<string, [string, 'good' | 'bad']> = {
  valid: ['Ready', 'good'],
  // Saved but never validated. Still shown as Ready: it is set up and there is nothing for
  // the owner to act on — a failure surfaces the moment a turn actually uses it.
  unchecked: ['Ready', 'good'],
  invalid: ['Key rejected', 'bad'],
  unreachable: ["Can't connect", 'bad'],
}

async function getJson<T>(path: string): Promise<T> {
  const r = await authFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

// ── provider row ─────────────────────────────────────────────────────────────
function ProviderRow({
  entry,
  cfg,
  isDefault,
  detected,
  detectedPath,
  onEdit,
  onToggle,
  toast,
  subscription,
}: {
  entry: CatalogEntry
  cfg: ConfiguredProvider
  isDefault: boolean
  detected: boolean
  detectedPath: string
  onEdit: (k: string) => void
  onToggle: (k: string, v: boolean) => void
  toast: SectionProps['toast']
  subscription?: {
    platform?: CliHostPlatform
    onCheck: () => Promise<unknown>
    terminal?: { onOpen: () => void }
  }
}) {
  const isCli = Boolean(entry.cli_binary)
  const enabled = cfg.enabled !== false

  const badge = () => {
    // No "Disabled" chip: the toggle to its right IS that state, and showing both says the same
    // thing twice while crowding the row. A disabled provider shows no badge at all — the switch
    // carries it. Every other badge reports something the switch cannot: whether a key
    // validated, whether a local CLI is installed.
    //
    // No "Default" chip either. It dates from when the row OWNED the default, and it REPLACED
    // the row's real status — the default CLI read "Default" instead of "Detected", hiding
    // whether its binary was even installed. The Default model row below names its provider,
    // so the chip was saying it twice and costing the useful fact.
    if (!enabled) return null
    // Binary presence does not prove the CLI's saved login can answer a turn.
    if (isCli)
      return (
        <Badge tone={detected ? 'good' : 'bad'}>{detected ? 'Installed' : 'Not installed'}</Badge>
      )
    const [txt, tone] = STATUS_BADGE[cfg.key_status || 'unchecked'] || STATUS_BADGE.unchecked!
    return <Badge tone={tone}>{txt}</Badge>
  }

  return (
    <div className="rd-prov">
      <div className="rd-prov-head">
        <div className="rd-prov-main">
          <div className="rd-prov-name">{cfg.label || entry.label}</div>
          <div className="rd-prov-meta">
            {/* display_base_url covers the pinned-transport case: Anthropic stores base_url:""
                because its transport owns the host, so the row said nothing about where it
                talks to while every other provider showed its endpoint. */}
            {isCli ? (
              /* The resolved binary path, shown exactly where a URL provider shows its
                 endpoint — both answer "where does this actually run from". Falls back to
                 the binary name when it is not on this machine (no path to show). */
              <span className="rd-mono">{detectedPath || entry.cli_binary || 'Local CLI'}</span>
            ) : (
              <span className="rd-mono">
                {cfg.base_url || entry.base_url || entry.display_base_url}
              </span>
            )}
            {/* An explicit separator: the two facts were divided by flex gap alone, which reads
                as one run-on string. Suppressed when there is no endpoint to separate from. */}
            {cfg.model && (isCli || cfg.base_url || entry.base_url || entry.display_base_url) ? (
              <span aria-hidden="true">•</span>
            ) : null}
            {cfg.model ? <span>{cfg.model}</span> : null}
          </div>
          {subscription && (
            <div className="rd-help">
              Use your {entry.key === 'claude_cli' ? 'Claude' : 'ChatGPT'} subscription; no API key
              needed.
            </div>
          )}
        </div>
        {badge()}
        {(!subscription || detected) && (
          <>
            <Toggle
              on={enabled}
              disabled={enabled && isDefault}
              label={(enabled ? 'Disable ' : 'Enable ') + entry.label}
              onChange={(v) => {
                if (enabled && isDefault) {
                  toast('Choose a default model on another provider first')
                  return
                }
                onToggle(entry.key, v)
              }}
            />
            <Btn size="xs" variant="ghost" onClick={() => onEdit(entry.key)}>
              Edit
            </Btn>
          </>
        )}
      </div>
      {subscription && (entry.key === 'claude_cli' || entry.key === 'codex_cli') && (
        <CliSubscriptionHelp
          provider={entry.key}
          platform={subscription.platform}
          detected={detected}
          onCheck={subscription.onCheck}
          terminal={subscription.terminal}
        />
      )}
    </div>
  )
}

// ── one editor for both paths: adding a provider, and editing a configured one ─
function ProviderModal({
  mode,
  editKey,
  cfg,
  catalog,
  models,
  cliPlatform,
  detectedClis,
  onCheckCli,
  terminal,
  taken,
  isDefault,
  onClose,
  onSave,
  onRemove,
  toast,
}: {
  mode: 'add' | 'edit'
  editKey?: string
  cfg?: ConfiguredProvider
  catalog: CatalogEntry[]
  /** Every configured provider's live models — the field below searches this provider's slice. */
  models: ModelOption[]
  cliPlatform?: CliHostPlatform
  detectedClis: Record<string, boolean>
  onCheckCli: () => Promise<unknown>
  terminal?: { onOpen: () => void }
  taken: string[]
  isDefault: boolean
  onClose: () => void
  onSave: (
    key: string,
    patch: { label: string; url: string; model: string; key: string; bin: string },
  ) => void
  onRemove: (key: string) => void
  toast: SectionProps['toast']
}) {
  const available = catalog.filter((e) => e.key === 'custom' || !taken.includes(e.key))
  const [sel, setSel] = useState(
    mode === 'edit' ? editKey || 'custom' : available[0]?.key || 'custom',
  )
  const entry = catalog.find((e) => e.key === sel) || catalog[0]!
  const [f, setF] = useState(() => ({
    label: cfg?.label || '',
    url: cfg?.base_url || entry.base_url,
    model: cfg?.model || '',
    key: '',
    bin: cfg?.base_url || '',
  }))
  // This provider's own live list (about 470 rows for OpenRouter) when we have it; the catalog's
  // short hint list otherwise — adding a provider, or one that cannot be listed yet.
  const providerModels = useMemo<ModelOption[]>(() => {
    const live = models.filter((m) => m.provider_key === entry.key)
    if (live.length) return live
    return entry.model_hints.map((id) => ({
      id,
      provider_key: entry.key,
      provider_label: entry.label,
    }))
  }, [models, entry])
  const [armed, setArmed] = useState(false)
  const [testing, setTesting] = useState(false)
  // The toast is gone in two seconds; a failed connection test stays up, beside a way to report it.
  const [testFailure, setTestFailure] = useState<string | null>(null)

  const isCli = Boolean(entry.cli_binary)
  const urlMissing = entry.editable_base_url && !isCli && !f.url.trim()
  const set = (k: keyof typeof f, v: string) => setF((p) => ({ ...p, [k]: v }))

  // Reveal is a per-click round trip to the human-only endpoint. The key is never held in any
  // list response, so nothing else in the app (caches, screen context sent to Lucius) can see it.
  const [showKey, setShowKey] = useState(false)
  const [revealing, setRevealing] = useState(false)
  async function revealKey() {
    if (revealing) return
    setRevealing(true)
    try {
      const res = await authFetch(
        `/api/model-config/providers/${encodeURIComponent(entry.key)}/reveal-key`,
      )
      if (!res.ok) {
        toast(
          res.status === 404 ? 'No stored key to show' : `Could not read the key (${res.status})`,
        )
        return
      }
      const data = (await res.json()) as { api_key?: string }
      if (!data.api_key) {
        toast('No stored key to show')
        return
      }
      set('key', data.api_key)
      setShowKey(true)
    } catch {
      toast('Could not reach the server. Try again.')
    } finally {
      setRevealing(false)
    }
  }

  function pick(k: string) {
    const e = catalog.find((x) => x.key === k) || catalog[0]!
    setSel(k)
    setF({ label: '', url: e.base_url, model: '', key: '', bin: '' })
  }

  async function testConnection() {
    setTesting(true)
    setTestFailure(null)
    const started = performance.now()
    try {
      const r = await authFetch(`/api/model-config/providers/${encodeURIComponent(sel)}/validate`, {
        method: 'POST',
      })
      const body = (await r.json().catch(() => null)) as {
        key_status?: string
        error?: string
        detail?: string
      } | null
      const ms = Math.round(performance.now() - started)
      if (body?.key_status === 'valid') toast(`Tested ok — ${ms}ms`)
      // `detail` is what a 4xx carries (e.g. the provider is switched off). Without it this
      // asserted "unreachable" for ANY failure — including our own server erroring — and so
      // blamed the network for problems that had nothing to do with it.
      else {
        const message = body?.error || body?.detail || `Test failed (HTTP ${r.status})`
        setTestFailure(message)
        toast(message)
      }
    } catch {
      setTestFailure('Could not reach the server. Try again.')
      toast('Could not reach the server. Try again.')
    } finally {
      setTesting(false)
    }
  }

  return (
    <Modal
      title={mode === 'edit' ? 'Edit provider' : 'Add AI provider'}
      onClose={onClose}
      footer={
        <>
          {mode === 'edit' && !isDefault ? (
            <Btn
              variant={armed ? 'danger-solid' : 'danger'}
              style={{ marginRight: 'auto' }}
              onClick={() => {
                if (!armed) {
                  setArmed(true)
                  setTimeout(() => setArmed(false), 4000)
                } else {
                  onRemove(sel)
                  onClose()
                }
              }}
            >
              {armed ? 'Confirm remove' : 'Remove'}
            </Btn>
          ) : null}
          <Btn onClick={onClose}>Cancel</Btn>
          <Btn
            variant="primary"
            disabled={urlMissing}
            onClick={() => {
              onSave(sel, f)
              onClose()
            }}
          >
            {mode === 'edit' ? 'Save changes' : 'Add provider'}
          </Btn>
        </>
      }
    >
      <div className="rd-formgrid">
        {mode === 'add' && (
          <Field
            label="Provider"
            span="full"
            help={
              entry.key === 'custom'
                ? 'Any endpoint that speaks the OpenAI API — a self-hosted gateway, a proxy, anything'
                : undefined
            }
          >
            <Select value={sel} onChange={(e) => pick(e.target.value)}>
              {available.map((e) => (
                <option key={e.key} value={e.key}>
                  {e.label}
                </option>
              ))}
            </Select>
          </Field>
        )}
        {entry.key === 'custom' && (
          <Field label="Display name" span="full">
            <input
              className="rd-input"
              value={f.label}
              placeholder="e.g. Work gateway"
              onChange={(e) => set('label', e.target.value)}
            />
          </Field>
        )}
        {entry.editable_base_url && !isCli && (
          <Field label="Base URL" span="full" help={urlMissing ? 'Required' : undefined}>
            <input
              className="rd-input"
              value={f.url}
              placeholder="https://…/v1"
              onChange={(e) => set('url', e.target.value)}
            />
          </Field>
        )}
        {/* A pinned provider still SHOWS where it talks to — it just is not yours to change.
            Anthropic stores base_url:"" (its transport knows its own host), so without the
            display fallback this rendered an empty read-only box that looked like a missing
            setting rather than a fixed one. */}
        {!entry.editable_base_url && !isCli && (entry.base_url || entry.display_base_url) && (
          <Field label="Endpoint" span="full" help="Fixed by this provider">
            <input
              className="rd-input rd-mono"
              value={entry.base_url || entry.display_base_url}
              readOnly
            />
          </Field>
        )}
        {entry.needs_api_key && (
          <Field
            label="API key"
            span="full"
            help={
              cfg?.has_api_key
                ? 'A key is saved — leave blank to keep it'
                : 'Stored server-side, never shown again'
            }
          >
            <div className="rd-keyfield">
              <input
                className="rd-input"
                type={showKey ? 'text' : 'password'}
                autoComplete="off"
                value={f.key}
                placeholder={cfg?.has_api_key ? '•••••••• (saved)' : 'Paste your API key'}
                onChange={(e) => set('key', e.target.value)}
              />
              {/* Reveal fetches on demand from the human-only reveal-key endpoint — the key is
                  NOT part of any list payload, so it never sits in a cache or in screen context.
                  Only offered when a key is actually stored and the box is still empty (an
                  unsaved draft is already plain text). */}
              {cfg?.has_api_key && !f.key ? (
                <button
                  type="button"
                  className="rd-keyeye"
                  onClick={() => void revealKey()}
                  disabled={revealing}
                  aria-label="Show the saved API key"
                >
                  <Eye size={17} strokeWidth={1.8} aria-hidden="true" />
                </button>
              ) : null}
              {showKey ? (
                <button
                  type="button"
                  className="rd-keyeye"
                  onClick={() => {
                    setShowKey(false)
                    set('key', '')
                  }}
                  aria-label="Hide the API key"
                >
                  <EyeOff size={17} strokeWidth={1.8} aria-hidden="true" />
                </button>
              ) : null}
            </div>
          </Field>
        )}
        {isCli ? (
          <Field
            label="Binary path"
            span="full"
            help={'Leave blank to use the ' + entry.cli_binary + ' found on PATH'}
          >
            <input
              className="rd-input"
              value={f.bin}
              placeholder={entry.cli_binary}
              onChange={(e) => set('bin', e.target.value)}
            />
          </Field>
        ) : (
          <Field label="Default model" span="full" help="Search the list, or type any model ID">
            <ModelPicker
              label={`Default model for ${entry.label}`}
              models={providerModels}
              value={f.model ? `${entry.key}:${f.model}` : ''}
              placeholder="Choose a model…"
              customProviderKey={entry.key}
              onChange={(ref) =>
                set(
                  'model',
                  ref.startsWith(`${entry.key}:`) ? ref.slice(entry.key.length + 1) : ref,
                )
              }
            />
          </Field>
        )}
        {(entry.key === 'claude_cli' || entry.key === 'codex_cli') && (
          <div style={{ gridColumn: '1 / -1' }}>
            <CliSubscriptionHelp
              provider={entry.key}
              platform={cliPlatform}
              detected={Boolean(detectedClis[entry.key])}
              onCheck={onCheckCli}
              terminal={terminal}
            />
          </div>
        )}
      </div>
      {mode === 'edit' ? (
        <Btn size="xs" disabled={testing} onClick={() => void testConnection()}>
          {testing ? 'Testing…' : 'Test connection'}
        </Btn>
      ) : null}
      {testFailure ? (
        <Field label="The connection test failed" span="full" help={testFailure}>
          <Btn size="xs" onClick={() => openReportProblem({ error: testFailure })}>
            Report this problem
          </Btn>
        </Field>
      ) : null}
    </Modal>
  )
}

// ── task models + the vision contract ────────────────────────────────────────
function TaskModels({
  toast,
  defaultModel,
  models,
  taskModels,
  onSave,
}: {
  toast: SectionProps['toast']
  defaultModel: string
  models: ModelOption[]
  taskModels: Record<string, string | null>
  onSave: (next: Record<string, string | null>) => void
}) {
  const over = taskModels
  const keys = Object.keys(over).filter((k) => over[k])
  const label = (k: string) => (TASK_ROWS.find(([kk]) => kk === k) || [, k])[1] as string
  const resolved = (k: string) => over[k] || defaultModel
  // Only a positively-declared text-only model warns. An unknown model id is UNKNOWN, not
  // incapable — the model field takes free text, so warning on anything unrecognised would
  // cry wolf on every custom gateway.
  const blind = VISION_TASKS.filter(([k]) =>
    modelRejectsImages(resolved(k) || '', models, defaultModel),
  )

  const set = (k: string, v: string) => {
    const next = { ...over }
    if (v) next[k] = v
    else delete next[k]
    onSave(next)
  }

  return (
    <Panel
      title="Task models"
      sub="Background chores can run on cheaper models — chat itself always uses your default provider"
    >
      {blind.length ? (
        <div className="rd-banner">
          <span style={{ color: 'var(--warn)', marginTop: 1 }}>
            <TriangleAlert size={14} strokeWidth={1.7} aria-hidden="true" />
          </span>
          <div style={{ flex: 1 }}>
            <div className="rd-banner-title">
              {blind.length === 1
                ? blind[0]![1] + ' can’t read images'
                : blind.length + ' image tasks can’t read images'}
            </div>
            <div className="rd-banner-sub">
              {blind.map(([k, l]) => l + ' → ' + resolved(k)).join(' • ')} — text-only. These runs
              will fail until you route them to a vision model.
            </div>
          </div>
        </div>
      ) : null}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>
            {keys.length
              ? keys.length + ' of ' + TASK_ROWS.length + ' tasks overridden'
              : 'All tasks use your default model'}
          </div>
          <div className="rd-help" style={{ marginTop: 3, display: 'block', lineHeight: 1.5 }}>
            {keys.length
              ? keys.map(label).join(' • ')
              : TASK_ROWS.length + ' routable tasks — override only the ones you care about.'}
          </div>
        </div>
        {keys.length ? (
          <Btn
            size="xs"
            variant="ghost"
            onClick={() => {
              onSave({})
              toast('Task overrides cleared')
            }}
          >
            Reset
          </Btn>
        ) : null}
      </div>
      {TASK_GROUPS.map(([group, rows]) => (
        // A keyed Fragment, NOT a display:contents div. Both keep the wrapper out of the
        // layout, but a real element still sits in the DOM tree — so the panel's
        // `.rd-panel-body > .rd-subhead` rule (its padding and background band) stopped
        // matching, and the group headings rendered flush against the card edge.
        <Fragment key={group}>
          <div className="rd-subhead">{group}</div>
          <div className="rd-formgrid">
            {rows.map(([k, l, help, vision]) => {
              const bad =
                Boolean(vision) && modelRejectsImages(resolved(k) || '', models, defaultModel)
              return (
                <Field key={k} label={l} help={help}>
                  <ModelPicker
                    label={l}
                    models={models}
                    value={over[k] || ''}
                    defaultLabel={
                      'Default' +
                      (vision && modelRejectsImages(defaultModel, models, defaultModel)
                        ? ' (no vision)'
                        : '')
                    }
                    defaultModelId={defaultModel}
                    annotate={
                      vision ? (m) => (m.vision === false ? '— no vision' : undefined) : undefined
                    }
                    onChange={(next) => set(k, next)}
                  />
                  {bad ? (
                    <span
                      className="rd-help"
                      style={{
                        color: 'var(--warn)',
                        display: 'block',
                        marginTop: 5,
                        lineHeight: 1.5,
                      }}
                    >
                      {resolved(k)} is text-only — pick a vision model or this task will fail.
                    </span>
                  ) : null}
                </Field>
              )
            })}
          </div>
        </Fragment>
      ))}
    </Panel>
  )
}

// ── preferred models ─────────────────────────────────────────────────────────
// A draft + explicit Save, NOT eager per-click saving: the shortlist is edited a few
// models at a time, and PUTting on every tick would fire a toast per click.
export function PreferredModels({
  models,
  saved,
  onSave,
}: {
  models: ModelOption[]
  saved: string[]
  onSave: (next: string[]) => void
}) {
  const [sel, setSel] = useState<string[]>(saved)
  const dirty = sel.length !== saved.length || saved.some((r) => !sel.includes(r))

  // The saved list arrives async, so seeding useState once would leave the chips empty on a
  // cold load. Re-sync whenever the SAVED list changes identity (a fetch landing, or our own
  // save round-tripping) — never on every keystroke, so an in-progress edit is not clobbered.
  const savedKey = saved.join('\u0000')
  useEffect(() => {
    setSel(saved)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey])

  return (
    <>
      <span className="rd-help">
        Your picks become a “Preferred” group at the top of every model picker. Leave empty to show
        the full list.
      </span>
      <ModelPicker
        multiple
        label="Preferred models"
        models={models}
        value={sel}
        emptyHint={
          <span className="rd-help">
            Nothing picked — every available model shows in the picker.
          </span>
        }
        onChange={setSel}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Btn size="xs" variant="primary" disabled={!dirty} onClick={() => onSave(sel)}>
          Save preferred models
        </Btn>
        {dirty ? <span className="rd-help">Unsaved</span> : null}
      </div>
    </>
  )
}

// ── the section ──────────────────────────────────────────────────────────────
export function ModelSection({ toast, gatewayMode = false, openSection }: SectionProps) {
  const [subscriptionKeys, setSubscriptionKeys] = useState<string[]>([])
  const providers = useCachedFetchState<ProvidersResponse>(
    '/api/model-config/providers',
    async () => {
      const data = await getJson<ProvidersResponse>('/api/model-config/providers')
      const missing = data.catalog
        .filter(
          (entry) =>
            (entry.key === 'claude_cli' || entry.key === 'codex_cli') &&
            !data.configured_providers[entry.key] &&
            !data.detected_clis[entry.key],
        )
        .map((entry) => entry.key)
      // Keep the sign-in instructions mounted after the binary is detected.
      setSubscriptionKeys((previous) => {
        const added = missing.filter((key) => !previous.includes(key))
        return added.length ? [...previous, ...added] : previous
      })
      return data
    },
    { staleTime: 0 },
  )
  const modelConfig = useCachedFetchState<ModelConfigPublic>(
    '/api/model-config',
    () => getJson<ModelConfigPublic>('/api/model-config'),
    { staleTime: 0 },
  )
  const preferred = useCachedFetchState<PreferredModelsConfig>(
    '/api/preferred-models',
    () => getJson<PreferredModelsConfig>('/api/preferred-models'),
    { staleTime: 0 },
  )
  const { models } = useModels(true)
  useSectionBusy(
    (providers.initialLoading && !providers.data) ||
      (modelConfig.initialLoading && !modelConfig.data) ||
      (preferred.initialLoading && !preferred.data),
  )

  const [editor, setEditor] = useState<{ mode: 'add' | 'edit'; key?: string } | null>(null)

  const terminal =
    gatewayMode && openSection ? { onOpen: () => openSection('terminal') } : undefined
  const catalog = (providers.data?.catalog ?? []).filter(
    (entry) => !gatewayMode || !isLocalOnlyProvider(entry),
  )
  const configured = providers.data?.configured_providers ?? {}
  const detected = providers.data?.detected_clis ?? {}
  const detectedPaths = providers.data?.detected_cli_paths ?? {}
  const refreshProviders = providers.refresh
  const refreshCliDetection = useCallback(async () => {
    const previous = getCachedQueryData<ProvidersResponse>('/api/model-config/providers')
    const next = await refreshProviders()
    const keys = new Set([
      ...Object.keys(previous?.detected_clis ?? {}),
      ...Object.keys(previous?.detected_cli_paths ?? {}),
      ...Object.keys(next.detected_clis ?? {}),
      ...Object.keys(next.detected_cli_paths ?? {}),
    ])
    if (
      previous &&
      [...keys].some(
        (key) =>
          Boolean(previous.detected_clis?.[key]) !== Boolean(next.detected_clis?.[key]) ||
          (previous.detected_cli_paths?.[key] || '') !== (next.detected_cli_paths?.[key] || ''),
      )
    )
      window.dispatchEvent(new Event(MODEL_CONFIG_CHANGED_EVENT))
    return next
  }, [refreshProviders])
  const { check: checkCliInstallations, error: cliDetectionError } = useCliDetectionPoll(
    refreshCliDetection,
    Boolean(providers.data),
  )
  const subscriptions = catalog.filter(
    (entry) =>
      (entry.key === 'claude_cli' || entry.key === 'codex_cli') &&
      !configured[entry.key] &&
      (subscriptionKeys.includes(entry.key) || !detected[entry.key]),
  )

  // The instance default is stored as a TRANSPORT, so map it back to the catalog key whose
  // transport + base_url match — that is the row whose radio should read as selected.
  // The default is stored as "<provider_key>:<model_id>", so its provider is the prefix.
  // This used to re-derive the key by matching transport + base_url back against the
  // catalogue — a guess that could land on the wrong entry.
  const defaultModelRef = modelConfig.data?.default_model || ''
  const defaultKey = defaultModelRef.split(':')[0] || ''

  // only what's actually set up — the default first.
  // A local CLI enrols by DETECTION, not by stored credentials (it has no api_key or URL to
  // store), so it never appears in `configured`. Filtering on that alone showed "No providers
  // yet" on a machine where the Claude Code CLI was installed, selected as the default model,
  // and already supplying every model picker — see the endpoint's own note about the row
  // "having nothing true to say about itself".
  const rows = catalog
    .filter(
      (e) =>
        (configured[e.key] || detected[e.key]) &&
        !subscriptions.some((subscription) => subscription.key === e.key),
    )
    .sort((a, b) => (a.key === defaultKey ? -1 : b.key === defaultKey ? 1 : 0))
  // Split by the switch. Eleven rows in one run interleaved live providers with switched-off
  // ones, so "what is actually on?" took a scan of every toggle.
  const [showDisabled, setShowDisabled] = useState(false)
  // A detected-only CLI has no stored row, so `configured[key]` is undefined — it is enabled
  // by virtue of being installed. `?.enabled !== false` already reads that correctly.
  const enabledRows = rows.filter((e) => configured[e.key]?.enabled !== false)
  const disabledRows = rows.filter((e) => configured[e.key]?.enabled === false)
  const takenKeys = Object.keys(configured)
  const defaultModel = modelConfig.data?.model || ''

  function announceModelConfigChanged() {
    window.dispatchEvent(new Event(MODEL_CONFIG_CHANGED_EVENT))
  }

  async function putProvider(key: string, body: Record<string, unknown>, message: string) {
    try {
      const r = await authFetch(`/api/model-config/providers/${encodeURIComponent(key)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const b = (await r.json().catch(() => null)) as { detail?: string } | null
        toast(b?.detail || `Could not save (HTTP ${r.status})`)
        return
      }
      void providers.refresh()
      announceModelConfigChanged()
      toast(message)
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  // DELETE /providers/{key}, not the sibling /providers/{key}/key — that one drops only the
  // API key and keeps `enabled: false`, so the provider never left the list however many
  // times you pressed Remove. (Removing only the key is `/key`, which nothing here calls.)
  async function removeProvider(key: string) {
    try {
      const r = await authFetch(`/api/model-config/providers/${encodeURIComponent(key)}`, {
        method: 'DELETE',
      })
      if (!r.ok) {
        const b = (await r.json().catch(() => null)) as { detail?: string } | null
        toast(b?.detail || `Could not remove (HTTP ${r.status})`)
        return
      }
      void providers.refresh()
      announceModelConfigChanged()
      toast('Provider removed')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function putDefaultModel(ref: string) {
    try {
      const r = await authFetch('/api/model-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_model: ref }),
      })
      if (!r.ok) {
        const b = (await r.json().catch(() => null)) as { detail?: string } | null
        toast(b?.detail || `Could not set the default model (HTTP ${r.status})`)
        return
      }
      void modelConfig.refresh()
      announceModelConfigChanged()
      toast('Default model set')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function putPreferred(models: string[], message: string) {
    try {
      const r = await authFetch('/api/preferred-models', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      void preferred.refresh()
      // The shortlist drives the "Preferred" group in EVERY picker, and `preferred` is a
      // flag the backend stamps onto GET /api/models — which useModels caches at module
      // level. Without this the chat panel, the dock, the task-model rows and the
      // Automation editor all kept the old group until a full page reload.
      announceModelConfigChanged()
      toast(message)
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function putTaskModels(next: Record<string, string | null>) {
    // The endpoint takes the WHOLE map; an omitted task would silently keep its old pin.
    const full: Record<string, string | null> = {}
    for (const [k] of TASK_ROWS) full[k] = next[k] || null
    try {
      const r = await authFetch('/api/model-config/task-models', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(full),
      })
      if (!r.ok) {
        const b = (await r.json().catch(() => null)) as { detail?: string } | null
        toast(b?.detail || `Could not save (HTTP ${r.status})`)
        return
      }
      void modelConfig.refresh()
      announceModelConfigChanged()
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  return (
    <>
      <Panel
        title="AI providers"
        sub="Credentials and endpoints, and the model Lucius runs by default"
        actions={
          <Btn size="xs" onClick={() => setEditor({ mode: 'add' })}>
            <Plus size={12} aria-hidden="true" /> Add provider
          </Btn>
        }
      >
        {rows.length ? (
          <div className="rd-provlist" role="radiogroup" aria-label="Default provider">
            {(() => {
              const row = (entry: (typeof rows)[number]) => (
                <ProviderRow
                  key={entry.key}
                  entry={entry}
                  cfg={configured[entry.key] ?? DETECTED_CLI_CFG}
                  isDefault={defaultKey === entry.key}
                  detected={Boolean(detected[entry.key])}
                  detectedPath={detectedPaths[entry.key] || ''}
                  toast={toast}
                  onEdit={(k) => setEditor({ mode: 'edit', key: k })}
                  onToggle={(k, v) =>
                    void putProvider(k, { enabled: v }, v ? 'Enabled' : 'Disabled')
                  }
                />
              )
              return (
                <>
                  {/* The enabled group carries a heading only when there is a disabled group to
                      distinguish it from — otherwise it labels the entire list and says nothing. */}
                  {enabledRows.length && disabledRows.length ? (
                    <div className="rd-provgroup">
                      Enabled
                      <span>{enabledRows.length}</span>
                    </div>
                  ) : null}
                  {enabledRows.map(row)}
                  {disabledRows.length ? (
                    <>
                      {/* Collapsed by default: switched-off providers are the part you are NOT
                          working with, and they outnumbered the live ones here. */}
                      <button
                        type="button"
                        className="rd-provgroup rd-provgroup--toggle"
                        aria-expanded={showDisabled}
                        onClick={() => setShowDisabled((v) => !v)}
                      >
                        <ChevronRight
                          size={11}
                          strokeWidth={2.6}
                          aria-hidden="true"
                          style={{
                            transform: showDisabled ? 'rotate(90deg)' : 'none',
                            transition: 'transform .15s ease',
                          }}
                        />
                        Disabled
                        <span>{disabledRows.length}</span>
                      </button>
                      {showDisabled ? disabledRows.map(row) : null}
                    </>
                  ) : null}
                </>
              )
            })()}
          </div>
        ) : (
          <Empty title="No providers yet" body="Add one to give Lucius a model to think with." />
        )}
        {subscriptions.length > 0 && (
          <div role="group" aria-label="Connect a subscription">
            <div className="rd-provgroup">Connect a subscription</div>
            <div className="rd-provlist">
              {subscriptions.map((entry) => (
                <ProviderRow
                  key={entry.key}
                  entry={entry}
                  cfg={DETECTED_CLI_CFG}
                  isDefault={defaultKey === entry.key}
                  detected={Boolean(detected[entry.key])}
                  detectedPath={detectedPaths[entry.key] || ''}
                  toast={toast}
                  onEdit={(key) => setEditor({ mode: 'edit', key })}
                  onToggle={(key, enabled) =>
                    void putProvider(key, { enabled }, enabled ? 'Enabled' : 'Disabled')
                  }
                  subscription={{
                    platform: providers.data?.cli_host_platform,
                    onCheck: checkCliInstallations,
                    terminal,
                  }}
                />
              ))}
            </div>
          </div>
        )}
        {gatewayMode && (
          <div className="rd-help" role="note">
            {NO_LOCAL_MODELS_NOTE}
          </div>
        )}
        {cliDetectionError && (
          <div className="rd-help" role="status">
            {cliDetectionError}
          </div>
        )}
        <Field
          label="Default model"
          help="What Lucius runs unless a chat or automation picks something else"
        >
          <ModelPicker
            label="Default model"
            models={models}
            value={defaultModelRef}
            annotate={(m) => cliDefaultNote(m, providers.data?.cli_default_models)}
            onChange={(next) => void putDefaultModel(next)}
          />
        </Field>
      </Panel>

      <Panel
        title="Preferred models"
        sub="Your shortlist — pinned to the top of every model picker so the ones you actually use are one click away"
      >
        <PreferredModels
          models={models}
          saved={preferred.data?.models ?? []}
          onSave={(next) => void putPreferred(next, 'Preferred models saved')}
        />
      </Panel>

      <TaskModels
        toast={toast}
        defaultModel={defaultModel}
        models={models}
        taskModels={modelConfig.data?.task_models ?? {}}
        onSave={(next) => void putTaskModels(next)}
      />

      <TurnTimeLimitPanel toast={toast} />

      {editor && catalog.length > 0 && (
        <ProviderModal
          mode={editor.mode}
          editKey={editor.key}
          cfg={editor.key ? configured[editor.key] : undefined}
          catalog={catalog}
          models={models}
          cliPlatform={providers.data?.cli_host_platform}
          detectedClis={detected}
          onCheckCli={checkCliInstallations}
          terminal={terminal}
          taken={takenKeys}
          isDefault={editor.key === defaultKey}
          toast={toast}
          onClose={() => setEditor(null)}
          onSave={(key, patch) => {
            const entry = catalog.find((e) => e.key === key)
            const body: Record<string, unknown> = { enabled: true }
            if (entry?.cli_binary) body.base_url = patch.bin
            else body.base_url = patch.url
            if (patch.model) body.model = patch.model
            if (patch.key) body.api_key = patch.key
            void putProvider(key, body, configured[key] ? 'Provider updated' : 'Provider added')
          }}
          onRemove={(key) => void removeProvider(key)}
        />
      )}
    </>
  )
}
