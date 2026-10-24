import { createPortal } from 'react-dom'
import { MemoryDialog } from '../tabs/settings/MemoryDialog'
import { useToast } from '../tabs/settings/ds'
import '../tabs/settings/settings.css'

/** The chat header's Memory button opens Settings' own stored-memories dialog — one
 *  implementation, one look. Its classes are scoped under `.onw-settings`, so this supplies that
 *  scope (and the toast Settings would) outside the Settings shell.
 *
 *  Portalled to <body>: opened from the phone's Lucius tab it sat inside the app column's
 *  stacking context, and the bottom tab bar painted over the sheet's footer. */
export function MemorySheet({ onClose }: { onClose: () => void }) {
  const [toast, toastNode] = useToast()
  return createPortal(
    <div className="onw-settings">
      <MemoryDialog onClose={onClose} toast={toast} />
      {toastNode}
    </div>,
    document.body,
  )
}
