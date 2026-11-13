// One row of Settings → Data → Uploaded files. An image shows its own thumbnail and opens a
// preview, so a file named `telegram_photo_<n>.jpg` can be told apart from its neighbours
// before it is deleted — a name alone never said which photo held what.
import { useState } from 'react'
import { fDate } from '../../../../utils/format'
import { FileText } from '../../../../ui/icons'
import { Btn, Modal } from '../ds'
import { fmtSize, isPreviewableImage } from './uploadFiles'
import type { UploadRow } from './uploadFiles'

function imageSrc(u: UploadRow): string {
  return `/api/uploads/${encodeURIComponent(u.id)}/image`
}

export function UploadedFileRow({
  upload: u,
  onDelete,
}: {
  upload: UploadRow
  onDelete: (u: UploadRow) => void
}) {
  const [broken, setBroken] = useState(false)
  const [open, setOpen] = useState(false)
  const showThumb = isPreviewableImage(u) && !broken

  return (
    <div className="rd-row">
      {showThumb ? (
        <button
          type="button"
          className="rd-row-ic rd-row-thumb"
          onClick={() => setOpen(true)}
          aria-label={`Preview ${u.filename}`}
        >
          <img src={imageSrc(u)} alt="" loading="lazy" onError={() => setBroken(true)} />
        </button>
      ) : (
        <span className="rd-row-ic">
          <FileText size={14} strokeWidth={1.7} aria-hidden="true" />
        </span>
      )}
      <div className="rd-row-main">
        <div className="rd-row-title">{u.filename}</div>
        <div className="rd-row-meta">
          <span>{fmtSize(u.size_bytes)}</span>
          <span>{fDate(u.created_at, 'short', 'always')}</span>
        </div>
      </div>
      <div className="rd-row-actions">
        <Btn size="xs" variant="ghost" onClick={() => onDelete(u)}>
          Delete
        </Btn>
      </div>
      {open && (
        <Modal
          title={u.filename}
          onClose={() => setOpen(false)}
          footer={
            <>
              <Btn onClick={() => setOpen(false)}>Close</Btn>
              <Btn
                variant="danger"
                onClick={() => {
                  setOpen(false)
                  onDelete(u)
                }}
              >
                Delete
              </Btn>
            </>
          }
        >
          <img className="rd-upload-preview" src={imageSrc(u)} alt={u.filename} />
        </Modal>
      )}
    </div>
  )
}
