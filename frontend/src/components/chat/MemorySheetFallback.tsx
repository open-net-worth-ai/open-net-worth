import { createPortal } from 'react-dom'

/** Shown while the lazily loaded memory sheet's chunk arrives, so a click on Memory never looks
 *  like it did nothing. Inline styles: the sheet's own stylesheet is part of the chunk. */
export function MemorySheetFallback() {
  return createPortal(
    <div
      role="status"
      aria-live="polite"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0, 0, 0, 0.35)',
        color: 'var(--ink, inherit)',
        fontSize: 'var(--text-base)',
      }}
    >
      Opening memory…
    </div>,
    document.body,
  )
}
