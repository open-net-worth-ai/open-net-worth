import './following.css'

import { useState } from 'react'
import { Button, EmptyState, ErrorState, TabSkeleton } from '../../ui'
import { Plus } from '../../../ui/icons'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { COPY } from './following.copy'
import { FollowDialog } from './FollowDialog'
import { FollowView } from './FollowView'
import { useFollowList } from './useFollowing'

interface Props {
  onAskLucius?: (text: string) => void
}

export function FollowingTab({ onAskLucius }: Props) {
  const list = useFollowList()
  const isMobile = useIsMobile()
  const [pickedId, setPickedId] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const follows = list.data?.follows ?? []
  const selected = follows.find((f) => f.id === pickedId) ?? follows[0]

  if (list.initialLoading)
    return <TabSkeleton variant="holdings" label={COPY.loadingList} header={false} />
  if (!list.data)
    return (
      <ErrorState
        title={COPY.listErrorTitle}
        message={COPY.listErrorBody}
        onRetry={() => void list.refresh()}
      />
    )

  const dialog = dialogOpen && (
    <FollowDialog
      onClose={() => setDialogOpen(false)}
      onFollowed={(follow) => {
        setPickedId(follow.id)
        setDialogOpen(false)
        void list.refresh()
      }}
    />
  )
  const followButton = (
    <Button
      variant="primary"
      size="sm"
      iconLeft={<Plus size={14} />}
      onClick={() => setDialogOpen(true)}
    >
      {isMobile && selected ? COPY.followShort : COPY.follow}
    </Button>
  )

  if (!selected)
    return (
      <>
        <EmptyState title={COPY.emptyTitle} body={COPY.emptyBody} action={followButton} />
        {dialog}
      </>
    )

  return (
    <div className="fol-page">
      <div className="fol-bar">
        <div className="fol-chips" role="tablist" aria-label={COPY.listTitle}>
          {follows.map((f) => (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={f.id === selected.id}
              className={`fol-chip${f.id === selected.id ? ' is-on' : ''}`}
              onClick={() => setPickedId(f.id)}
            >
              {f.name}
              <span className="fol-chip-n">{f.model?.holdings.length ?? 0}</span>
            </button>
          ))}
        </div>
        {followButton}
      </div>
      <FollowView
        key={selected.id}
        follow={selected}
        onChanged={list.refresh}
        onUnfollowed={() => setPickedId(null)}
        onAskLucius={onAskLucius}
      />
      {dialog}
    </div>
  )
}
