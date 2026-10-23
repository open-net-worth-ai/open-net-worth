// ReceiptViewer — in-app viewer for one transaction receipt, modeled directly on
// StatementViewer (StatementsSection.tsx): same Modal shell, same download-footer pattern,
// same fetch-then-render state machine (shared via useFileViewerState/FileViewerBody rather
// than forked) — the one addition is the 'image' branch, since a receipt (unlike a
// statement) is very often a photographed paper receipt.
import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { FileViewerBody, useFileViewerState } from './StatementsSection'
import { Download } from '../../../ui/icons'

export interface ReceiptViewerTarget {
  id: string
  filename: string
  mediaType: string | null
}

export function ReceiptViewer({
  receipt,
  onClose,
}: {
  receipt: ReceiptViewerTarget
  onClose: () => void
}) {
  const ext = (receipt.filename || '').split('.').pop()?.toLowerCase() || ''
  const isImage = !!receipt.mediaType && receipt.mediaType.startsWith('image/')
  const state = useFileViewerState(`/api/receipts/${receipt.id}/file`, ext, isImage)

  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      title={receipt.filename || 'Receipt'}
      footer={
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            const a = document.createElement('a')
            a.href = `/api/receipts/${receipt.id}/file`
            a.download = receipt.filename || 'receipt'
            a.click()
          }}
        >
          <Download size={14} aria-hidden="true" /> Download original
        </Button>
      }
    >
      <FileViewerBody
        state={state}
        filename={receipt.filename}
        pageItemId={`receipt-${receipt.id}`}
      />
    </Modal>
  )
}
