import { useEffect, useState } from 'react'
import type { ArtifactRef } from '../types/chat'
import { fetchArtifactContent, fetchArtifactFile } from '../api/artifacts'
import { isBinaryType, isMarkupType } from './artifactFormats'

// Shared fetch+prepare logic for an artifact's rendered document — used by both the inline
// chat card and the side/fullscreen viewer, so the two never drift on how content is fetched,
// retried, or sandboxed. A pdf/xlsx artifact has no raw text route (415) — it is never fetched
// here; `raw` stays null and the viewer shows its file panel instead.
/** Same-origin URL of the prepared document for a sandboxed `<iframe src>`. */
const artifactFrameUrl = (id: string) => `/api/artifacts/${encodeURIComponent(id)}/frame`

export function useArtifactContent(artifactId: string, artifactType: ArtifactRef['artifactType']) {
  const [raw, setRaw] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

  const fetchable = !isBinaryType(artifactType)

  useEffect(() => {
    let cancelled = false
    setRaw(null)
    setFailed(false)
    if (!fetchable) return
    fetchArtifactContent(artifactId)
      .then((text) => {
        if (!cancelled) setRaw(text)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [artifactId, attempt, fetchable])

  // Loaded once the document is known to exist; the frame itself is served prepared (CSP +
  // height probe) by the backend, so it keeps its own CSP instead of inheriting the page's.
  const frameSrc = raw === null || !isMarkupType(artifactType) ? null : artifactFrameUrl(artifactId)

  return { raw, frameSrc, failed, retry: () => setAttempt((n) => n + 1) }
}

/** Save an already-fetched artifact's raw text to the user's disk. Pure — no state, so it's
 * usable from a one-shot click handler as easily as from ArtifactViewer's own render. */
export function downloadArtifactContent(raw: string, artifact: ArtifactRef): void {
  const mime = artifact.artifactType === 'svg' ? 'image/svg+xml' : 'text/html'
  const objectUrl = URL.createObjectURL(new Blob([raw], { type: mime }))
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = artifact.filename || 'artifact.html'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}

/** Save a blob under `filename`. The object URL is revoked later, not at once, so the browser has
 * started the download first. */
export function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}

/** Fetch-then-download in one shot — for a caller (the chip's own button) that has no
 * standing useArtifactContent() subscription of its own. html/svg save their source text;
 * pdf/xlsx/csv come from the download route, saved under the server's filename. */
export async function downloadArtifact(artifact: ArtifactRef): Promise<void> {
  if (isMarkupType(artifact.artifactType)) {
    const raw = await fetchArtifactContent(artifact.id)
    downloadArtifactContent(raw, artifact)
    return
  }
  const { blob, filename } = await fetchArtifactFile(artifact.id)
  saveBlob(blob, filename || artifact.filename || `artifact.${artifact.artifactType}`)
}

/** Open a PDF artifact in a new tab. The tab is opened synchronously (inside the click, so a
 * popup blocker allows it) and pointed at the blob once the authenticated fetch lands. */
function readBytes(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as ArrayBuffer)
    reader.onerror = () => reject(reader.error)
    reader.readAsArrayBuffer(blob)
  })
}

export async function openArtifactPdf(artifact: ArtifactRef): Promise<void> {
  const tab = window.open('', '_blank')
  try {
    const { blob } = await fetchArtifactFile(artifact.id)
    // The blob: URL opens with the APP's origin (the server's CSP: sandbox doesn't follow it),
    // so only a real PDF may open — checked by type AND by its %PDF- signature — and the
    // blob is re-typed so nothing else can ever render as a page here.
    const head = new Uint8Array(await readBytes(blob.slice(0, 5)))
    const isPdf =
      blob.type.split(';')[0]?.trim() === 'application/pdf' &&
      String.fromCharCode(...head) === '%PDF-'
    if (!isPdf) throw new Error('This artifact is not a PDF.')
    const objectUrl = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
    if (tab) tab.location.href = objectUrl
    else window.open(objectUrl, '_blank')
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  } catch (err) {
    tab?.close()
    throw err
  }
}
