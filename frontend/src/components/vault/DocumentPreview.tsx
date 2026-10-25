import { useState } from 'react'
import type { VaultItem } from '../../api/vault'
import { Modal } from '../ui/Modal'
import { ArtifactPreview } from './ArtifactPreview'
import { DocumentPages } from './DocumentPages'
import { DocumentReader } from './DocumentReader'
import { ImagePreview } from './ImagePreview'
import { NoPreview } from './NoPreview'
import { TablePreview } from './TablePreview'
import { TextPreview } from './TextPreview'
import { isImage, isPdf, isTable, isText } from './vaultFormat'

function PdfPreview({ item }: { item: VaultItem }) {
  const [reading, setReading] = useState(false)
  return (
    <>
      <DocumentPages
        key={item.id}
        itemId={item.id}
        title={item.title}
        onExpand={() => setReading(true)}
      />
      {reading && (
        <Modal open onClose={() => setReading(false)} title={item.title} size="xl">
          <div className="vault-modal-content" tabIndex={-1} data-autofocus>
            <DocumentReader itemId={item.id} title={item.title} />
          </div>
        </Modal>
      )}
    </>
  )
}

/** The ONE file preview — the Documents tab, the account's Statements viewer and the import
 *  review's file pane all show a document through this, so a CSV is a spreadsheet grid and a
 *  PDF is its pages everywhere (owner, 2026-10-26: "even the statement viewer is not
 *  supporting full files like we have in the Documents tab"). `highlightRow` points the grid
 *  at one row of a CSV/Excel file; other kinds ignore it. */
export function DocumentPreview({
  item,
  onOpenArtifact,
  highlightRow,
}: {
  item: VaultItem
  onOpenArtifact?: () => void
  highlightRow?: number
}) {
  if (isPdf(item)) return <PdfPreview item={item} />
  if (isTable(item)) return <TablePreview key={item.id} item={item} highlightRow={highlightRow} />
  if (item.kind === 'artifact' && onOpenArtifact)
    return <ArtifactPreview item={item} onOpen={onOpenArtifact} />
  if (isImage(item) && item.file_url) return <ImagePreview item={item} src={item.file_url} />
  if (isText(item) && item.file_url) return <TextPreview key={item.id} item={item} />
  return <NoPreview item={item} />
}
