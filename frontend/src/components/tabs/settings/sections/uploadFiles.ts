// Pure helpers for Settings → Data → Uploaded files (kept out of the component file for fast refresh).

export interface UploadRow {
  id: string
  filename: string
  size_bytes: number
  created_at: string
  kind?: string
  media_type?: string | null
}

export function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

export function isPreviewableImage(u: UploadRow): boolean {
  const mime = (u.media_type || '').toLowerCase()
  if (mime === 'image/svg+xml') return false
  return u.kind === 'image' || mime.startsWith('image/')
}
