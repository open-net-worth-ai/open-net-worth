// Chip.tsx — shared mention chip primitive.
//
// Ports the prototype's Chip / makeChip logic into a React component.
// The real app does NOT have a live D.byId data layer — a composer chip carries a
// MentionRef {type, ref, label} plus optional display fields from the picker row.
// Live re-resolution is deferred; the chip shows only what it was given.
//
// Variant / role matrix (mirrors prototype makeChip logic):
//   role='recipient'  → first agent chip: shows "To ·" prefix
//   role='ctx'        → second+ agent: dashed border, no "To"
//   role='verb'       → skill chip invoked as a /verb: no glyph, "/name" label
//   variant='compact' → smaller padding, hide sublabel/value
//   variant='rich'    → larger padding, more rounded

import type { MentionRef } from '../../../types/mentions'
import { GLYPH_SVG, Spark } from '../../../lucius/identity'
import { iconFor } from '../../../lucius/entityRegistry'
import { colorFor, C } from '../../../lucius/mentionColors'
import { isSafeCssColor, renderMaskableSublabel } from '../../../utils/mentionable'
import { labelFor } from '../../../lucius/entityRegistry'
import { X } from '../../../ui/icons'
import { useToolOwnerLogos } from '../toolActivity/useToolOwnerLogos'

export interface ChipProps {
  mention: MentionRef
  /** 'recipient' = first agent (shows "To ·"); 'ctx' = second agent (dashed); 'verb' = /name */
  role?: 'recipient' | 'ctx' | 'verb'
  variant?: 'default' | 'compact' | 'rich'
  /** Optional sublabel from the picker row (e.g. '50 shares'). */
  sublabel?: string
  /** Optional value string (e.g. '$12,340') — shown tinted for transactions. */
  value?: string
  /** True when the underlying entity was removed / changed since staging. */
  stale?: boolean
  /** When provided, renders the remove button (×). */
  onRemove?: (m: MentionRef) => void
  /** Optional click handler (e.g. to show detail popover). */
  onClick?: (m: MentionRef, anchor: HTMLElement) => void
}

