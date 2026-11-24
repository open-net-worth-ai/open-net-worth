import { ShieldCheck } from '../../ui/icons'

export function ZeroRetentionBadge() {
  return (
    <span className="ui-badge ui-badge--pos zr-badge">
      <ShieldCheck size={13} aria-hidden="true" />
      Zero retention · No training
    </span>
  )
}
