// A REST API's id, in its form: shown under the Name field while the name is typed, and typed
// directly when the owner asks. It names the API's tools (`rest__<id>__…`) and the permissions
// set for them, so it is chosen once, before the first save, and never changes.

import { RestField } from './restFormParts'
import { cleanRestId, restIdFromName } from './restApi'
import type { RestApiDraft } from './restApi'

const ID_RULE =
  'It names this API’s tools and their permissions, and can’t be changed after you save.'

interface IdHelpProps {
  draft: RestApiDraft
  onCustomize: () => void
}

/** The API's id, under its name: it is made from the name on the first save and never changes,
 *  so it is shown while the name is still being typed and the choice is still open. */
export function IdHelp({ draft, onCustomize }: IdHelpProps) {
  if (draft.id) {
    return (
      <>
        ID: <code className="rest-path">{draft.id}</code>. Renaming the API does not change it.
      </>
    )
  }
  const id = restIdFromName(draft.name)
  return (
    <>
      {id ? (
        <>
          ID: <code className="rest-path">{id}</code>.{' '}
        </>
      ) : (
        'The ID is made from the name. '
      )}
      {ID_RULE}{' '}
      <button type="button" className="rest-id-link" onClick={onCustomize}>
        Change the ID
      </button>
    </>
  )
}

interface IdFieldProps {
  value: string
  onChange: (next: string) => void
  onUseName: () => void
}

/** The id, typed by the owner instead of taken from the name. Offered only before the first
 *  save: it is their one chance to choose it. */
export function IdField({ value, onChange, onUseName }: IdFieldProps) {
  return (
    <RestField
      label="ID"
      htmlFor="rest-id"
      required
      help={
        <>
          Lowercase letters, digits and underscores, up to 20. {ID_RULE}{' '}
          <button type="button" className="rest-id-link" onClick={onUseName}>
            Use the name instead
          </button>
        </>
      }
    >
      <input
        id="rest-id"
        className="rd-input rest-path"
        placeholder="my_bank"
        value={value}
        autoFocus
        spellCheck={false}
        autoCapitalize="none"
        onChange={(e) => onChange(cleanRestId(e.target.value))}
      />
    </RestField>
  )
}
