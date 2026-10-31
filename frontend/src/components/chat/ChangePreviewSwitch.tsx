import { useFlag } from '../../hooks/useFlag'
import { Skeleton } from '../ui'
import { ChangePreview } from './ChangePreview'
import { ChangePreviewV2, type ChangePreviewV2Props } from './ChangePreviewV2'

/**
 * The approval card's ONE switch point for `permissions_v2` (docs/FEATURE_FLAGS.md): the old
 * Allow once / Always allow / Deny-or-Reject card, or the three-answer v2 card. A card still
 * waiting for the flag list renders a placeholder, never a guess.
 */
export function ChangePreviewSwitch(props: ChangePreviewV2Props) {
  const v2 = useFlag('permissions_v2')
  if (v2 === undefined) {
    return (
      <div className="change-preview" aria-busy="true">
        <Skeleton height={64} />
      </div>
    )
  }
  return v2 ? <ChangePreviewV2 {...props} /> : <ChangePreview {...props} />
}
