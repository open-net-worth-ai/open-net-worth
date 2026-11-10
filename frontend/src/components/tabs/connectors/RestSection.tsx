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

function NewRestApi() {
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
  return <RestApiEditor key={round} initial={draft} onSaved={setAdded} />
}

function SavedRestApi({ slug }: { slug: string }) {
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
    />
  )
}

export function RestSection({ connectorId }: { connectorId: string }) {
  const slug = restSlugOf(connectorId)
  return slug ? <SavedRestApi slug={slug} /> : <NewRestApi />
}
