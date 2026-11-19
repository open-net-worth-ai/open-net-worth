import { useFlag } from '../../../hooks/useFlag'
import { LEVELS } from './model'
import { LEVELS_V2 } from './modelV2'

/** How the three levels are named under the permission engine in use; undefined while the flag
 *  is still loading. For a control that draws a level outside the ladder itself (the ladder
 *  has its own switch, `PermissionLadderSwitch`). */
export function usePermissionLevels(): typeof LEVELS | undefined {
  const v2 = useFlag('permissions_v2')
  if (v2 === undefined) return undefined
  return v2 ? LEVELS_V2 : LEVELS
}
