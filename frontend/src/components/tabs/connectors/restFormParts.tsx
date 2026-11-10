// The REST connector's form, in the connectors screen's own form language (`rd-*`): the same
// 34px fields, 13px text, small labels and help lines the MCP server form is built from, so the
// two forms of this screen read as one (owner, 2026-11-11 — the shared `ui/` kit's larger
// controls looked like another app's inside this dialog).

import { useState, type JSX, type ReactNode } from 'react'
import { Eye, EyeOff } from '../../../ui/icons'

export interface RestFieldProps {
  label: ReactNode
  /** The control's id, so the label is tied to it. */
  htmlFor?: string
  required?: boolean
  /** A line under the control: what it is for. */
  help?: ReactNode
  /** In a form grid, span its whole width (every field of this form does). */
  full?: boolean
  children: ReactNode
}

export function RestField({
  label,
  htmlFor,
  required,
  help,
  full = true,
  children,
}: RestFieldProps): JSX.Element {
  return (
    <div className={'rd-field' + (full ? ' full' : '')}>
      <label className="rd-label" htmlFor={htmlFor}>
        {label}
        {required ? (
          <span className="rd-req" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      {children}
      {help ? <span className="rd-help">{help}</span> : null}
    </div>
  )
}

export interface SecretInputProps {
  id?: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  'aria-label'?: string
}

/** A key, token or password: hidden as typed, with the eye to check it. Never pre-filled — the
 *  server does not send a saved value back. */
export function SecretInput({ id, value, onChange, placeholder, ...rest }: SecretInputProps) {
  const [shown, setShown] = useState(false)
  return (
    <div className="rd-secret">
      <input
        id={id}
        className="rd-input"
        type={shown ? 'text' : 'password'}
        autoComplete="off"
        placeholder={placeholder}
        value={value}
        aria-label={rest['aria-label']}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="rd-secret-eye"
        onClick={() => setShown((v) => !v)}
        aria-label={shown ? 'Hide secret' : 'Show secret'}
        aria-pressed={shown}
        title={shown ? 'Hide' : 'Show'}
        tabIndex={-1}
      >
        {shown ? <EyeOff size={15} strokeWidth={1.7} /> : <Eye size={15} strokeWidth={1.7} />}
      </button>
    </div>
  )
}
