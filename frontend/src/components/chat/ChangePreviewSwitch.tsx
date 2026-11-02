import { FEATURE_FLAGS_KEY, fetchFeatureFlags, useFlag } from '../../hooks/useFlag'
import { useQueryCache } from '../../state/queryCache'
import { Skeleton } from '../ui'
import { ChangePreview } from './ChangePreview'
import { ChangePreviewV2, type ChangePreviewV2Props } from './ChangePreviewV2'

/**
 * The approval card's ONE switch point for `permissions_v2` (docs/FEATURE_FLAGS.md): the old
 * Allow once / Always allow / Deny-or-Reject card, or the three-answer v2 card. A card still
 * waiting for the flag list renders a placeholder, never a guess. If the list cannot be loaded
 * the card shows the old version: an approval the owner has to answer must never sit behind a
 * placeholder that does not go away, and the old card is what the app shows with the flag off.
 */
export function ChangePreviewSwitch(props: ChangePreviewV2Props) {
  const v2 = useFlag('permissions_v2')
  const { error } = useQueryCache(FEATURE_FLAGS_KEY, fetchFeatureFlags, {
    enabled: v2 === undefined,
  })
  if (v2 === undefined && !error) {
    return (
      <div className="change-preview" aria-busy="true">
        <Skeleton height={64} />
      </div>
    )
  }
  return v2 ? <ChangePreviewV2 {...props} /> : <ChangePreview {...props} />
}
