import { useRef, useState, type DragEvent } from 'react'

function carriesFiles(e: DragEvent): boolean {
  return [...(e.dataTransfer?.types ?? [])].includes('Files')
}

/** One file drop target: `over` drives the mask, `handlers` spread onto the target element.
 *  Disabled, every handler is a no-op so the drag bubbles to an outer zone instead. */
export function useFileDropZone(onFiles: (files: FileList) => void, enabled = true) {
  const [over, setOver] = useState(false)
  // Crossing into a child fires dragenter on it before dragleave on the parent, so counting
  // enters against leaves says when the drag has really left (relatedTarget is null in Safari).
  const depth = useRef(0)
  const reset = () => {
    depth.current = 0
    setOver(false)
  }
  const handlers = {
    onDragEnter(e: DragEvent<HTMLElement>) {
      if (!enabled || !carriesFiles(e)) return
      depth.current += 1
      setOver(true)
    },
    onDragOver(e: DragEvent<HTMLElement>) {
      if (!enabled || !carriesFiles(e)) return
      e.preventDefault()
      setOver(true)
    },
    onDragLeave(e: DragEvent<HTMLElement>) {
      if (!enabled || !carriesFiles(e)) return
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) setOver(false)
    },
    onDrop(e: DragEvent<HTMLElement>) {
      if (!enabled) return
      if (e.dataTransfer?.files?.length) {
        e.preventDefault()
        e.stopPropagation()
        onFiles(e.dataTransfer.files)
      }
      reset()
    },
  }
  return { over: enabled && over, handlers }
}
