// Opening an attachment from the asset page (owner 2026-09-28: "nothing happens when I tap the
// row … we should show preview for the file too!"). The file is the same item Documents lists
// (`asset-<ref>`), so it opens with Documents' own viewers: an image as itself, a PDF page by
// page, a spreadsheet as a table — anything else as a card to download.
import type { VaultItem } from '../../../api/vault'
import type { AssetDoc, OwnedAsset } from '../../../types/assets'
import { Button, Modal } from '../../ui'
import { DocumentPages } from '../../vault/DocumentPages'
import { NoPreview } from '../../vault/NoPreview'
import { PrivateContent } from '../../vault/PrivateContent'
import { TablePreview } from '../../vault/TablePreview'
import { formatSize, isPdf, isTable } from '../../vault/vaultFormat'
import '../../vault/vault.css'
import { monthYear } from './assetsData'

export const docFileUrl = (doc: AssetDoc) => `/api/uploads/${encodeURIComponent(doc.ref_id)}/file`

export const isImageDoc = (doc: AssetDoc) => (doc.media_type ?? '').startsWith('image/')

/** The attachment as Documents lists it — the same file, the same viewers. */
function asVaultItem(a: OwnedAsset, doc: AssetDoc): VaultItem {
  return {
    id: `asset-${doc.ref_id}`,
    kind: 'asset',
    source_id: doc.ref_id,
    title: doc.name,
    filename: doc.filename || doc.name,
    media_type: doc.media_type ?? null,
    size_bytes: doc.size_bytes ?? null,
    added_at: doc.added_on,
    year: null,
    // "Lucius can't read" IS Private: the same guard, here as in every model read.
    private: !doc.lucius_readable,
    file_url: docFileUrl(doc),
    asset_id: a.id,
    asset_name: a.name,
  }
}

function Preview({ a, doc }: { a: OwnedAsset; doc: AssetDoc }) {
  const item = asVaultItem(a, doc)
  if (isImageDoc(doc)) return <img className="as-viewer-img" src={docFileUrl(doc)} alt={doc.name} />
  if (isPdf(item)) return <DocumentPages itemId={item.id} title={doc.name} />
  if (isTable(item)) return <TablePreview item={item} />
  return <NoPreview item={item} />
}

function download(doc: AssetDoc) {
  const link = document.createElement('a')
  link.href = docFileUrl(doc)
  link.download = doc.filename || doc.name
  document.body.appendChild(link)
  link.click()
  link.remove()
}

export interface AttachmentViewerProps {
  a: OwnedAsset
  doc: AssetDoc
  onClose(): void
}

export function AttachmentViewer({ a, doc, onClose }: AttachmentViewerProps) {
  // Its own file name only when a rename hides it, then its size and when it was added.
  const facts = [
    doc.filename && doc.filename !== doc.name ? doc.filename : null,
    formatSize(doc.size_bytes),
    `Added ${monthYear(doc.added_on)}`,
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <Modal
      className="as-modal"
      open
      onClose={onClose}
      title={doc.name}
      subtitle={facts}
      size="lg"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
          <Button variant="secondary" size="sm" onClick={() => download(doc)}>
            Download
          </Button>
        </>
      }
    >
      <div className="as-viewer">
        <PrivateContent hidden={!doc.lucius_readable}>
          <Preview a={a} doc={doc} />
        </PrivateContent>
      </div>
    </Modal>
  )
}
