import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useIsMobile } from '../../hooks/useIsMobile'
import { X, ChevronLeft, ChevronRight, ChevronDown, Check } from '../../ui/icons'
import type { ModelOption } from '../../api/models'

// The ONE model chooser. Every surface that lets a human pick a model uses it: the chat
// side panel, the Lucius dock, Settings → Task models (×13), Settings → Preferred models,
// Settings → Dedup, and the Automation editor.
//
// It replaced six divergent controls (native <select> in three different skins, a bespoke
// search combo, and an Automation <select> fed by a list that never included the provider
// catalog at all — so a model you had not already used somewhere else was UNREACHABLE).
// That last one is why this is a component and not a shared options-builder: the bug was
// possible because each site sourced and rendered its own list.
//
// Desktop = anchored popover, phone = bottom sheet — the same split ActionMenu makes, for
// the same reason (a popover anchored to a 28px trigger is unusable with a thumb).
//
// Search is the point: a configured OpenRouter account alone makes this list 300+ entries.

/** A model's canonical picker value: provider-pinned when we know the provider. */
export function modelRefOf(m: ModelOption): string {
  return m.provider_key ? `${m.provider_key}:${m.id}` : m.id
}

/** The bare model id inside a ref (`openai:gpt-4o` → `gpt-4o`). */
export function bareModelId(ref: string): string {
  return ref.includes(':') ? ref.slice(ref.indexOf(':') + 1) : ref
}

/** A resolved selection — the provider and the model, never just one of them.
 *
 *  A bare model id is genuinely ambiguous here: `sonnet` names a Claude Code CLI alias, an
 *  Anthropic model AND an OpenRouter model, billed three different ways. Anything that
 *  DISPLAYS a selection must show both, so this is the shape every caller resolves through
 *  rather than re-deriving the split from the ref string. */
export interface ModelSelection {
  /** The stored ref: `''` (instance default), `'<model>'`, or `'<provider_key>:<model>'`. */
  ref: string
  /** The bare model id, never carrying a provider prefix. */
  modelId: string
  /** Catalogue key (`openai`, `claude_cli`, …); `''` when the ref carries no provider. */
  providerKey: string
  /** Human-facing provider name; `''` when it cannot be resolved from the model list. */
  providerLabel: string
  /** The catalogue entry, when the ref matched one — absent for a since-removed provider. */
  model?: ModelOption
  /** True for the `''` ref: whatever Settings → AI Models currently points at. */
  isDefault: boolean
}

export function resolveModelSelection(ref: string, models: ModelOption[]): ModelSelection {
  const clean = (ref || '').trim()
  const pinned = models.find((m) => modelRefOf(m) === clean)
  // A ref WITHOUT a provider prefix still names a real model — the instance default is
  // stored bare (`grok-4.5`, not `xai:grok-4.5`), and so are legacy per-conversation
  // overrides. Resolve it by bare id so the provider can still be named. Only when the id
  // is UNAMBIGUOUS: `sonnet` exists on Claude Code CLI, Anthropic and OpenRouter at once,
  // and naming the wrong one is worse than naming none.
  const bareMatches = pinned ? [] : models.filter((m) => m.id === clean)
  const match = pinned ?? (bareMatches.length === 1 ? bareMatches[0] : undefined)
  const providerKey = clean.includes(':') ? clean.slice(0, clean.indexOf(':')) : ''
  return {
    ref: clean,
    modelId: bareModelId(clean),
    providerKey: match?.provider_key ?? providerKey,
    // Fall back to the catalogue KEY when no entry matched: naming `openrouter` still beats
    // showing nothing for a model whose provider was since disabled or removed.
    providerLabel: match?.provider_label ?? providerKey,
    model: match,
    isDefault: clean === '',
  }
}

interface ModelGroup {
  label: string
  models: ModelOption[]
}

/** Preferred first (it is the one group that mixes providers), then one group per provider.
 *  A preferred model ALSO stays in its provider group — this narrows nothing. */
export function groupModels(models: ModelOption[]): ModelGroup[] {
  const preferred: ModelGroup = { label: 'Preferred', models: [] }
  const groups: ModelGroup[] = []
  const byLabel = new Map<string, ModelGroup>()
  for (const m of models) {
    if (m.preferred) preferred.models.push(m)
    const label = m.provider_label ?? m.provider_key ?? 'Other'
    let group = byLabel.get(label)
    if (!group) {
      group = { label, models: [] }
      byLabel.set(label, group)
      groups.push(group)
    }
    group.models.push(m)
  }
  return preferred.models.length ? [preferred, ...groups] : groups
}

