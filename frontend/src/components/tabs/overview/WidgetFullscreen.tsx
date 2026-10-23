// Overview v2 — the fullscreen widget detail view (item 7 of the parity
// audit): clicking a widget outside Customize mode opens a split shell —
// widget content fills the left at full detail, an "Appearance" rail on the
// right folds in title/show-title/viz/height/accent PLUS (item 9) a generic
// renderer for the widget's own declared `def.settings` schema, PLUS (for a
// Lucius-generated widget) a prompt + Regenerate. Ported from the design
// handoff's `app.jsx` fullscreen JSX (~1270-1386) and `.ov-fs-*` CSS.
//
// Edits apply LIVE (via the same `onPatch` every widget content component
// already uses) so the preview updates as you tweak controls; Discard reverts
// to the snapshot DashboardGrid captured when this opened, Save just closes
// (the edits already landed). This is the SAME live-preview-with-discard
// model Customize mode uses for the whole layout — just scoped to one item.

import * as Dialog from '@radix-ui/react-dialog'
import { useEffect, useRef, useState } from 'react'
import type { ComponentType, ReactElement, ReactNode } from 'react'
import { Button } from '../../ui'
import { BottomSheet } from '../../ui/BottomSheet'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { CheckboxGroup } from '../../ui/CheckboxGroup'
import { X, ChevronDown } from '../../../ui/icons'
import { useCategories, usePlanningSummary } from '../../../hooks/serverState'
import { ColorPicker } from '../accounts/ds'
import { AUTOH_IDS, fitMode } from './grid'
import { ALL_ROWS, CURATED_ACCENTS, DEFAULT_CUSTOM_HEX, rowCap } from './widgetDisplayShared'
import { resolveCategoryIds } from './categoryRefs'
import { GridItemContext } from './gridItemContext'
import { BriefingFocusControl } from './BriefingFocusControl'
import { vizOptionsFor, vizOf } from './vizModel'
import type {
  HeightMode,
  RegisterDeepLink,
  WidgetContentProps,
  WidgetDefinition,
  WidgetInstance,
} from './types'

// A small pinned list — the generic Data-settings renderer only needs common
// pairs (FX watch-pairs today); this is NOT the app's canonical currency
// list (that lives in tabs/accounts/ds.tsx, private to that module).
const COMMON_CCY = [
  'USD',
  'SGD',
  'EUR',
  'GBP',
  'JPY',
  'CNY',
  'HKD',
  'AUD',
  'CAD',
  'CHF',
  'INR',
  'MYR',
  'NZD',
  'KRW',
  'THB',
  'VND',
  'IDR',
  'PHP',
]

// Ported verbatim from the handoff's `VIZ_ICON` (app.jsx) — a glyph per viz id,
// shown beside the label on each "Show as" chip. `line` has no prototype
// entry either (the `lux` shape's Line option renders label-only there too),
// so it's intentionally absent here — not a gap, a faithful mirror.
const VIZ_ICON: Record<string, ReactElement> = {
  list: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <line x1="5" y1="7" x2="19" y2="7" />
      <line x1="5" y1="12" x2="19" y2="12" />
      <line x1="5" y1="17" x2="14" y2="17" />
    </svg>
  ),
  bar: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <rect x="4" y="11" width="4" height="9" rx="1" />
      <rect x="10" y="6" width="4" height="14" rx="1" />
      <rect x="16" y="9" width="4" height="11" rx="1" />
    </svg>
  ),
  pie: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
    >
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 12V3.5M12 12l7.5 4" />
    </svg>
  ),
  trend: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 16l5-6 4 3 7-8" />
    </svg>
  ),
  comp: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="6" y="4" width="12" height="7" rx="1" />
      <rect x="6" y="13" width="12" height="7" rx="1" />
    </svg>
  ),
  chart: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 16l5-6 4 3 7-8" />
    </svg>
  ),
  numbers: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M7 8h2v8" />
      <path d="M13 8h3a1.5 1.5 0 0 1 0 3h-1a1.5 1.5 0 0 1 0 3h-2" />
    </svg>
  ),
  full: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3.5" y="6" width="5.5" height="12" rx="1" />
      <path d="M12 15l3-3 2 2 4-5" />
    </svg>
  ),
  treemap: (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3.5" y="3.5" width="10" height="10" rx="1.5" />
      <rect x="15.5" y="3.5" width="5" height="5" rx="1.5" />
      <rect x="15.5" y="10.5" width="5" height="10" rx="1.5" />
      <rect x="3.5" y="15.5" width="10" height="5" rx="1.5" />
    </svg>
  ),
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

