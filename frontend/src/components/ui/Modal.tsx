import * as Dialog from '@radix-ui/react-dialog'
import { useEffect, useLayoutEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { X } from '../../ui/icons'
import { useIsMobile } from '../../hooks/useIsMobile'
import { useSheetDragToDismiss } from '../../hooks/useSheetDragToDismiss'

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl' | 'workspace'

// Some surfaces are always reachable even with a modal open — a click/pointer-down on them
// registers as "outside" the Dialog.Content to Radix regardless of their own (higher)
// z-index or genuine visual stacking above this modal, so without this a click there would
// dismiss whatever modal happens to be open. Every Lucius surface below is mounted once in
// RouterShell as a sibling of wherever this modal's Dialog.Portal renders — never a DOM
// descendant of Dialog.Content — so every interaction inside any of them is a real "outside"
// click as far as Radix can tell. Exempt surfaces:
//   - .lx-side: ChatPanel's own root — the full Lucius side panel (composer, model/effort
//     picker, conversation history, mention popovers) when expanded.
//   - .lx-dock: LuciusDock's own root — the floating collapsed pill shown when the side
//     panel is closed.
//   - .lucius-mobile-shell: LuciusMobile.tsx's own root — the phone's full-screen Lucius tab
//     (2026-08-13 refactor; ChatPanel's own .lx-side is no longer what renders there). Carries
//     the exact same reachability need .lx-side always had: a `size="workspace"` modal
//     (Settings.tsx) is a 92dvh sheet that deliberately leaves the top ~8% of the viewport
//     showing whatever's behind it, and ANY modal has a real one-render window where this tab
//     is still mounted while routeTab is flipping away from /lucius (RouterShell.tsx) — a tap
//     landing in either window must still reach this tab's own controls, not read as a
//     dismiss-the-modal outside click.
//   - [data-lucius-portal]: content a Lucius component renders via createPortal to
//     document.body instead — e.g. the "Ask Lucius about this" context menu and the
//     drag-shortfall toast in dragRightClick.tsx — which is NOT a descendant of .lx-dock in
//     the real DOM even though it's part of the same Lucius surface. Also carried directly
//     (no actual createPortal call) by DockArtifactPanel.tsx — a plain position:fixed sibling
//     of ChatPanel in RouterShell, not a modal-adjacent surface, but the attribute-based match
//     here doesn't care how an element got into the DOM, only that it's marked as one of
//     these always-reachable Lucius surfaces.
//   - .acct-detail-wrap: TxnDetail's own root. It renders through its OWN Radix
//     Dialog.Portal straight to document.body (see TxnDetail.tsx's own liftAboveModal
//     comment) — a sibling portal, not a DOM descendant of THIS modal's Dialog.Content —
//     so opening it from inside a modal (e.g. "Show in ledger" on an import row) and then
//     clicking anywhere in the drawer was read as an outside click on the modal underneath.
//   - [data-portal-layer]: any overlay a component inside this modal renders via
//     createPortal to document.body — e.g. ui/ModelPicker's popover/sheet. Radix makes a
//     modal dialog set `pointer-events: none` on the body and allows them only inside
//     Dialog.Content, so such a layer got NO pointer events at all: it could not be
//     scrolled or clicked, and the click landed on the overlay and dismissed the modal
//     underneath it (owner report 2026-08-06, Settings -> AI -> Preferred models).
export function isOutsideExempt(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    !!target.closest(
      '.lx-side, .lx-dock, .lucius-mobile-shell, [data-lucius-portal], [data-portal-layer], .acct-detail-wrap',
    )
  )
}

export interface ModalProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  /** Optional secondary line under the title. */
  subtitle?: ReactNode
  /** Controls beside the close button — a view toggle that belongs to the whole dialog (the
   *  import review's "Hide file"), not to one step's body. */
  headerActions?: ReactNode
  size?: ModalSize
  footer?: ReactNode
  children: ReactNode
  closeOnBackdrop?: boolean
  /** The phone's drag-to-dismiss (and its handle bar). Independent from backdrop dismissal;
   *  defaults on for bottom sheets. A modal with protected work or its own drag gestures opts out. */
  dragToDismiss?: boolean
  /** Feature-specific layout hook; responsive geometry remains owned here. */
  className?: string
  /**
   * Same hook for the OVERLAY (scrim). A feature that restyles the backdrop — padding, tint,
   * edge-to-edge on small screens — needs a real class to hang it on: `:has(.my-card)` works
   * but makes the scrim's appearance depend on a descendant match, which is the silent-
   * degradation trap the `--workspace` modifier below is deliberately set explicitly to avoid.
   */
  overlayClassName?: string
  /**
   * A `size="workspace"` modal defaults to full-screen on mobile (a manager surface wants the
   * whole screen), so drag-to-dismiss is vetoed for the tier as a whole. Settings is the one
   * workspace modal whose OWN CSS docks it to the bottom edge as a real sheet below the phone
   * breakpoint (settings.css) — this opts that specific case back into the same gesture every
   * other bottom sheet gets, rather than leaving it hand-rolled per-caller.
   */
  mobileDockedSheet?: boolean
}

