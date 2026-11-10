import type { CSSProperties, ReactNode } from 'react'

export interface MoneyProps {
  /** The pre-formatted currency string (e.g. `fBase(1234)` → "S$1,234.00"). */
  children: ReactNode
  /** Element tag — default 'span'. Use 'div'/'td' to carry the mask on a layout cell. */
  as?: 'span' | 'div'
  className?: string
  style?: CSSProperties
  title?: string
}

/**
 * Money — the maskable-currency primitive (privacy mode). Wrap ANY rendered
 * currency amount in `<Money>` and it masks to `***` under `body.hide-amounts`
 * by construction — no per-selector CSS allowlist to maintain. The mask rule
 * lives in index.css (`body.hide-amounts .money`).
 *
 * Only the figure goes inside, an amount or a percentage of your own — keep labels
 * and dates OUTSIDE so they stay visible while masked (e.g. render
 * `<Money>{fBase(x)}</Money> short`, not the whole string). A market price stays
 * visible. For elements that already carry layout classes (a `<td>`,
 * a flex cell), prefer the `moneyClass()` helper to just add the `.money` class
 * instead of nesting a wrapper.
 */
export function Money({ children, as: Tag = 'span', className, style, title }: MoneyProps) {
  const classes = ['money', className || ''].filter(Boolean).join(' ')
  return (
    <Tag className={classes} style={style} title={title}>
      {children}
    </Tag>
  )
}