// Generic FX-pairs editor for a `settings` entry of `type: 'pairs'` — mirrors
// the handoff's `PairsEditor`. Enumerates CURRENCIES, not pairs; the cross is
// derived, so this only manages the user's extra "watch" list.
function PairsEditor({
  value,
  onChange,
}: {
  value: unknown
  onChange: (pairs: [string, string][]) => void
}) {
  const pairs: [string, string][] = Array.isArray(value) ? (value as [string, string][]) : []
  const [base, setBase] = useState('GBP')
  const [quote, setQuote] = useState('SGD')
  const add = () => {
    if (base === quote) return
    if (pairs.some((p) => p[0] === base && p[1] === quote)) return
    onChange([...pairs, [base, quote]])
  }
  const remove = (i: number) => onChange(pairs.filter((_, j) => j !== i))
  return (
    <div className="ov-fs-pairs">
      {pairs.length > 0 && (
        <div className="ov-fs-pairlist">
          {pairs.map((p, i) => (
            <span className="ov-fs-pairchip" key={p[0] + p[1]}>
              {p[0]}/{p[1]}
              <button type="button" onClick={() => remove(i)} aria-label={`Remove ${p[0]}/${p[1]}`}>
                <X size={12} aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="ov-fs-pairadd">
        <select value={base} onChange={(e) => setBase(e.target.value)} aria-label="Base currency">
          {COMMON_CCY.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <span className="ov-fs-pairslash">/</span>
        <select
          value={quote}
          onChange={(e) => setQuote(e.target.value)}
          aria-label="Quote currency"
        >
          {COMMON_CCY.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <button type="button" className="ov-fs-pairbtn" onClick={add}>
          Add
        </button>
      </div>
    </div>
  )
}

// Live-syncing text field: edits apply as you type, but an EXTERNAL value
// change (e.g. Lucius renaming the widget on regenerate) syncs back into the
// field only while it isn't focused, so it never fights the user's typing —
// mirrors the handoff's `FsTextInput`.
function FsTextInput({
  value,
  placeholder,
  apply,
}: {
  value: string
  placeholder?: string
  apply: (v: string) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el && document.activeElement !== el && el.value !== (value || '')) el.value = value || ''
  }, [value])
  return (
    <input
      ref={ref}
      className="ov-fs-input"
      defaultValue={value}
      placeholder={placeholder}
      onChange={(e) => apply(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
      }}
    />
  )
}

function SwatchRow({
  value,
  onPick,
}: {
  value: string | null | undefined
  onPick: (v: string | null) => void
}) {
  const isCustom = value != null && !CURATED_ACCENTS.some((c) => c.key === value)
  const hex = isCustom ? (value as string) : DEFAULT_CUSTOM_HEX
  return (
    <div className="ov-fs-sw-row">
      <button
        type="button"
        className={'ov-fs-sw ov-fs-sw--none' + (value == null ? ' on' : '')}
        title="None"
        aria-label="None"
        onClick={() => onPick(null)}
      />
      {CURATED_ACCENTS.map((c) => (
        <button
          key={c.key}
          type="button"
          className={'ov-fs-sw' + (value === c.key ? ' on' : '')}
          style={{ background: c.cssVar }}
          title={c.label}
          aria-label={c.label}
          onClick={() => onPick(c.key)}
        />
      ))}
      <ColorPicker value={hex} onChange={(v) => onPick(v)} />
    </div>
  )
}

interface Ctrl {
  group: 'Appearance' | 'Data' | 'Lucius'
  key: string
  node: ReactNode
}

export interface WidgetFullscreenProps {
  instance: WidgetInstance
  def: WidgetDefinition
  Content: ComponentType<WidgetContentProps>
  onPatch: (patch: Partial<WidgetInstance>) => void
  onDiscard: () => void
  onSave: () => void
  onNavigate?: (tab: string) => void
  onOpenAsset?: (label: string) => void
  onAskLucius?: (prompt: string) => void
  onShareWithLucius?: (label: string) => void
  onOpenRegister?: (filter: RegisterDeepLink) => void
  /** Phones only: `view` opens the widget full screen, `edit` opens its settings in a bottom
   *  sheet. Desktop shows both together whatever this says. */
  mode?: 'view' | 'edit'
}

export function WidgetFullscreen({
  instance,
  def,
  Content,
  onPatch,
  onDiscard,
  onSave,
  onNavigate,
  onOpenAsset,
  onAskLucius,
  onShareWithLucius,
  onOpenRegister,
  mode = 'edit',
}: WidgetFullscreenProps) {
  const isPhone = useIsMobile()
  const [viewMode, setViewMode] = useState(mode)
  const cfg = instance.config || {}
  const isAutoh = AUTOH_IDS.has(instance.defId)
  const vizOpts = vizOptionsFor(def)
  const isLux = !!def.lucius || instance.defId === 'briefing' || instance.defId === 'lux-ask'
  // Mirrors GridCard's own accent-tint resolution, so the fullscreen preview
  // shows the SAME emphasis tint as the grid card it was opened from.
  const accentStyle = instance.accent
    ? ({
        '--wtint': instance.accent.startsWith('#')
          ? instance.accent
          : `var(--${instance.accent === 'blue' ? 'accent' : instance.accent === 'green' ? 'good' : instance.accent === 'amber' ? 'warn' : instance.accent === 'violet' ? 'violet' : 'lucius'})`,
      } as React.CSSProperties)
    : undefined

  const appearance: Ctrl[] = []
  appearance.push({
    group: 'Appearance',
    key: 'title',
    node: (
      <div className="ov-fs-ctl" key="title">
        <span className="ov-fs-ctl-l">Title</span>
        <FsTextInput
          value={instance.title || def.title || ''}
          placeholder={def.title || 'Title'}
          apply={(v) => {
            const t = v.trim()
            onPatch({ title: t && t !== def.title ? t : undefined })
          }}
        />
      </div>
    ),
  })
  appearance.push({
    group: 'Appearance',
    key: 'showtitle',
    node: (
      <div className="ov-fs-ctl" key="showtitle">
        <span className="ov-fs-ctl-l">Show title</span>
        <div className="ov-fs-seg">
          {(['Show', 'Hide'] as const).map((o) => (
            <button
              key={o}
              type="button"
              className={'ov-fs-chip' + ((instance.hideTitle ? 'Hide' : 'Show') === o ? ' on' : '')}
              onClick={() => onPatch({ hideTitle: o === 'Hide' })}
            >
              {o}
            </button>
          ))}
        </div>
      </div>
    ),
  })
  if (vizOpts && vizOpts.length > 1) {
    const current = vizOf(instance, def)
    appearance.push({
      group: 'Appearance',
      key: 'viz',
      node: (
        <div className="ov-fs-ctl" key="viz">
          <span className="ov-fs-ctl-l">Show as</span>
          <div className="ov-fs-seg">
            {vizOpts.map((o) => (
              <button
                key={o.id}
                type="button"
                className={'ov-fs-chip ov-fs-chip--viz' + (current === o.id ? ' on' : '')}
                onClick={() => onPatch({ config: { ...cfg, viz: o.id } })}
                title={o.label}
              >
                {VIZ_ICON[o.id]}
                <span>{o.label}</span>
              </button>
            ))}
          </div>
          <span className="ov-fs-ctl-hint">Ways this widget can show its data</span>
        </div>
      ),
    })
  }
  if (isAutoh) {
    const fit = fitMode(instance.defId, instance.heightMode)
    appearance.push({
      group: 'Appearance',
      key: 'height',
      node: (
        <div className="ov-fs-ctl" key="height">
          <span className="ov-fs-ctl-l">Height</span>
          <div className="ov-fs-seg">
            {(['Fit', 'Fixed'] as const).map((o) => (
              <button
                key={o}
                type="button"
                className={'ov-fs-chip' + ((fit ? 'Fit' : 'Fixed') === o ? ' on' : '')}
                onClick={() =>
                  onPatch({ heightMode: o === 'Fit' ? undefined : ('fixed' as HeightMode) })
                }
              >
                {o}
              </button>
            ))}
          </div>
          <span className="ov-fs-ctl-hint">
            Fit grows the widget to its content • Fixed keeps a set height and scrolls inside
          </span>
        </div>
      ),
    })
  }
  appearance.push({
    group: 'Appearance',
    key: 'color',
    node: (
      <div className="ov-fs-ctl" key="color">
        <span className="ov-fs-ctl-l">Color</span>
        <SwatchRow value={instance.accent} onPick={(v) => onPatch({ accent: v })} />
      </div>
    ),
  })

  // ── Data: declared settings schema (item 9 — the generic renderer every
  // widget def gets for free, never a hand-built per-widget control) + the
  // list row-cap, in that order (mirrors the handoff). ──────────────────────
  const data: Ctrl[] = []
  ;(def.settings || []).forEach((st) => {
    const cur = cfg[st.key] !== undefined ? cfg[st.key] : st.default
    if (st.type === 'segment' && st.options) {
      appearance.push({
        group: 'Appearance',
        key: st.key,
        node: (
          <div className="ov-fs-ctl" key={st.key}>
            <span className="ov-fs-ctl-l">{st.label}</span>
            <div className="ov-fs-seg">
              {st.options.map((o) => (
                <button
                  key={o}
                  type="button"
                  className={'ov-fs-chip' + (cur === o ? ' on' : '')}
                  onClick={() => onPatch({ config: { ...cfg, [st.key]: o } })}
                >
                  {o}
                </button>
              ))}
            </div>
          </div>
        ),
      })
    } else if (st.type === 'categories' || st.type === 'categoryIds') {
      const Row = st.type === 'categoryIds' ? LedgerCategorySettingRow : CategorySettingRow
      appearance.push({
        group: 'Appearance',
        key: st.key,
        node: (
          <Row
            key={st.key}
            schema={st}
            value={(cur as string[]) || []}
            onPatch={(next: string[]) => onPatch({ config: { ...cfg, [st.key]: next } })}
          />
        ),
      })
    } else if (st.type === 'pairs') {
      appearance.push({
        group: 'Appearance',
        key: st.key,
        node: (
          <div className="ov-fs-ctl ov-fs-ctl-wide" key={st.key}>
            <span className="ov-fs-ctl-l">{st.label}</span>
            <PairsEditor
              value={cur}
              onChange={(v) => onPatch({ config: { ...cfg, [st.key]: v } })}
            />
          </div>
        ),
      })
    } else if (st.type === 'url') {
      appearance.push({
        group: 'Appearance',
        key: st.key,
        node: (
          <div className="ov-fs-ctl" key={st.key}>
            <span className="ov-fs-ctl-l">{st.label}</span>
            <input
              className="ov-fs-input"
              defaultValue={typeof cur === 'string' ? cur : ''}
              placeholder="https://…"
              onBlur={(e) => onPatch({ config: { ...cfg, [st.key]: e.target.value.trim() } })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
            />
          </div>
        ),
      })
    }
  })
  if (def.rows) {
    const shown = rowCap(instance, def)
    appearance.push({
      group: 'Appearance',
      key: 'rows',
      node: (
        <div className="ov-fs-ctl" key="rows">
          <span className="ov-fs-ctl-l">Rows</span>
          <div className="ov-fs-seg">
            {def.rows.options.map((o) => {
              const value = o === 'all' ? null : o
              return (
                <button
                  key={String(o)}
                  type="button"
                  className={'ov-fs-chip' + (shown === value ? ' on' : '')}
                  onClick={() => onPatch({ limit: value ?? ALL_ROWS })}
                >
                  {o === 'all' ? 'All' : o}
                </button>
              )
            })}
          </div>
        </div>
      ),
    })
  }

  // ── Lucius: the prompt IS the setting for a generated/Ask widget — no
  // structured data knobs, just a textarea + Regenerate (item 7's must-have
  // for Lucius-generated widgets). Reuses the SAME onPatch(config.prompt) +
  // onAskLucius(prompt) pattern every lux widget content component already
  // calls (content.tsx's LuxHtml, overviewCashflowCore.tsx's LuxAsk) — never
  // a second AI call path. ────────────────────────────────────────────────
  const lux: Ctrl[] = []
  if (instance.defId === 'briefing') {
    // The briefing's prompt is its server-side standing instruction, not a chat message.
    lux.push({
      group: 'Lucius',
      key: 'prompt',
      node: (
        <BriefingFocusControl
          key="prompt"
          legacyPrompt={isRecord(cfg) && typeof cfg.prompt === 'string' ? cfg.prompt : undefined}
          onSaved={() => {
            if (isRecord(cfg) && 'prompt' in cfg) {
              const rest = { ...cfg }
              delete rest.prompt
              onPatch({ config: rest })
            }
          }}
        />
      ),
    })
  } else if (isLux) {
    const promptValue = isRecord(cfg) && typeof cfg.prompt === 'string' ? cfg.prompt : ''
    lux.push({
      group: 'Lucius',
      key: 'prompt',
      node: (
        <div className="ov-fs-ctl" key="prompt">
          <span className="ov-fs-ctl-l">Lucius prompt</span>
          <textarea
            className="ov-fs-prompt"
            defaultValue={promptValue}
            placeholder="Describe the widget you want Lucius to build…"
            onBlur={(e) => onPatch({ config: { ...cfg, prompt: e.target.value } })}
          />
          <button
            type="button"
            className="ov-fs-regen"
            onClick={() => {
              const p = (typeof cfg.prompt === 'string' && cfg.prompt) || promptValue
              onPatch({ config: { ...cfg, prompt: p } })
              onAskLucius?.(p)
            }}
          >
            ✦ Regenerate
          </button>
        </div>
      ),
    })
  }

  const hasData = data.length > 0
  const hasLux = lux.length > 0
  const label = instance.title || def.title || def.label
  const preview = (
    <div
      className={
        'ov-fs-card' +
        (instance.hideTitle ? ' ov-fs-card--no-title' : '') +
        (instance.accent ? ' ov-fs-card--accent' : '')
      }
      style={accentStyle}
    >
      <GridItemContext.Provider
        value={{
          instanceId: instance.instanceId,
          editing: false,
          icon: def.icon,
          onRenameTitle: (t) => onPatch({ title: t }),
        }}
      >
        <Content
          instance={instance}
          def={def}
          onPatch={onPatch}
          onNavigate={onNavigate}
          onOpenAsset={onOpenAsset}
          onAskLucius={onAskLucius}
          onShareWithLucius={onShareWithLucius}
          onOpenRegister={onOpenRegister}
        />
      </GridItemContext.Provider>
    </div>
  )

  // A phone gets the two jobs this dialog does on desktop as two bottom sheets, like every other
  // phone modal: the settings, and the widget itself (a tall sheet, titled with its name) — not
  // a desktop dialog stacked into a column that put the settings a whole widget below.
  if (isPhone && viewMode === 'edit') {
    return (
      <BottomSheet
        title={`Edit ${label}`}
        onClose={onDiscard}
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={onDiscard}>
              Discard
            </Button>
            <Button variant="primary" size="sm" onClick={onSave}>
              Save changes
            </Button>
          </>
        }
      >
        <div className="ov-fs-sheet">
          {appearance.map((c) => c.node)}
          {hasData && (
            <>
              <div className="ov-fs-bar-h">Data</div>
              {data.map((c) => c.node)}
            </>
          )}
          {hasLux && lux.map((c) => c.node)}
        </div>
      </BottomSheet>
    )
  }
  if (isPhone) {
    return (
      <BottomSheet
        title={label}
        tall
        onClose={onDiscard}
        headerAction={
          <Button variant="secondary" size="sm" onClick={() => setViewMode('edit')}>
            Edit
          </Button>
        }
      >
        <div className="ov-fs-sheet-view">{preview}</div>
      </BottomSheet>
    )
  }

  // Desktop "Full screen" is the widget alone, with Edit to bring in the settings — the same
  // split the phone has. It used to open the editor, so Full screen and Edit… were one thing.
  if (viewMode === 'view') {
    return (
      <Dialog.Root
        open
        onOpenChange={(next) => {
          if (!next) onDiscard()
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="ov-fs-backdrop">
            <Dialog.Content className="ov-fs-shell ov-fs-shell--view" aria-describedby={undefined}>
              <Dialog.Title className="ui-visually-hidden">{label} — full screen</Dialog.Title>
              <div className="ov-fs-main">
                <div className="ov-fs-viewbar">
                  <Button variant="secondary" size="sm" onClick={() => setViewMode('edit')}>
                    Edit
                  </Button>
                  <button
                    type="button"
                    className="ov-fs-close"
                    title="Close"
                    aria-label="Close"
                    onClick={onDiscard}
                  >
                    <X size={18} strokeWidth={1.6} aria-hidden="true" />
                  </button>
                </div>
                <div className="ov-fs-preview">{preview}</div>
              </div>
            </Dialog.Content>
          </Dialog.Overlay>
        </Dialog.Portal>
      </Dialog.Root>
    )
  }

  return (
    <Dialog.Root
      open
      onOpenChange={(next) => {
        if (!next) onDiscard()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="ov-fs-backdrop">
          <Dialog.Content className="ov-fs-shell" aria-describedby={undefined}>
            <Dialog.Title className="ui-visually-hidden">{label} — full screen</Dialog.Title>
            <div className="ov-fs-main">
              <div className="ov-fs-preview">{preview}</div>
              {(hasData || hasLux) && (
                <div className="ov-fs-data">
                  {hasData && (
                    <div className="ov-fs-data-sec">
                      <div className="ov-fs-bar-h">Data</div>
                      <div className="ov-fs-inline">{data.map((c) => c.node)}</div>
                    </div>
                  )}
                  {hasLux && <div className="ov-fs-data-sec">{lux.map((c) => c.node)}</div>}
                </div>
              )}
            </div>
            <div className="ov-fs-rail">
              <div className="ov-fs-bar-h">Appearance</div>
              <div className="ov-fs-stack">{appearance.map((c) => c.node)}</div>
              <div className="ov-fs-actions">
                <Button variant="secondary" size="sm" onClick={onDiscard}>
                  Discard
                </Button>
                <Button variant="primary" size="sm" onClick={onSave}>
                  Save changes
                </Button>
              </div>
            </div>
            <button
              type="button"
              className="ov-fs-close"
              title="Close"
              aria-label="Close"
              onClick={onDiscard}
            >
              <X size={18} strokeWidth={1.6} aria-hidden="true" />
            </button>
          </Dialog.Content>
        </Dialog.Overlay>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

interface CategorySettingProps {
  schema: { label: string }
  value: string[]
  onPatch: (next: string[]) => void
}

function CategorySettingRow({ schema, value, onPatch }: CategorySettingProps) {
  const { data: summary } = usePlanningSummary()
  const options = (summary?.envelopes.budgeted_rows || []).map((r: { label: string }) => ({
    value: r.label,
    label: r.label,
  }))
  return <CategoryPickerRow schema={schema} value={value} onPatch={onPatch} options={options} />
}

/** Any ledger category, stored by id so a rename never unhooks the widget. */
function LedgerCategorySettingRow({
  schema,
  value,
  onPatch,
}: CategorySettingProps & { schema: { fallbackLabel?: string } }) {
  const { data: categories } = useCategories()
  const list = Array.isArray(categories) ? categories.filter((c) => c.id !== 'split') : []
  const options = list.map((c) => ({ value: c.id, label: c.label ?? c.id }))
  // Show what the widget actually lists — a stale preset id resolves the same way here.
  const shown = resolveCategoryIds(value, list, schema.fallbackLabel)
  return <CategoryPickerRow schema={schema} value={shown} onPatch={onPatch} options={options} />
}

function CategoryPickerRow({
  schema,
  value,
  onPatch,
  options,
}: CategorySettingProps & { options: { value: string; label: string }[] }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const labelOf = (v: string) => options.find((o) => o.value === v)?.label ?? v
  const display =
    value.length === 0
      ? 'None'
      : value.length === 1
        ? labelOf(value[0]!)
        : `${value.length} selected`

  return (
    <div className="ov-fs-ctl" ref={ref} style={{ position: 'relative' }}>
      <span className="ov-fs-ctl-l">{schema.label}</span>
      <button
        type="button"
        className="ov-fs-chip on"
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          minWidth: 100,
          justifyContent: 'space-between',
        }}
      >
        {display} <ChevronDown size={12} style={{ opacity: 0.6 }} aria-hidden="true" />
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: 4,
            width: 280,
            background: 'var(--bg-2, #171a21)',
            border: '1px solid var(--line-strong)',
            borderRadius: 'var(--radius-md)',
            zIndex: 10,
            padding: 8,
            boxShadow: '0 8px 24px rgba(0,0,0,.4)',
          }}
        >
          <CheckboxGroup options={options} value={value} onChange={onPatch} maxHeight={250} />
        </div>
      )}
    </div>
  )
}
