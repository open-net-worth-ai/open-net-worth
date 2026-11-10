// One numbered step of a connector's setup (keys → link a bank → choose accounts). A done step
// shows a check instead of its number; the current one is the only emphasised step, so the
// order a new user has to follow is visible at a glance.
import type { ReactNode } from 'react'
import { Check } from '../../../ui/icons'

export type SetupStepState = 'todo' | 'current' | 'done'

export function SetupStep({
  n,
  title,
  state,
  aside,
  children,
}: {
  n: number
  title: string
  state: SetupStepState
  /** Right-aligned in the step's heading — e.g. "Edit keys" on a done step. */
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className={`setup-step setup-step--${state}`} aria-label={`Step ${n}: ${title}`}>
      <div className="setup-step-head">
        <span className="setup-step-n" aria-hidden="true">
          {state === 'done' ? <Check size={13} /> : n}
        </span>
        <h4 className="setup-step-title">{title}</h4>
        {state === 'done' && <span className="sr-only">(done)</span>}
        {aside && <div className="setup-step-aside">{aside}</div>}
      </div>
      <div className="setup-step-body">{children}</div>
    </section>
  )
}
