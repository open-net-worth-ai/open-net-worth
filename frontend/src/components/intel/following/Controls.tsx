import type { ReactNode } from 'react'

// The controls every Famous Portfolios toolbar uses, and nothing else: Investments' own (its
// search, its dropdown and its filter pills, the same classes), so the two screens match and
// these three screens can never drift apart from each other.

interface Chip<T extends string> {
  value: T
  label: ReactNode
  /** Shown beside the label; a filter with nothing in it shows 0, it never disappears, so the
   *  row never changes width under the reader. */
  count?: number
}

export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Chip<T>[]
  value: T
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="mk-chips fol-chips" role="tablist" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          className={`mk-chip${value === o.value ? ' is-on' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.count != null && <span className="mk-chip-n"> {o.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function SearchBox({
  value,
  onChange,
  label,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  label: string
  placeholder: string
}) {
  return (
    <label className="mk-search-wrap">
      <svg
        width="16"
        height="16"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        aria-hidden="true"
      >
        <circle cx="7" cy="7" r="4.5" />
        <path d="m10.4 10.4 3.1 3.1" strokeLinecap="round" />
      </svg>
      <input
        className="mk-search"
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}

export function ToolSelect<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
  label: string
  className?: string
}) {
  return (
    <select
      className={`mk-toolsel${className ? ` ${className}` : ''}`}
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}
