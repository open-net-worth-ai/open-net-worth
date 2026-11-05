import { useCallback, useSyncExternalStore } from 'react'

/**
 * Hide amounts inside a sentence or a native attribute. CSS masks an element (`.money`), so a
 * figure baked into a string needs cutting out first (<MaskedText>), and a `title` or
 * `aria-label` can't be masked by CSS at all (useMaskText).
 */
const SYMBOL = '[$€£¥₹₩₫฿₪₱₴₦₡]'
const PREFIX = `(?:[A-Z]{1,3}\\$|${SYMBOL}|\\b[A-Z]{3}[\\s\\u00a0]?)`
const NUMBER = '\\d+(?:[.,\\u00a0\\u202f]\\d+)*'
const SIGN = '[+\\-−–]?'

// A currency figure (either side of its symbol, S$12.5K / 1.234,56 €), a percentage, or a bare
// amount with cents, or a holding's size. A plain count ("in 40 days") is not a figure.
const FIGURE = new RegExp(
  `(?<![\\w.,])(?:${SIGN}${PREFIX}\\s?${NUMBER}(?:[KMBT]\\b)?` +
    `|${SIGN}${NUMBER}\\s?%` +
    `|${SIGN}${NUMBER}\\s?${SYMBOL}` +
    `|${NUMBER}[.,]\\d{2}\\b` +
    `|${NUMBER}\\s(?:units?|shares?)\\b)`,
  'g',
)

export interface TextPiece {
  text: string
  figure: boolean
}

export function splitFigures(text: string): TextPiece[] {
  const pieces: TextPiece[] = []
  let at = 0
  for (const match of text.matchAll(FIGURE)) {
    if (match.index > at) pieces.push({ text: text.slice(at, match.index), figure: false })
    pieces.push({ text: match[0], figure: true })
    at = match.index + match[0].length
  }
  if (at < text.length) pieces.push({ text: text.slice(at), figure: false })
  return pieces
}

export function maskFigures(text: string): string {
  return text.replace(FIGURE, '***')
}

export function amountsHidden(): boolean {
  return document.body.classList.contains('hide-amounts')
}

const listeners = new Set<() => void>()
let observer: MutationObserver | null = null
let lastHidden = false

/** Told when Hide amounts flips, from wherever it was flipped. The body class is the one flag. */
export function subscribeAmountsHidden(listener: () => void): () => void {
  listeners.add(listener)
  if (!observer) {
    lastHidden = amountsHidden()
    observer = new MutationObserver(() => {
      if (amountsHidden() === lastHidden) return
      lastHidden = !lastHidden
      listeners.forEach((l) => l())
    })
    observer.observe(document.body, { attributes: true, attributeFilter: ['class'] })
  }
  return () => {
    listeners.delete(listener)
  }
}

export function useAmountsHidden(): boolean {
  return useSyncExternalStore(subscribeAmountsHidden, amountsHidden)
}

/** For a `title` or `aria-label`: the figures in the string become `***` while amounts are
 *  hidden, and the words stay. */
export function useMaskText(): (text: string) => string {
  const hidden = useAmountsHidden()
  return useCallback((text: string) => (hidden ? maskFigures(text) : text), [hidden])
}