interface BaseProps {
  models: ModelOption[]
  /** aria-label for the trigger AND the sheet title. */
  label: string
  /** Annotation appended to a row, e.g. `— no vision`. */
  annotate?: (m: ModelOption) => string | undefined
  /** Rows this site cannot honour (e.g. a disabled provider). Never selectable. */
  isUnavailable?: (m: ModelOption) => boolean
  size?: 'sm' | 'md'
  /** Native tooltip on the trigger — the chat surfaces explain what the turn will run on. */
  title?: string
  className?: string
  disabled?: boolean
  id?: string
  /**
   * Rendered in the TRIGGER after the model name — the chat surfaces put the effort tier
   * there, so one control reads `sonnet High ⌄` instead of two controls with two different
   * chevrons sitting side by side.
   */
  trailing?: ReactNode
  /**
   * Extra choices that belong to this control but are NOT the model list — the chat surfaces
   * pass one, `Effort`.
   *
   * Passing any turns the panel into a two-level menu: opening it shows a ROOT of rows
   * (`Model  sonnet ›`, `Effort  High ›`) and picking one opens that list on its own, with a
   * back arrow. The two lists never share a panel: a model list is ~300 searchable rows and a
   * tier list is five, so showing both at once buries the short one under the long one.
   * Omit the prop and the panel opens straight onto the model list, as it always has.
   */
  panes?: PickerPane[]
}

/** Back arrow + title, shown at the top of any pane reached from the root menu. */
function PaneHead({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="mp-pane-head">
      <button type="button" className="mp-pane-back" aria-label="Back" onClick={onBack}>
        <ChevronLeft size={16} aria-hidden="true" />
      </button>
      <span className="mp-pane-title">{title}</span>
    </div>
  )
}

export interface PickerPane {
  key: string
  /** Row label in the root menu, and the pane's own title. */
  label: string
  /** The current selection, shown on the root row so the menu answers without being opened. */
  value?: ReactNode
  /** The pane body — typically a `.mp-list` of `.mp-opt` rows. A function receives `close`
   *  so a pane's own selection (e.g. an effort tier) can dismiss the whole panel the same
   *  way picking a model does, instead of leaving it open until an outside click/Escape. */
  content: ReactNode | ((close: () => void) => ReactNode)
}

interface SingleProps extends BaseProps {
  multiple?: false
  value: string
  onChange: (next: string) => void
  /** Fallback text for the `''` row when `defaultModelId` is absent or unresolvable. */
  defaultLabel?: string
  /** The instance default's id/ref. Drives the "Default" GROUP and the trigger, both of
   *  which then name the real model + provider rather than the word "Default". */
  defaultModelId?: string
  /** Trigger text when nothing is chosen and there is no default to name (e.g. a provider's
   *  own "Default model" field, where blank means "not set yet"). */
  placeholder?: string
  /** When set, a search that matches no model id offers `Use “…”` — the typed text, pinned to
   *  this provider — so a model the list cannot know about (a new release, a `:free` variant)
   *  is still choosable. The reported ref is `<providerKey>:<typed>`. */
  customProviderKey?: string
}

interface MultiProps extends BaseProps {
  multiple: true
  value: string[]
  onChange: (next: string[]) => void
  /** Shown in place of the chip row when nothing is picked. */
  emptyHint?: ReactNode
}

export type ModelPickerProps = SingleProps | MultiProps

