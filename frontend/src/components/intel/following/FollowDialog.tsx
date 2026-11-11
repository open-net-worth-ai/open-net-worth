import { useState } from 'react'
import { Stack, Button, ErrorState, Loading, Modal, Text, TextField } from '../../ui'
import { followPortfolio, type DirectoryEntry, type Follow } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { Monogram } from './Monogram'
import { useDirectory } from './useFollowing'

interface Props {
  onClose: () => void
  onFollowed: (follow: Follow) => void
}

const keyOf = (e: DirectoryEntry) => `${e.kind}:${e.source_ref}`
const MIN_QUERY = 2

interface RowProps {
  entry: DirectoryEntry
  busy: boolean
  locked: boolean
  failure: string | undefined
  onFollow: () => void
}

function ResultRow({ entry, busy, locked, failure, onFollow }: RowProps) {
  return (
    <li className="fol-result" aria-busy={busy || undefined}>
      <div className="fol-result-line">
        <Monogram name={entry.name} />
        <div className="fol-id">
          <div className="fol-sym">{entry.name}</div>
          <div className="fol-sub">{entry.subtitle}</div>
        </div>
        {entry.followed ? (
          <span className="fol-sub fol-followed">{COPY.following}</span>
        ) : (
          <Button size="sm" loading={busy} disabled={locked && !busy} onClick={onFollow}>
            {busy ? COPY.followBusy : failure ? COPY.tryAgain : COPY.followAction}
          </Button>
        )}
      </div>
      {busy && (
        <Text size="sm" tone="muted" as="div">
          <span role="status">{COPY.fetching}</span>
        </Text>
      )}
      {failure && !busy && (
        <div role="alert" className="fol-note fol-note--bad">
          {failure}
        </div>
      )}
    </li>
  )
}

export function FollowDialog({ onClose, onFollowed }: Props) {
  const [typed, setTyped] = useState('')
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [failures, setFailures] = useState<Record<string, string>>({})
  const directory = useDirectory(typed)
  const results = directory.data
  const searching = directory.q.length >= MIN_QUERY

  async function follow(entry: DirectoryEntry) {
    const key = keyOf(entry)
    setBusyKey(key)
    setFailures((f) => Object.fromEntries(Object.entries(f).filter(([k]) => k !== key)))
    try {
      onFollowed((await followPortfolio(entry.kind, entry.source_ref)).follow)
    } catch (e) {
      const why = e instanceof Error ? e.message : ''
      setFailures((f) => ({ ...f, [key]: COPY.followFailed(entry.name, why) }))
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <Modal open onClose={onClose} title={COPY.follow} subtitle={COPY.dialogIntro} size="md">
      <Stack gap="--space-5">
        <TextField
          type="search"
          autoFocus
          aria-label={COPY.searchLabel}
          placeholder={COPY.gallerySearch}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
        />
        <div className="fol-head-label">
          {searching ? COPY.results(directory.q) : COPY.featured}
        </div>
        {directory.initialLoading && <Loading label={COPY.searching} />}
        {!results && directory.error != null && (
          <ErrorState
            title={COPY.searchErrorTitle}
            message={COPY.searchErrorBody}
            onRetry={() => void directory.refresh()}
          />
        )}
        {results && results.length === 0 && <Text tone="muted">{COPY.noMatch(directory.q)}</Text>}
        {results && results.length > 0 && (
          <ul className="fol-results">
            {results.map((entry) => (
              <ResultRow
                key={keyOf(entry)}
                entry={entry}
                busy={busyKey === keyOf(entry)}
                locked={busyKey !== null}
                failure={failures[keyOf(entry)]}
                onFollow={() => void follow(entry)}
              />
            ))}
          </ul>
        )}
      </Stack>
    </Modal>
  )
}