/**
 * Canonical modal surface. Radix owns portals, inert background, scroll
 * locking, focus containment/restoration, nested stacking and dismissal.
 * CSS owns the desktop dialog and shared phone bottom-sheet geometry.
 */
export function Modal({
  open,
  onClose,
  title,
  subtitle,
  headerActions,
  size = 'md',
  footer,
  children,
  closeOnBackdrop = true,
  dragToDismiss = true,
  className,
  overlayClassName,
  mobileDockedSheet = false,
}: ModalProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLElement | null>(null)
  const isMobile = useIsMobile()
  // `--workspace` never docks to a bottom sheet (it's a full-screen two-pane manager, see the
  // CSS comment above the mobile media query) — dragging it closed makes no sense. `mobileDockedSheet`
  // is the opt-back-in for the one workspace caller (Settings) whose own CSS overrides that default
  // and docks it to the bottom edge on a phone instead. A modal hosting the command palette is the
  // other standing exception: full-screen, top-anchored, structurally not a bottom sheet either,
  // but Modal itself doesn't know its children's content, so that one is vetoed at drag-start
  // instead of gated on `size`.
  const dragHandlers = useSheetDragToDismiss({
    sheetRef: cardRef,
    overlayRef,
    onDismiss: onClose,
    enabled: isMobile && (size !== 'workspace' || mobileDockedSheet) && dragToDismiss,
    canStart: () => !cardRef.current?.querySelector('.cmdk'),
  })

  useEffect(() => {
    if (open && document.activeElement instanceof HTMLElement) {
      triggerRef.current = document.activeElement
    }
  }, [open])

  // Not dismissing the modal is only half of "Lucius stays reachable": Radix's FocusScope is
  // `trapped` while the dialog is modal, so focus landing on an exempt surface is yanked
  // straight back into the card — you can click the composer but never type into it. Let the
  // trap yield for exactly the surfaces isOutsideExempt() already lists.
  //
  // The trap listens on `document` in the BUBBLE phase, so stopImmediatePropagation() there
  // neutralizes it for this one event. Two details make that safe rather than a hack:
  //   - A LAYOUT effect is required. Radix registers its trap from a passive effect inside a
  //     CHILD of this component, and a parent's layout effect always runs before a child's
  //     passive effect — that ordering is what puts this listener ahead of Radix's, which is
  //     what stopImmediatePropagation needs to bite.
  //   - Stopping at `document` cuts off document-level listeners ONLY. React delegates
  //     onFocus/onBlur at the app root, below document, so the panel's own focus handlers
  //     (placeholder state, mention popovers) have already run and still fire.
  // A non-exempt outside element is untouched and stays trapped — see Modal.focustrap.test.tsx.
  useLayoutEffect(() => {
    if (!open) return
    const yieldToExemptSurface = (event: FocusEvent) => {
      const landing = event.type === 'focusout' ? event.relatedTarget : event.target
      if (isOutsideExempt(landing)) event.stopImmediatePropagation()
    }
    document.addEventListener('focusin', yieldToExemptSurface)
    document.addEventListener('focusout', yieldToExemptSurface)
    return () => {
      document.removeEventListener('focusin', yieldToExemptSurface)
      document.removeEventListener('focusout', yieldToExemptSurface)
    }
  }, [open])

  // …and TOUCH scrolling on those same surfaces. react-remove-scroll's document-level touchmove
  // listener calls preventDefault() for any touch outside the dialog card, so an exempt surface
  // (the transaction drawer opened from review mode, the Lucius shell) could not be finger-
  // scrolled at all (owner report 2026-09-19). Same mechanism as the focus yield above: this
  // listener is registered from a LAYOUT effect, so it sits ahead of the library's and stops it
  // for exempt targets only — the browser then scrolls natively, momentum included. (The wheel
  // leg below applies the delta by hand instead; a touch has no single delta to apply.)
  useLayoutEffect(() => {
    if (!open) return
    const letExemptSurfaceScroll = (event: TouchEvent) => {
      if (isOutsideExempt(event.target)) event.stopImmediatePropagation()
    }
    document.addEventListener('touchmove', letExemptSurfaceScroll)
    return () => document.removeEventListener('touchmove', letExemptSurfaceScroll)
  }, [open])

  // …and the third leg: SCROLL. Radix mounts react-remove-scroll with `shards: [contentRef]`
  // and no `noIsolation`, so its document-level wheel listener calls preventDefault() on every
  // wheel event outside the dialog card — including the exempt surfaces above. The result was a
  // Lucius panel you could click and type into but not scroll, and portalled popovers (the model
  // picker) whose lists were stuck (owner report 2026-08-06).
  //
  // preventDefault() cancels the BROWSER's scrolling, not ours: applying the delta by hand still
  // works and needs no cooperation from the library, so this is immune to listener ordering.
  // Passive, because we never call preventDefault ourselves.
  useEffect(() => {
    if (!open) return
    const scrollExemptSurface = (event: WheelEvent) => {
      const target = event.target
      if (!(target instanceof Element) || !isOutsideExempt(target)) return
      // Nearest ancestor that can actually take the delta — a popover list may be nested several
      // levels inside the exempt root, and the root itself may not be the scroller.
      for (let node: Element | null = target; node; node = node.parentElement) {
        const style = getComputedStyle(node)
        const scrollsY = /(auto|scroll|overlay)/.test(style.overflowY)
        if (scrollsY && node.scrollHeight > node.clientHeight) {
          node.scrollTop += event.deltaY
          return
        }
        if (!isOutsideExempt(node.parentElement)) return
      }
    }
    document.addEventListener('wheel', scrollExemptSurface, { passive: true })
    return () => document.removeEventListener('wheel', scrollExemptSurface)
  }, [open])

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay
          ref={overlayRef}
          className={
            `modal-overlay${size === 'workspace' ? ' modal-overlay--workspace' : ''}` +
            (overlayClassName ? ` ${overlayClassName}` : '')
          }
        >
          <Dialog.Content
            ref={cardRef}
            className={`ui-modal-card ui-modal-card--${size}${className ? ` ${className}` : ''}`}
            aria-describedby={undefined}
            {...dragHandlers}
            // Observable wiring for `closeOnBackdrop`. jsdom cannot faithfully dispatch the
            // pointer sequence Radix listens for, so a test asserting "a click outside is
            // ignored" would be asserting the mock rather than the behaviour — this exposes
            // the decision itself. Not a style hook: nothing in CSS may select on it.
            data-onw-dismissable={closeOnBackdrop ? 'true' : 'false'}
            onEscapeKeyDown={(event) => {
              // A composite popup inside this card, or a portaled layer above
              // it, owns the first Escape. Scope this to the current dialog:
              // an unrelated layer elsewhere must never suppress dismissal.
              const target = event.target
              if (
                cardRef.current?.querySelector('[data-modal-escape-layer]') ||
                (target instanceof Element && !cardRef.current?.contains(target))
              ) {
                event.preventDefault()
              }
            }}
            onPointerDownOutside={(event) => {
              if (!closeOnBackdrop || isOutsideExempt(event.target)) event.preventDefault()
            }}
            onInteractOutside={(event) => {
              if (!closeOnBackdrop || isOutsideExempt(event.target)) event.preventDefault()
            }}
            onOpenAutoFocus={(event) => {
              const preferred = cardRef.current?.querySelector<HTMLElement>('[data-autofocus]')
              if (!preferred) return
              event.preventDefault()
              // preventScroll: focusing an element scrolls the nearest scrollable ancestor to
              // reveal it, and for a tall [data-autofocus] target that ancestor is the PAGE —
              // which dragged the dialog's own header out of view on open. A modal must never
              // scroll the document to focus its own content.
              preferred.focus({ preventScroll: true })
            }}
            onCloseAutoFocus={(event) => {
              const trigger = triggerRef.current
              if (!trigger || !document.contains(trigger)) return
              event.preventDefault()
              trigger.focus()
              triggerRef.current = null
            }}
          >
            {/* Visual affordance only — the whole card is the drag-to-dismiss target now (see
                useSheetDragToDismiss's own top-of-file note). Modal is draggable on mobile like
                `.mb-sheet`/`.kebab-sheet`, both of which show a handle bar — Modal, the app's
                single most-reused surface, silently doing the same with zero visual cue would
                leave the gesture undiscoverable. Gated only on `dragToDismiss`; backdrop taps
                are a separate interaction policy. */}
            {dragToDismiss && (
              <div className="ui-modal-handle-wrap">
                <span className="ui-modal-handle" />
              </div>
            )}
            <div className="ui-modal__head">
              <div className="ui-modal__heading">
                {title ? (
                  <Dialog.Title asChild>
                    <h3 className="ui-modal__title">{title}</h3>
                  </Dialog.Title>
                ) : (
                  <Dialog.Title className="ui-visually-hidden">Dialog</Dialog.Title>
                )}
                {subtitle ? (
                  <Dialog.Description asChild>
                    <p className="ui-modal__subtitle">{subtitle}</p>
                  </Dialog.Description>
                ) : null}
              </div>
              {headerActions && <div className="ui-modal__actions">{headerActions}</div>}
              <Dialog.Close asChild>
                <button type="button" className="ui-modal__close" aria-label="Close">
                  <X size={18} strokeWidth={1.6} aria-hidden="true" />
                </button>
              </Dialog.Close>
            </div>
            <div className="ui-modal__body">{children}</div>
            {footer && <div className="ui-modal__footer">{footer}</div>}
          </Dialog.Content>
        </Dialog.Overlay>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
