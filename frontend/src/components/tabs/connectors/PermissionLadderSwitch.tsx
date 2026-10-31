import type { JSX } from 'react'
import { useFlag } from '../../../hooks/useFlag'
import { Skeleton } from '../../ui'
import { PermissionLadder, type PermissionLadderProps } from './PermissionLadder'
import { LEVELS_V2 } from './modelV2'

/** The connector permission ladder's ONE switch point for `permissions_v2`: the same ladder,
 * with its levels named Needs approval / Blocked (old) or Ask / Block (new). */
export function PermissionLadderSwitch(props: Omit<PermissionLadderProps, 'levels'>): JSX.Element {
  const v2 = useFlag('permissions_v2')
  if (v2 === undefined) return <Skeleton height={96} />
  return <PermissionLadder {...props} levels={v2 ? LEVELS_V2 : undefined} />
}
