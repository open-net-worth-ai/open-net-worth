import type { ButtonHTMLAttributes, ReactNode } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'
export type ButtonSize = 'sm' | 'md' | 'dense'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  iconLeft?: ReactNode
  fullWidth?: boolean
}

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  danger: 'btn-danger',
  ghost: 'ui-btn--ghost',
}

/**
 * Button — wraps the house .btn-* classes. `loading` shows a spinner and
 * disables the control (new state — no component had a busy button before).
 * `size="sm"` maps onto the .btn-small density.
 */
export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  iconLeft,
  fullWidth = false,
  disabled,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  const classes = [
    'ui-btn',
    VARIANT_CLASS[variant],
    size === 'sm' ? 'ui-btn--sm' : '',
    size === 'dense' ? 'ui-btn--dense' : '',
    fullWidth ? 'ui-btn--full' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <span className="ui-btn__spinner" aria-hidden="true" />}
      {!loading && iconLeft}
      {children}
    </button>
  )
}
