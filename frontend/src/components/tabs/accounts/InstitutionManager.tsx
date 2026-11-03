import { useState } from 'react'
import { Button, ConfirmDialog, Modal, TextField } from '../../ui'
import type { InstitutionOption } from './ds'

export interface InstitutionDeleteResult {
  id: string
  unlinked_account_count: number
  unlinked_account_ids: string[]
}

interface InstitutionManagerProps {
  open: boolean
  institutions: InstitutionOption[]
  onClose: () => void
  onRename: (id: string, name: string) => Promise<InstitutionOption>
  onDelete: (id: string) => Promise<InstitutionDeleteResult>
  onRenamed?: (previous: InstitutionOption, renamed: InstitutionOption) => void
  onDeleted?: (result: InstitutionDeleteResult) => void
}

// "Checking, Savings" — WHICH accounts a rename or delete touches; the count alone when the
// names aren't known (an older server) or there are none.
function accountsLine(institution: InstitutionOption): string {
  const names = institution.accountNames
  if (names && names.length > 0) return names.join(', ')
  return institution.accountCount === 1 ? '1 account' : `${institution.accountCount} accounts`
}

export function InstitutionManager({
  open,
  institutions,
  onClose,
  onRename,
  onDelete,
  onRenamed,
  onDeleted,
}: InstitutionManagerProps) {
  const [editing, setEditing] = useState<InstitutionOption | null>(null)
  const [name, setName] = useState('')
  const [deleting, setDeleting] = useState<InstitutionOption | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const startRename = (institution: InstitutionOption) => {
    setEditing(institution)
    setName(institution.name)
    setError('')
  }
  const saveRename = async () => {
    if (!editing) return
    setBusy(true)
    setError('')
    try {
      const renamed = await onRename(editing.id, name)
      onRenamed?.(editing, renamed)
      setEditing(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not rename institution')
    } finally {
      setBusy(false)
    }
  }
  const confirmDelete = async () => {
    if (!deleting) return
    setBusy(true)
    setError('')
    try {
      const result = await onDelete(deleting.id)
      onDeleted?.(result)
      setDeleting(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete institution')
      setDeleting(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Modal open={open} onClose={onClose} title="Manage institutions" size="md">
        <div className="acct-inst-manager">
          <p className="acct-inst-manager-copy">
            Rename a shared institution, or remove it from the saved list. Deleting only unlinks
            accounts; it never deletes account data.
          </p>
          {error && (
            <p className="acct-inst-manager-error" role="alert">
              {error}
            </p>
          )}
          {institutions.length === 0 ? (
            <p className="acct-inst-manager-empty">No saved institutions yet.</p>
          ) : (
            <div className="acct-inst-manager-list">
              {institutions.map((institution) => (
                <div className="acct-inst-manager-row" key={institution.id}>
                  {editing?.id === institution.id ? (
                    <div className="acct-inst-manager-edit">
                      <TextField
                        aria-label={`Institution name for ${institution.name}`}
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                        autoFocus
                      />
                      <div className="acct-inst-manager-actions">
                        <Button size="sm" onClick={() => void saveRename()} disabled={busy}>
                          Save
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setEditing(null)}
                          disabled={busy}
                        >
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="acct-inst-manager-main">
                        <div className="acct-inst-manager-name">{institution.name}</div>
                        <div
                          className="acct-inst-manager-count"
                          title={institution.accountNames?.join(', ') || undefined}
                        >
                          {accountsLine(institution)}
                        </div>
                      </div>
                      <div className="acct-inst-manager-actions">
                        <Button size="sm" variant="ghost" onClick={() => startRename(institution)}>
                          Rename
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="acct-inst-manager-delete"
                          onClick={() => setDeleting(institution)}
                        >
                          Delete
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal>
      <ConfirmDialog
        data={
          deleting
            ? {
                title: `Delete ${deleting.name}?`,
                body: `This removes it from ${deleting.accountCount === 1 ? '1 account' : `${deleting.accountCount} accounts`}. The accounts, balances, and transactions stay intact.`,
                confirmLabel: 'Unlink and delete',
                danger: true,
                confirmDisabled: busy,
              }
            : null
        }
        onCancel={() => setDeleting(null)}
        onConfirm={() => void confirmDelete()}
      />
    </>
  )
}
