import { Select, type SelectProps } from '../../ui'
import { ChevronDown } from '../../../ui/icons'

/** The kit Select with the app's own chevron in place of the browser's arrow (which differs
 *  per browser and matched nothing else on the page), as Investments' range picker draws it. */
export function PickSelect({ className, ...props }: SelectProps) {
  return (
    <span className={`fol-pick${className ? ` ${className}` : ''}`}>
      <Select {...props} />
      <ChevronDown size={14} strokeWidth={2} className="fol-pick-chev" aria-hidden="true" />
    </span>
  )
}
