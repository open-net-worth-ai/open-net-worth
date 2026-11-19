import { Tag } from '../../ui'
import { LuciusMark } from '../../../ui/icons'

/** A paper portfolio Lucius trades on his own: his star on solid ink, apart from the blue
 *  Paper tag beside it. */
export function AutopilotTag() {
  return (
    <Tag tone="lucius">
      <LuciusMark size={12} aria-hidden="true" />
      Autopilot
    </Tag>
  )
}
