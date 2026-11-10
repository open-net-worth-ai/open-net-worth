import { useState } from 'react'
import { Button, ErrorState, Loading, TextField } from '../../ui'
import { followPortfolio, type DirectoryEntry, type Follow } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { Monogram } from './Monogram'
import { useDirectory } from './useFollowing'

const keyOf = (e: DirectoryEntry) => `${e.kind}:${e.source_ref}`

interface Props {
  onFollowed: (follow: Follow) => void
}

/** The first screen: well-known portfolios to pick from, not an empty page with one button. */
export function Gallery({ onFollowed }: Props) {
  const [typed, setTyped] = useState('')
  const directory = useDirectory(typed)
  const searching = directory.q.length >= 2
  const [busy, setBusy] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  async function follow(entry: DirectoryEntry) {
    setBusy(keyOf(entry))
    setFailure(null)
    try {
      onFollowed((await followPortfolio(entry.kind, entry.source_ref)).follow)
    } catch (e) {
      setFailure(COPY.followFailed(entry.name, e instanceof Error ? e.message : ''))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="fol-sec" aria-label={COPY.galleryTitle}>
      <div className="fol-gallery-head">
        <h2 className="fol-name">{COPY.galleryTitle}</h2>
        <p className="fol-by">{COPY.gallerySub}</p>
        <TextField
          type="search"
          aria-label={COPY.searchLabel}
          placeholder={COPY.gallerySearch}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
        />
      </div>
      {failure && (
        <div role="alert" className="fol-note fol-note--bad">
          {failure}
        </div>
      )}
      {directory.initialLoading && <Loading label={COPY.searching} />}
      {!directory.data && directory.error != null && (
        <ErrorState
          title={COPY.searchErrorTitle}
          message={COPY.searchErrorBody}
          onRetry={() => void directory.refresh()}
        />
      )}
      {directory.data && directory.data.length === 0 && searching && (
        <p className="fol-by">{COPY.noMatch(directory.q)}</p>
      )}
      <ul className="fol-gallery">
        {(directory.data ?? []).map((entry) => (
          <li
            key={keyOf(entry)}
            className="fol-tile"
            aria-busy={busy === keyOf(entry) || undefined}
          >
            <Monogram name={entry.name} />
            <div className="fol-id">
              <div className="fol-sym">{entry.name}</div>
              <div className="fol-sub">{entry.subtitle}</div>
            </div>
            {entry.followed ? (
              <span className="fol-sub">{COPY.following}</span>
            ) : (
              <Button
                size="sm"
                loading={busy === keyOf(entry)}
                disabled={busy !== null && busy !== keyOf(entry)}
                onClick={() => void follow(entry)}
              >
                {busy === keyOf(entry) ? COPY.followBusy : COPY.followAction}
              </Button>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
