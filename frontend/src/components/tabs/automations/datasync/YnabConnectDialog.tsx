import { useNavigate } from '@tanstack/react-router'
import { Button, Modal } from '../../../ui'
import { useConnectorSaveHost } from '../../connectors/formSave'
import { YnabSection } from '../../connectors/YnabSection'

// The YNAB connection form, opened right on the sync page: the same form the Connectors page
// shows (token, and the plan picker once a token sees several), so setting up or changing the
// token never sends the user away from the step they are on. Pausing, removing and the account
// list stay on the Connectors page, one link away.

export function YnabConnectDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const navigate = useNavigate()
  const save = useConnectorSaveHost()
  const state = save.state

  async function submit() {
    if (await save.run()) {
      onSaved()
      onClose()
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="YNAB connection"
      footer={
        <>
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              void navigate({ to: '/connectors', search: { connector: 'ynab' } } as never)
            }
          >
            More on the Connectors page
          </Button>
          <Button size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!state?.dirty}
            loading={state?.saving}
            onClick={() => void submit()}
          >
            Save
          </Button>
        </>
      }
    >
      {open && (
        <save.Provider>
          <YnabSection onSyncPage />
        </save.Provider>
      )}
    </Modal>
  )
}
