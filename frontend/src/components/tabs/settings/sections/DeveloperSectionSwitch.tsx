import { useFlag } from '../../../../hooks/useFlag'
import { useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
import { DeveloperSection } from './DeveloperSection'
import { DeveloperSectionV2 } from './DeveloperSectionV2'

/** Settings → Developer's ONE switch point for `permissions_v2`: with it on, the run_code
 * "Always allow" and "Unattended script runs" switches are gone (a tool's value in Settings →
 * Permissions replaced them); with it off they are exactly as before. */
export function DeveloperSectionSwitch({ toast }: SectionProps) {
  const v2 = useFlag('permissions_v2')
  useSectionBusy(v2 === undefined)
  if (v2 === undefined) return null
  return v2 ? <DeveloperSectionV2 toast={toast} /> : <DeveloperSection toast={toast} />
}
