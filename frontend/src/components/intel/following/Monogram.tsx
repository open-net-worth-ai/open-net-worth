import { initials } from '../../../lucius/identity'

/** A follow's mark: its initials (the app's own `initials`). There is no logo source, and a
 *  made-up one would be a lie. */
export function Monogram({ name }: { name: string }) {
  return (
    <span className="fol-mono" aria-hidden="true">
      {initials(name)}
    </span>
  )
}
