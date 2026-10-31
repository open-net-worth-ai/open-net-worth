import { useFlag } from '../../../../hooks/useFlag'
import { useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
import { PermissionsSection } from './PermissionsSection'
import { PermissionsSectionV2 } from './PermissionsSectionV2'

/** Settings → Permissions' ONE switch point for `permissions_v2`: the old list of stored
 * decisions, or the built-in tool catalogue with one value per tool. */
export function PermissionsSectionSwitch({ toast }: SectionProps) {
  const v2 = useFlag('permissions_v2')
  useSectionBusy(v2 === undefined)
  if (v2 === undefined) return null
  return v2 ? <PermissionsSectionV2 toast={toast} /> : <PermissionsSection toast={toast} />
}
