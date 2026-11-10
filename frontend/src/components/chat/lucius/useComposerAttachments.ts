// The composer's staged files: each uploads to /api/upload the moment it is added, and the
// server's ref_id is what a send forwards. This is the whole lifecycle of a chip, kept out of
// the composer so "can this be sent yet, and if not, why" has one answer.

import { useCallback, useState } from 'react'
import { authFetch } from '../../../api/client'
import type { Attachment } from '../../../types/chat'

export const ATTACH_ACCEPT = 'image/*,.pdf,.csv,.txt,.xlsx,.docx'

export interface AttachmentItem {
  id: string
  name: string
  size: number
  type: string
  kind: 'image' | 'file'
  url: string | null
  dim: string | null
  /** True until the /api/upload request resolves; the file isn't sendable yet. */
  uploading?: boolean
  /** Server-assigned reference once uploaded — what the backend actually reads. */
  refId?: string
  /** Server-derived summary (text extract / image caption). */
  summary?: string
  /** Server-classified kind ('image' | 'text'). */
  uploadKind?: 'image' | 'text'
  /** True if the upload failed; the chip stays so the reader can see which one. */
  failed?: boolean
}

type UploadReply = { ref_id?: string; kind?: 'image' | 'text'; summary?: string }

/** Map staged items to the ref_id-bearing Attachment[] a send needs. */
export function toSendAttachments(items: AttachmentItem[]): Attachment[] {
  return items
    .filter((a) => a.refId)
    .map((a) => ({
      ref_id: a.refId as string,
      kind: a.uploadKind ?? (a.kind === 'image' ? 'image' : 'text'),
      summary: a.summary ?? a.name,
      localUrl: a.url ?? undefined,
    }))
}

export function useComposerAttachments() {
  const [attachments, setAttachments] = useState<AttachmentItem[]>([])

  const patch = useCallback((id: string, change: Partial<AttachmentItem>) => {
    // Mapping over what is THERE: a file whose upload lands after it was removed or the
    // composer was cleared must not come back.
    setAttachments((a) => a.map((x) => (x.id === id ? { ...x, ...change } : x)))
  }, [])

  const addFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList) return
      const files = [...fileList]
      const stamp = Date.now()
      const next: AttachmentItem[] = files.map((f, i) => {
        const isImg = /^image\//.test(f.type)
        return {
          id: `att-${stamp}-${i}`,
          name: f.name,
          size: f.size,
          type: f.type || 'file',
          kind: isImg ? 'image' : 'file',
          url: isImg ? URL.createObjectURL(f) : null,
          dim: null,
          uploading: true,
        }
      })
      setAttachments((a) => [...a, ...next])
      next.forEach((att, i) => {
        // Read image pixel dimensions for the chip meta.
        if (att.kind === 'image' && att.url) {
          const img = new Image()
          img.onload = () => patch(att.id, { dim: `${img.naturalWidth}×${img.naturalHeight}` })
          img.src = att.url
        }
        const file = files[i]
        if (!file) return
        const form = new FormData()
        form.append('file', file)
        authFetch('/api/upload', { method: 'POST', body: form })
          .then((res) => (res.ok ? (res.json() as Promise<UploadReply>) : null))
          .then((data) =>
            data?.ref_id
              ? patch(att.id, {
                  uploading: false,
                  refId: data.ref_id,
                  summary: data.summary,
                  uploadKind: data.kind,
                })
              : patch(att.id, { uploading: false, failed: true }),
          )
          .catch(() => patch(att.id, { uploading: false, failed: true }))
      })
    },
    [patch],
  )

  const remove = useCallback(
    (id: string) => setAttachments((a) => a.filter((x) => x.id !== id)),
    [],
  )
  const clear = useCallback(() => setAttachments([]), [])

  return {
    attachments,
    addFiles,
    remove,
    clear,
    uploading: attachments.filter((a) => a.uploading),
    failed: attachments.filter((a) => a.failed),
  }
}
