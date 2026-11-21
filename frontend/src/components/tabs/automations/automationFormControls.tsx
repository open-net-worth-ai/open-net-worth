// The editor's small form controls, shared by the trigger fields and the condition row.
import { useContext } from 'react'
import { DateField } from '../../ui'
import { ComboSelect } from './ComboSelect'
import { ComboDropdowns } from './comboDropdowns'

export function Row({
  label,
  htmlFor,
  required,
  unit,
  below,
  children,
}: {
  label: string
  htmlFor: string
  required?: boolean
  /** Inline right after the control — what the number means. */
  unit?: React.ReactNode
  /** Its own line under the control, aligned with it. */
  below?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="a3-trow">
      <label className="a3-trow-label" htmlFor={htmlFor}>
        {label}
        {required && (
          <>
            {' '}
            <span className="req">*</span>
          </>
        )}
      </label>
      <div className="a3-trow-ctl">
        {children}
        {unit && <span className="a3-trow-unit">{unit}</span>}
      </div>
      {below && <div className="a3-trow-below">{below}</div>}
    </div>
  )
}
export function Select({
  id,
  value,
  options,
  placeholder,
  needed,
  onChange,
  onCreate,
}: {
  id: string
  value: string
  options: ReadonlyArray<readonly [string, string]>
  placeholder?: string
  needed?: boolean
  onChange: (value: string) => void
  /** Rule editor only: offer "Create “…”" when a typed name matches nothing. */
  onCreate?: (name: string) => Promise<string | null>
}) {
  const combo = useContext(ComboDropdowns)
  if (combo) {
    return (
      <ComboSelect
        id={id}
        value={value}
        options={options.filter(([key]) => key !== '')}
        placeholder={placeholder ?? options.find(([key]) => key === '')?.[1]}
        needed={needed}
        onChange={onChange}
        onCreate={onCreate}
      />
    )
  }
  return (
    <select
      id={id}
      className={`a3-mini-select${needed ? ' needed' : ''}`}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {placeholder && !options.some(([key]) => key === '') && (
        <option value="">{placeholder}</option>
      )}
      {options.map(([key, label]) => (
        <option key={key} value={key}>
          {label}
        </option>
      ))}
    </select>
  )
}
export function OptionSelect({
  options,
  ...props
}: {
  id: string
  value: unknown
  options: Array<{ id: string; label: string }>
  placeholder: string
  needed?: boolean
  onChange: (value: string) => void
  onCreate?: (name: string) => Promise<string | null>
}) {
  return (
    <Select
      {...props}
      value={String(props.value ?? '')}
      options={options.map((option) => [option.id, option.label])}
    />
  )
}
export function Input({
  id,
  value,
  type = 'text',
  placeholder,
  needed,
  list,
  min,
  onChange,
}: {
  id: string
  value: unknown
  type?: string
  placeholder?: string
  needed?: boolean
  list?: string
  min?: number
  onChange: (value: string) => void
}) {
  if (type === 'date') {
    return (
      <DateField
        unstyled
        id={id}
        list={list}
        className={`a3-mini-select${needed ? ' needed' : ''}`}
        value={String(value ?? '')}
        placeholder={placeholder}
        min={min}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }
  return (
    <input
      id={id}
      type={type}
      list={list}
      className={`a3-mini-select${needed ? ' needed' : ''}`}
      value={String(value ?? '')}
      placeholder={placeholder}
      min={min}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}
