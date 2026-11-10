/** A follow's mark: its initials. There is no logo source, and a made-up one would be a lie. */
export function Monogram({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' }) {
  const initials = name
    .split(/\s+/)
    .filter((w) => /^[A-Za-z]/.test(w))
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join('')
  return (
    <span className={`fol-mono fol-mono--${size}`} aria-hidden="true">
      {initials || '?'}
    </span>
  )
}
