import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
  type ReactNode,
} from 'react'

/**
 * The config view's ONE primary action, published by whichever credential form is on screen.
 *
 * ## The defect this removes
 *
 * Every `*Section.tsx` form was written for the OLD Connectors tab, where it sat inline on a
 * card and owned its own Save button. The redesign re-hosted them as a view of the detail
 * modal — which supplies its own pinned footer — and both controls survived. The result was a
 * body `Save` and a footer `Done` on the same screen, with nothing telling the user whether
 * `Done` wrote their edits or dropped them. (It dropped them: `Done` was `leaveConfig`.)
 *
 * ## The shape
 *
 * The footer owns the action; the form owns the work. A form publishes `{ dirty, saving,
 * error, save }` through this context, the footer renders one `Save` button from it. Save just
 * saves (2026-08-22 — used to also navigate back to the detail page on success; that auto-close
 * was itself a bug for Telegram's pairing flow, bouncing the operator off the one screen that
 * shows live pending requests right when Save turned pairing on). Leaving — saved or not — is
 * the `‹ <name>` back control that was always there.
 *
 * `dirty` is real, not a placeholder: each form hands `useConnectorSave` its editable values
 * and the values the server last returned, and the button is disabled while they match. A
 * form that publishes nothing (an MCP server, an `.env`-managed connector — neither has a
 * form at all) leaves the footer with the plain Done it had before.
 *
 * ## Publishing every render is deliberate
 *
 * `publish` bails when the scalar state is unchanged, so calling it from an unconditional
 * effect cannot loop: the parent's `setState` is skipped, no re-render follows. The `save`
 * callback is kept in a ref rather than in state for the same reason — a form that rebuilds
 * its closure each render (all of them do) would otherwise re-render the modal forever.
 */
export interface ConnectorSaveState {
  /** Whether there is anything to write. Drives the footer button's disabled state. */
  dirty: boolean
  saving: boolean
  /** Set after a failed write. The footer stays open and the form shows the message. */
  error: boolean
  /** Perform the write; resolve `true` only when it landed. */
  save: () => Promise<boolean>
}

type Publish = (state: ConnectorSaveState | null) => void

const PublishContext = createContext<Publish | null>(null)

/** Tells the page a form wrote on its OWN (an inline Save button, an Approve) rather than through
 *  the footer — so the header's Connected badge and the list behind refresh the same way. */
const WroteContext = createContext<(() => void) | null>(null)

/** For a form: call the returned function after any write it makes outside the footer Save. */
export function useConnectorWrote(): () => void {
  const wrote = useContext(WroteContext)
  return useCallback(() => wrote?.(), [wrote])
}

export interface ConnectorSaveHost {
  /** Present once a form has published one. `null` → render the plain Done footer. */
  state: { dirty: boolean; saving: boolean; error: boolean } | null
  /** Runs the published form's save. Resolves false when there is nothing to run. */
  run: () => Promise<boolean>
  /** Wrap the config view's body in this. */
  Provider: (props: { children: ReactNode }) => JSX.Element
}

/** The modal side: hold the active form's save state and expose it to the footer. */
export function useConnectorSaveHost(onWrote?: () => void): ConnectorSaveHost {
  const wroteRef = useRef(onWrote)
  wroteRef.current = onWrote
  const wrote = useCallback(() => wroteRef.current?.(), [])
  const [state, setState] = useState<{ dirty: boolean; saving: boolean; error: boolean } | null>(
    null,
  )
  const saveRef = useRef<(() => Promise<boolean>) | null>(null)

  const publish = useCallback<Publish>((next) => {
    saveRef.current = next?.save ?? null
    setState((prev) => {
      if (next === null) return prev === null ? prev : null
      if (
        prev !== null &&
        prev.dirty === next.dirty &&
        prev.saving === next.saving &&
        prev.error === next.error
      ) {
        return prev
      }
      return { dirty: next.dirty, saving: next.saving, error: next.error }
    })
  }, [])

  const run = useCallback(async () => {
    const save = saveRef.current
    if (!save) return false
    return save()
  }, [])

  const Provider = useCallback(
    ({ children }: { children: ReactNode }) => (
      <PublishContext.Provider value={publish}>
        <WroteContext.Provider value={wrote}>{children}</WroteContext.Provider>
      </PublishContext.Provider>
    ),
    [publish, wrote],
  )

  return useMemo(() => ({ state, run, Provider }), [state, run, Provider])
}

/**
 * Fold a revealed-but-unedited secret back to blank for the dirty comparison.
 *
 * `useSecretReveal` puts the stored value INTO the box so it can be edited in place, which
 * would otherwise read as an edit and arm the footer's Save the moment the operator merely
 * looked at a key. Blank is what the form would have held had they not looked, and blank is
 * what every one of these stores reads as "keep the saved value", so it is the honest
 * baseline. Editing the revealed value clears `pristine` and the field counts again.
 */
export function unrevealed(value: string, reveal: { pristine: boolean }): string {
  return reveal.pristine ? '' : value
}

/**
 * Gates a "required" error behind the user actually having reached the field — a fresh
 * connector's blank required field is invalid from the first render (that's what disables
 * Save), but showing red error text before anyone has had a chance to type is the classic
 * error-before-interaction bug, not a real validation failure yet. Spread `onBlur` onto the
 * field and only render the error string once `touched` is true.
 */
export function useTouched(): { touched: boolean; onBlur: () => void } {
  const [touched, setTouched] = useState(false)
  return useMemo(() => ({ touched, onBlur: () => setTouched(true) }), [touched])
}

export interface UseConnectorSaveOptions<T> {
  /** False while the form is still loading — nothing to save yet. */
  ready: boolean
  /** The form's current editable values. */
  values: T
  /** The same shape, built from what the server last returned. */
  baseline: T
  saving: boolean
  /** Truthy when the last write failed. */
  error: unknown
  /** Perform the write; resolve `true` only when it landed. */
  save: () => Promise<boolean>
}

/**
 * The form side: publish this form's save to the modal footer.
 *
 * `values` vs `baseline` is compared by JSON, which is exact for the flat
 * string/boolean/number/array shapes these forms hold, and means a form gets a correct
 * dirty flag without hand-maintaining one field at a time.
 */
export function useConnectorSave<T>({
  ready,
  values,
  baseline,
  saving,
  error,
  save,
}: UseConnectorSaveOptions<T>): void {
  const publish = useContext(PublishContext)
  const dirty = ready && JSON.stringify(values) !== JSON.stringify(baseline)

  // Unconditional: `publish` is idempotent for unchanged scalars, and this must re-run on
  // every render because `save`'s closure captures the values it is about to write.
  useEffect(() => {
    publish?.({ dirty, saving, error: Boolean(error), save })
  })

  // A form leaving the screen takes its save with it — otherwise the footer would keep
  // offering the previous connector's write.
  useEffect(() => () => publish?.(null), [publish])
}