export function ModelPicker(props: ModelPickerProps) {
  const {
    models,
    label,
    annotate,
    isUnavailable,
    size = 'md',
    className,
    disabled,
    id,
    title,
    trailing,
    panes,
  } = props
  const [open, setOpen] = useState(false)
  // Which pane the panel is showing. null = the root menu (only reachable when `panes` is set;
  // without it the panel opens straight onto the model list).
  const [pane, setPane] = useState<string | null>(panes?.length ? null : 'models')
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null)

  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const isMobile = useIsMobile()

  const multiple = props.multiple === true
  const defaultLabel = multiple ? undefined : (props as SingleProps).defaultLabel
  const defaultModelId = multiple ? undefined : (props as SingleProps).defaultModelId
  const placeholder = multiple ? undefined : (props as SingleProps).placeholder
  const customProviderKey = multiple ? undefined : (props as SingleProps).customProviderKey
  const selected = useMemo(
    () => new Set(multiple ? (props.value as string[]) : []),
    [multiple, props.value],
  )

  const byRef = useMemo(() => {
    const map = new Map<string, ModelOption>()
    for (const m of models) map.set(modelRefOf(m), m)
    return map
  }, [models])

  // The flat, filtered row list the keyboard walks. Sentinel first (single only) so
  // "back to the default" is always one key away rather than buried under a search.
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const out: Array<{
      ref: string
      model?: ModelOption
      group?: string
      text: string
      /** Provider shown ON the row — only where the group header does not already say it. */
      provider?: string
      /** The typed text offered as a model id the list does not contain. */
      custom?: boolean
    }> = []
    // The instance default gets its OWN group and reads like every other row: the model
    // that will actually run, plus its provider. "Default (sonnet)" as a bare row said the
    // same thing twice and looked nothing like its neighbours.
    if (!multiple && !needle && (defaultModelId !== undefined || defaultLabel !== undefined)) {
      const d = defaultModelId ? resolveModelSelection(defaultModelId, models) : null
      out.push({ ref: '__group__Default', group: 'Default', text: 'Default' })
      out.push({
        ref: '',
        model: d?.model,
        text: d?.modelId || defaultLabel || 'Default',
        provider: d?.providerLabel || undefined,
      })
    }
    for (const group of groupModels(models)) {
      const hits = group.models.filter((m) => {
        if (!needle) return true
        const ref = modelRefOf(m).toLowerCase()
        return ref.includes(needle) || (m.provider_label ?? '').toLowerCase().includes(needle)
      })
      if (!hits.length) continue
      out.push({ ref: `__group__${group.label}`, group: group.label, text: group.label })
      const inPreferred = group.label === 'Preferred'
      for (const m of hits) {
        out.push({
          ref: modelRefOf(m),
          model: m,
          text: m.id,
          provider: inPreferred ? (m.provider_label ?? undefined) : undefined,
        })
      }
    }
    // A typed id the list lacks is still a valid choice — and it goes FIRST, so it is one
    // keystroke from the box rather than buried under 300 near-matches.
    const typed = q.trim()
    if (
      customProviderKey &&
      typed &&
      !models.some((m) => m.id.toLowerCase() === typed.toLowerCase())
    ) {
      out.unshift({ ref: `${customProviderKey}:${typed}`, text: typed, custom: true })
    }
    return out
  }, [models, q, multiple, defaultLabel, defaultModelId, customProviderKey])

  const selectableIdx = useMemo(
    () =>
      rows
        .map((r, i) => (r.group ? -1 : i))
        .filter((i) => i >= 0 && !(rows[i]!.model && isUnavailable?.(rows[i]!.model!))),
    [rows, isUnavailable],
  )

  const hasPanes = !!panes?.length

  // Split so a pane's own content (handed `dismiss`, never a ref) can close the panel
  // without touching `triggerRef` — passing a ref-reading closure into caller-supplied
  // content would let a static analyzer flag a synchronous ref read during render.
  const dismiss = () => {
    setOpen(false)
    setQ('')
    // Next open starts at the root again — a menu that reopens three levels deep is a menu that
    // lies about where you are.
    setPane(hasPanes ? null : 'models')
  }
  const close = (returnFocus = false) => {
    dismiss()
    if (returnFocus) triggerRef.current?.focus()
  }

  useLayoutEffect(() => {
    if (!open || isMobile || !triggerRef.current || !panelRef.current) return
    const t = triggerRef.current.getBoundingClientRect()
    const p = panelRef.current.getBoundingClientRect()
    const top = t.bottom + p.height + 8 > window.innerHeight ? t.top - p.height - 4 : t.bottom + 4
    // 300, not 260: a Preferred row can carry a name, a provider AND a "<CLI> default" note.
    const width = Math.min(Math.max(t.width, 300), 420)
    setPos({
      top: Math.max(4, top),
      left: Math.max(4, Math.min(t.left, window.innerWidth - width - 8)),
      width,
    })
    // `pane` is a dependency because each pane is a different height, and the panel flips to
    // opening upward when it would overflow the viewport — measured per pane, not once.
  }, [open, isMobile, rows.length, pane])

  // Focus the search when the MODEL list is what's on screen — with panes the panel opens on the
  // root menu, where there is no search field to focus (and re-runs on entering the list).
  useEffect(() => {
    if (open && pane === 'models') searchRef.current?.focus()
  }, [open, pane])

  // Deliberately NOT keyed on selectableIdx: that array is rebuilt each render, so
  // including it re-ran this after every render and stomped the hover/arrow highlight
  // back to the first row.
  useEffect(() => {
    setActive(-1)
  }, [q, open])

  useEffect(() => {
    if (!open || !isMobile) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [open, isMobile])

  // Outside-click WITHOUT a scrim. A full-screen scrim (even a transparent one) eats the
  // first click on whatever the user was actually aiming at, which is exactly the "layover"
  // feel this picker should not have. A plain document listener is only safe because two
  // other things are now true: the panel outranks every surface it can be opened from (see
  // .mp-pop's z-index) and [data-portal-layer] restores the pointer events a host Radix
  // modal switched off body-wide. Without those, this check could not tell "clicked
  // outside" from "clicked an overlay sitting on top of us", and every interaction inside
  // the picker dismissed it (owner report 2026-08-06).
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: Event) => {
      const target = e.target as Node
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return
      close()
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  function pick(ref: string) {
    if (multiple) {
      const cur = props.value as string[]
      const next = cur.includes(ref) ? cur.filter((r) => r !== ref) : [...cur, ref]
      ;(props.onChange as (v: string[]) => void)(next)
      // Deliberately stays open: picking a shortlist is a multi-step action, and
      // reopening between every tick is the thing that made the old combo tedious.
      return
    }
    ;(props.onChange as (v: string) => void)(ref)
    close(true)
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault()
      close(true)
      return
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!selectableIdx.length) return
      const at = selectableIdx.indexOf(active)
      const step = e.key === 'ArrowDown' ? 1 : -1
      const next = at < 0 ? 0 : (at + step + selectableIdx.length) % selectableIdx.length
      setActive(selectableIdx[next]!)
      return
    }
    if (e.key === 'Enter' && active >= 0 && rows[active]) {
      e.preventDefault()
      pick(rows[active]!.ref)
      return
    }
    // Nothing highlighted and nothing in the list matched: the typed id is the only choice, so
    // Enter takes it. With matches on screen Enter does nothing — a half-typed `glm` must never
    // be committed as a model id by a stray keypress.
    if (e.key === 'Enter' && rows.length === 1 && rows[0]!.custom) {
      e.preventDefault()
      pick(rows[0]!.ref)
    }
  }

  // What the trigger says. Both halves matter: the model alone is ambiguous across
  // providers, and the provider alone says nothing about what will run.
  const triggerParts = (() => {
    if (multiple) {
      const n = (props.value as string[]).length
      return { text: n ? `${n} selected — change…` : 'Choose models…', provider: '' }
    }
    const sel = resolveModelSelection(props.value as string, models)
    if (sel.isDefault) {
      // Following the instance default still RUNS a specific model, so name that model and
      // its provider exactly as an explicit pick is named. Which of the two it is shows in
      // the list (the tick sits under the "Default" header); the trigger answers the only
      // question it has room for — what is this turn going to run on?
      const d = defaultModelId ? resolveModelSelection(defaultModelId, models) : null
      return {
        text: d?.modelId || defaultLabel || placeholder || 'Default',
        provider: d?.providerLabel || '',
      }
    }
    return { text: sel.modelId, provider: sel.providerLabel }
  })()

  const list = (
    <>
      <div className="mp-search-wrap">
        <input
          ref={searchRef}
          className="mp-search"
          value={q}
          placeholder="Search models…"
          aria-label="Search models"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>
      <div className="mp-list" role="listbox" aria-label={label}>
        {rows.length === 0 && <div className="mp-empty">No model matches “{q}”.</div>}
        {rows.map((row, i) =>
          row.group ? (
            <div className="mp-group" key={row.ref}>
              {row.group}
            </div>
          ) : (
            <button
              type="button"
              role="option"
              key={row.ref + i}
              aria-selected={multiple ? selected.has(row.ref) : (props.value as string) === row.ref}
              disabled={Boolean(row.model && isUnavailable?.(row.model))}
              className={
                'mp-opt' +
                (i === active ? ' is-active' : '') +
                ((multiple ? selected.has(row.ref) : (props.value as string) === row.ref)
                  ? ' is-on'
                  : '')
              }
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(row.ref)}
            >
              <span className="mp-opt-name">{row.custom ? `Use “${row.text}”` : row.text}</span>
              {row.custom && <em className="mp-opt-note">custom model ID</em>}
              {/* Named only where the group header does NOT already say it — i.e. inside
                  "Preferred" and "Default", the two groups that can hold any provider. A bare
                  `sonnet` there could be the Claude Code CLI alias, an Anthropic model or an
                  OpenRouter one, billed three different ways. Repeating the provider under a
                  provider header would be pure noise on ~300 rows. */}
              {row.provider && <em className="mp-opt-prov">{row.provider}</em>}
              {row.model && annotate?.(row.model) && (
                <em className="mp-opt-note">{annotate(row.model)}</em>
              )}
              <span className="mp-opt-tick" aria-hidden="true">
                <Check size={14} />
              </span>
            </button>
          ),
        )}
      </div>
    </>
  )

  /** Root menu — one row per destination, each naming its current selection. */
  const root = (
    <div className="mp-menu" role="menu" aria-label={label}>
      <button
        type="button"
        role="menuitem"
        className="mp-menu-row"
        onClick={() => setPane('models')}
      >
        <span className="mp-menu-row-l">Model</span>
        <span className="mp-menu-row-v">{triggerParts.text}</span>
        <span className="mp-menu-row-chev" aria-hidden="true">
          <ChevronRight size={14} />
        </span>
      </button>
      {panes?.map((p) => (
        <button
          type="button"
          role="menuitem"
          key={p.key}
          className="mp-menu-row"
          onClick={() => setPane(p.key)}
        >
          <span className="mp-menu-row-l">{p.label}</span>
          <span className="mp-menu-row-v">{p.value}</span>
          <span className="mp-menu-row-chev" aria-hidden="true">
            <ChevronRight size={14} />
          </span>
        </button>
      ))}
    </div>
  )

  const openPane = panes?.find((p) => p.key === pane)
  const paneTitle = pane === null ? label : pane === 'models' ? 'Model' : (openPane?.label ?? '')
  const paneContent =
    pane === null
      ? root
      : pane === 'models'
        ? list
        : typeof openPane?.content === 'function'
          ? openPane.content(dismiss)
          : openPane?.content
  // Desktop: the pane carries its own header. On the phone the SHEET already has a header, and
  // stacking a second one under it ("Chat model ×" over "‹ MODEL") wasted a third of a small
  // screen saying the same thing twice — there the sheet head takes the back arrow instead.
  const body = (
    <>
      {!isMobile && pane !== null && hasPanes && (
        <PaneHead title={paneTitle} onBack={() => setPane(null)} />
      )}
      {paneContent}
    </>
  )

  const panel =
    open &&
    (isMobile ? (
      <div className="mp-sheet" data-portal-layer ref={panelRef} onKeyDown={onKeyDown}>
        <div className="mp-sheet-handle-wrap">
          <span className="mp-sheet-handle" />
        </div>
        <div className="mp-sheet-head">
          {pane !== null && hasPanes && (
            <button
              type="button"
              className="mp-sheet-back"
              aria-label="Back"
              onClick={() => setPane(null)}
            >
              <ChevronLeft size={16} aria-hidden="true" />
            </button>
          )}
          <span className="mp-sheet-title">{paneTitle}</span>
          <button
            type="button"
            className="mp-sheet-x"
            aria-label="Close"
            onClick={() => close(true)}
          >
            <X size={18} strokeWidth={1.6} aria-hidden="true" />
          </button>
        </div>
        {body}
      </div>
    ) : (
      <div
        className="mp-pop"
        data-portal-layer
        ref={panelRef}
        // Hidden for the one frame between mount and measurement — a fixed element with
        // no top/left would otherwise flash at its static position before the layout
        // effect runs.
        style={
          pos
            ? { top: pos.top, left: pos.left, width: pos.width }
            : { top: 0, left: 0, visibility: 'hidden' }
        }
        onKeyDown={onKeyDown}
      >
        {body}
      </div>
    ))

  return (
    <span className={'mp-wrap' + (className ? ` ${className}` : '')}>
      {multiple && (
        <span className="mp-chips">
          {(props.value as string[]).length === 0 && (props as MultiProps).emptyHint}
          {(props.value as string[]).map((ref) => (
            <span className="mp-chip" key={ref}>
              <span className="mp-chip-name">{bareModelId(ref)}</span>
              {byRef.get(ref)?.provider_label && <em>{byRef.get(ref)!.provider_label}</em>}
              <button
                type="button"
                aria-label={`Remove ${ref}`}
                onClick={() =>
                  (props.onChange as (v: string[]) => void)(
                    (props.value as string[]).filter((r) => r !== ref),
                  )
                }
              >
                <X size={12} aria-hidden="true" />
              </button>
            </span>
          ))}
        </span>
      )}
      <button
        ref={triggerRef}
        id={id}
        type="button"
        className={'mp-trigger' + (size === 'sm' ? ' mp-trigger--sm' : '')}
        aria-label={label}
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="mp-trigger-text">{triggerParts.text}</span>
        {triggerParts.provider && <span className="mp-trigger-prov">{triggerParts.provider}</span>}
        {trailing && <span className="mp-trigger-trail">{trailing}</span>}
        <span className="mp-trigger-chev" aria-hidden="true">
          <ChevronDown size={14} />
        </span>
      </button>
      {panel ? createPortal(panel, document.body) : null}
    </span>
  )
}
