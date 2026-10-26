import { useEffect, useState } from 'react'
import { authFetch } from '../../api/client'
import type { VaultItem } from '../../api/vault'
import { NoPreview } from './NoPreview'
import { PreviewLoading } from './PreviewLoading'
import { formatSize } from './vaultFormat'

// A text file shown as itself — a note, a bill of sale, a bank's OFX/QIF export (owner: "why txt
// file can't be previewed?"). The same preview-sized frame as a PDF's or a sheet's, scrolling
// inside; the text is React text, never markup. A huge file shows its start.

const LIMIT = 256 * 1024

type Shown = { text: string; cut: boolean } | 'loading' | 'error'

/** UTF-8 unless a byte-order mark says UTF-16 (Windows Notepad's "Unicode"). */
function decode(bytes: Uint8Array): string {
  const encoding =
    bytes[0] === 0xff && bytes[1] === 0xfe
      ? 'utf-16le'
      : bytes[0] === 0xfe && bytes[1] === 0xff
        ? 'utf-16be'
        : 'utf-8'
  return new TextDecoder(encoding).decode(bytes)
}

export function TextPreview({ item }: { item: VaultItem }) {
  const [shown, setShown] = useState<Shown>('loading')
  const url = item.file_url
  useEffect(() => {
    if (!url) return
    let alive = true
    authFetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.arrayBuffer()
      })
      .then((buffer) => {
        if (!alive) return
        const bytes = new Uint8Array(buffer)
        setShown({ text: decode(bytes.subarray(0, LIMIT)), cut: bytes.byteLength > LIMIT })
      })
      .catch(() => alive && setShown('error'))
    return () => {
      alive = false
    }
  }, [url])

  if (shown === 'error')
    return <NoPreview item={item} reason="Couldn't read it here — download it" />
  if (shown === 'loading')
    return (
      <div className="vault-preview vault-preview--text">
        <PreviewLoading />
      </div>
    )
  return (
    <div className="vault-preview vault-preview--text">
      <pre className="vault-text">{shown.text}</pre>
      {shown.cut && (
        <p className="vault-text-cut">
          Showing the first {formatSize(LIMIT)} — download it for the rest.
        </p>
      )}
    </div>
  )
}