export function Chip({
  mention,
  role,
  variant = 'default',
  sublabel,
  stale,
  onRemove,
  onClick,
}: ChipProps) {
  const { type, label } = mention
  const connectorLogo = useToolOwnerLogos(type === 'connector' ? [mention.ref] : [])(
    type === 'connector' ? mention.ref : '',
  ).logo

  // 'agent' joins 'screen'/'source' as a UI-side chip type: the mention REGISTRY carries no
  // 'agent' provider (agent is a SURFACE — `surfaces ⊆ {entity,agent,verb}` — and routing
  // rides `responding_agent`, not a mention type), so the union cannot name it. Cast-guarded
  // to keep the rendering behaviour identical rather than inventing a backend type.
  const isAgent = (type as string) === 'agent'
  // 'scope' and 'source' are prototype-side synthetic types; in the real app
  // scope-chips carry type 'screen' (handled via the compose engine). We guard
  // against those phantom types here for robustness.
  const isScope = (type as string) === 'screen'
  const isSource = (type as string) === 'source'
  const isVerb = role === 'verb' || type === 'skill'

  // Primary label: skill/verb → "/name"; otherwise the mention label.
  const primaryText = isVerb ? (label.startsWith('/') ? label : '/' + label) : label

  const showMeta = variant !== 'compact'
  // mention.chipDate / mention.chipAmt are the resolved display fields set by MentionText
  // (or by the composer pick path). They take precedence over the sublabel/value props.
  // chipAmtNeg undefined → neutral (no pos/neg class); true/false → neg/pos.
  const chipDate = showMeta ? mention.chipDate : undefined
  const chipAmt = showMeta ? mention.chipAmt : undefined

  // Build className string — mirrors prototype's cls computation exactly.
  let cls = 'chip'
  if (isAgent) cls += ' agent'
  if (isScope) cls += ' scope'
  if (isSource) cls += ' source'
  if (isVerb) cls += ' verb'
  if (role === 'ctx') cls += ' ctx'
  if (variant === 'compact') cls += ' compact'
  if (variant === 'rich') cls += ' rich'
  if (!onRemove) cls += ' readonly'
  if (stale) cls += ' stale'

  // ── Glyph logic ────────────────────────────────────────────────────────────
  // Matches prototype: verb/skill → no glyph; scope/source → text glyph;
  // agent → chip-glyph (Spark); GLYPH_SVG[type] → chip-gl with SVG; else text fallback.
  // Color is resolved through colorFor() — never read item.dot ad-hoc.
  // Transaction direction and account color are passed as dot so colorFor() returns them
  // at step 1. All other types get their TYPE_COLOR default.
  function renderGlyph() {
    if (isVerb) return null

    if (connectorLogo) return <img className="chip-logo" src={connectorLogo} alt="" />

    if (isScope || isSource) {
      return <span className="chip-glyph">◉</span>
    }

    if (isAgent) {
      return (
        <span className="chip-glyph">
          <Spark size={10} />
        </span>
      )
    }

    if (GLYPH_SVG[type] !== undefined) {
      // Build the per-item dot for colorFor():
      //   transaction → direction tint (green income / red expense)
      //   any entity with its own display color (account tint, envelope/category dot)
      //     → that color (isSafeCssColor-guarded)
      //   others      → no dot (TYPE_COLOR default applies)
      let dot: string | undefined
      if (type === 'transaction' && mention.chipAmtNeg !== undefined) {
        dot = mention.chipAmtNeg ? C.neg : C.pos
      } else if (mention.color && isSafeCssColor(mention.color)) {
        dot = mention.color
      }
      const glColor = colorFor({ type, dot })
      return (
        <span
          className="chip-gl"
          style={{ color: glColor }}
          dangerouslySetInnerHTML={{ __html: GLYPH_SVG[type] ?? '' }}
        />
      )
    }

    // Fallback for types with no SVG (category, envelope, budget_month, etc.) — a text
    // bullet, tinted by the entity's own display color when it carries one.
    const fallbackDot = mention.color && isSafeCssColor(mention.color) ? mention.color : undefined
    const glColor = colorFor({ type, dot: fallbackDot })
    // The descriptor's own glyph, not a bullet: GLYPH_SVG covers only 6 types, so every
    // other type used to render an identical dot in the transcript. iconFor() is the same
    // source MentionComposer already draws its inline chips from.
    return (
      <span className="chip-gl" style={{ color: glColor }}>
        {iconFor(type)}
      </span>
    )
  }

  function handleClick(e: React.MouseEvent<HTMLSpanElement>) {
    e.stopPropagation()
    if (onClick) onClick(mention, e.currentTarget)
  }

  function handleRemove(e: React.MouseEvent<HTMLButtonElement>) {
    e.stopPropagation()
    if (onRemove) onRemove(mention)
  }

  const typeLabel = labelFor(type)

  return (
    <span
      className={cls}
      data-type={type}
      data-ref={mention.ref}
      title={typeLabel}
      onClick={handleClick}
    >
      {/* "To •" prefix for the recipient agent chip */}
      {isAgent && role === 'recipient' && <span className="chip-to">To</span>}

      {renderGlyph()}

      <span className="chip-name">{primaryText}</span>

      {showMeta && sublabel && !chipDate && (
        <span className="chip-meta">{renderMaskableSublabel(sublabel)}</span>
      )}

      {chipDate && <span className="chip-meta">{chipDate}</span>}

      {chipAmt && (
        <span
          className={
            'chip-amt money' +
            (mention.chipAmtNeg === true ? ' neg' : mention.chipAmtNeg === false ? ' pos' : '')
          }
        >
          {chipAmt}
        </span>
      )}

      {onRemove && (
        <button className="chip-x" onClick={handleRemove} aria-label={'Remove ' + label}>
          <X size={10} aria-hidden="true" />
        </button>
      )}
    </span>
  )
}
