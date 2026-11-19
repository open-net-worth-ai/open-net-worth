// @vitest-environment jsdom
//
// The REST API form is long and its Save is in the dialog's footer. A failed save reports its
// reason for the screen's toast, the component this screen already uses for failures — the
// owner pressed Save on a name already taken and saw nothing, with the form's own message a
// screen below (2026-11-11). The message stays in the form too, to read after the toast goes.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../hooks/useFlag'
import { useConnectorSaveHost } from './formSave'
import { RestApiEditor } from './RestApiEditor'
import { emptyDraft } from './restApi'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(cleanup)

/** The dialog's side of the contract: a Save that runs the form's save. */
function Dialog({ onSaveError }: { onSaveError: (message: string) => void }) {
  const host = useConnectorSaveHost()
  return (
    <>
      <host.Provider>
        <RestApiEditor initial={emptyDraft()} onSaved={vi.fn()} onSaveError={onSaveError} />
      </host.Provider>
      <button onClick={() => void host.run()}>footer save</button>
    </>
  )
}

describe('a failed save of the REST API form', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ permissions_v2: false })))
  afterEach(() => restore())

  it('reports its reason for the toast, and keeps it in the form', async () => {
    const reason = "There is already a REST API called 'Acme Bank'."
    authFetch.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: reason }) })
    const onSaveError = vi.fn()
    render(<Dialog onSaveError={onSaveError} />)
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { value: 'Acme Bank' } })
    fireEvent.change(screen.getByLabelText(/^Base URL/), {
      target: { value: 'https://api.acme.test/v1' },
    })

    await act(async () => {
      fireEvent.click(screen.getByText('footer save'))
    })
    expect(onSaveError).toHaveBeenCalledWith(reason)
    expect(screen.getByRole('alert').textContent).toBe(reason)
  })

  it('says nothing for what is merely not filled in yet: the field is marked required', () => {
    // A line about the Name field at the foot of a long form read as out of place.
    const onSaveError = vi.fn()
    render(<Dialog onSaveError={onSaveError} />)
    expect(screen.queryByText('Give the API a name.')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(onSaveError).not.toHaveBeenCalled()
  })
})
