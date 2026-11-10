import { useEffect, useState } from 'react'
import { Button, Stack, Text } from '../../ui'
import { RestApiEditor } from './RestApiEditor'
import { draftOf, emptyDraft, getRestApi, restSlugOf } from './restApi'
import type { RestApi, RestApiDraft } from './restApi'

// REST connector config over /api/rest-connector/* (per-provider plane). Each API is its own
// connector, so this form is one of two things, decided by the connector it is opened on:
//
//   - the catalog's "REST API" entry (`rest`) — the form for a NEW API. Saving creates a
//     connector of its own (`rest:<slug>`), which then has its own page, Pause and Remove;
//   - an API the user added (`rest:<slug>`) — that API's saved settings, to edit.
//
// Calling an API is Lucius's job, through that API's own three tools; its connector's Tool
// permissions decide what runs without asking. Save lives in the modal footer — ./formSave.

function AddedNotice({ api, onAddAnother }: { api: RestApi; onAddAnother: () => void }) {
  const count = api.endpoints.length
  return (
    <Stack gap="--space-4">
      <Text size="sm">
        <strong>{api.name}</strong> is added and switched on, with {count} endpoint
        {count === 1 ? '' : 's'} listed. It has its own entry in your connectors, where you can edit
        it, pause it, and choose which of its tools run without asking.
      </Text>
      <Text size="sm" tone="faint">
        Try it: ask Lucius what {api.name} can do.
      </Text>
      <div>
        <Button size="sm" onClick={onAddAnother}>
          Add another API
        </Button>
      </div>
    </Stack>
  )
}

interface NewRestApiProps {
  /** Take the owner to the connector they just made. Without it (nothing to navigate) the
   *  form gives way to a notice that says where the API went. */
  onCreated?: (connectorId: string, name: string) => void
  onSaveError?: (message: string) => void
}

function NewRestApi({ onCreated, onSaveError }: NewRestApiProps) {
  const [added, setAdded] = useState<RestApi | null>(null)
  const [draft, setDraft] = useState<RestApiDraft>(emptyDraft)
  // A fresh key per form, so "Add another" starts from an empty editor, not the last one.
  const [round, setRound] = useState(0)

  if (added) {
    return (
      <AddedNotice
        api={added}
        onAddAnother={() => {
          setDraft(emptyDraft())
          setRound((n) => n + 1)
          setAdded(null)
        }}
      />
    )
  }
  // Saved: the API is a connector of its own now, so its own page is where to be — with its
  // tools and their permissions. Staying here left the owner on this entry's page, which
  // always reads "Not connected" and looked as if the save had not taken (owner, 2026-11-11).
  return (
    <RestApiEditor
      key={round}
      initial={draft}
      onSaved={(api) => (onCreated ? onCreated(api.connector_id, api.name) : setAdded(api))}
      onSaveError={onSaveError}
    />
  )
}

interface SavedRestApiProps {
  slug: string
  onSaveError?: (message: string) => void
}

function SavedRestApi({ slug, onSaveError }: SavedRestApiProps) {
  const [draft, setDraft] = useState<RestApiDraft | null>(null)
  const [failed, setFailed] = useState(false)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    let live = true
    getRestApi(slug)
      .then((api) => {
        if (!live) return
        setFailed(false)
        setDraft(draftOf(api))
        setVersion((n) => n + 1)
      })
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [slug])

  if (failed) {
    return (
      <Text size="sm" tone="bad">
        Could not load this API&apos;s settings.
      </Text>
    )
  }
  if (!draft) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }
  // Re-keyed on every load and save: what was just saved becomes the baseline the footer's
  // Save compares against, and the key field goes back to blank ("keep the saved one").
  return (
    <RestApiEditor
      key={version}
      initial={draft}
      onSaved={(api) => {
        setDraft(draftOf(api))
        setVersion((n) => n + 1)
      }}
      onSaveError={onSaveError}
    />
  )
}

export interface RestSectionProps {
  connectorId: string
  onCreated?: (connectorId: string, name: string) => void
  onSaveError?: (message: string) => void
}

export function RestSection({ connectorId, onCreated, onSaveError }: RestSectionProps) {
  const slug = restSlugOf(connectorId)
  return slug ? (
    <SavedRestApi slug={slug} onSaveError={onSaveError} />
  ) : (
    <NewRestApi onCreated={onCreated} onSaveError={onSaveError} />
  )
}
