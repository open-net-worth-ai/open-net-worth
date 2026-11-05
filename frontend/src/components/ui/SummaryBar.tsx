import type { MentionRef } from '../../types/mentions'
import { MaskedText } from './MaskedText'
import { LuciusAskButton } from '../shared/AskLucius'
import { entityRefProps } from '../../utils/mentionable'

export type StatTone = 'pos' | 'neg' | 'warn'

export interface StatCard {
  label: string
  value: string
  tone?: StatTone
  // Optional sub-label rendered below the value in muted/sm text.
  caption?: string
  // When set, the card gets an "Ask Lucius about this" ✦ that stages this entity as a mention.
  // Opt-in per card — cards without it (derived aggregates) render no affordance.
  mention?: MentionRef
  // When set, the value element is tagged as a data-ctx-fact for the screen snapshot engine.
  factAttr?: string
  onClick?: () => void
}

export interface SummaryBarProps {
  cards: StatCard[]
}

/**
 * SummaryBar — the row of stat cards (.sumbar/.sumcard). Promotes the shared
 * SummaryBar into the kit with a typed `tone` (was the stringly `cls`), which
 * maps to the existing .sv.pos/.neg/.warn colors. A card may opt into the ✦
 * "Ask Lucius" affordance via `mention`.
 */
export function SummaryBar({ cards }: SummaryBarProps) {
  return (
    <div className="sumbar">
      {cards.map(({ label, value, tone, caption, mention, factAttr, onClick }) => {
        const Tag = onClick ? 'button' : 'div'
        return (
          <Tag
            key={label}
            className={`sumcard${onClick ? ' sumcard--clickable' : ''}`}
            {...(onClick ? { type: 'button' as const, onClick } : {})}
            {...(mention ? entityRefProps(mention.type, mention.ref, mention.label ?? label) : {})}
          >
            <div className="sk">{label}</div>
            <div
              className={`sv${tone ? ` ${tone}` : ''}`}
              {...(factAttr ? { 'data-ctx-fact': factAttr } : {})}
            >
              {value}
            </div>
            {caption && (
              <div className="sc">
                <MaskedText>{caption}</MaskedText>
              </div>
            )}
            {mention && (
              <LuciusAskButton
                type={mention.type}
                refId={mention.ref}
                label={mention.label}
                className="sumcard-ask-btn"
              />
            )}
          </Tag>
        )
      })}
    </div>
  )
}

/** StatGrid — alias of SummaryBar (the audit's two names for one primitive). */
export const StatGrid = SummaryBar
