// screenRead.ts — how this device answers read_screen (pure; backend services/screen_read.py).
// The ranks mirror RANK_* there: the server keeps the highest, so the device the owner is
// actually on is the one read. A device out of view doesn't answer at all. Screen awareness has
// no say here: it decides what a message carries by default, not whether Lucius may look.

export type ScreenReadStatus = 'ok' | 'no_screen'

export const RANK_COVERED = 0
export const RANK_VISIBLE = 1
export const RANK_FOCUSED = 2
export const RANK_CALL = 3

export interface ScreenReadView {
  /** This device is running the call the request came from. */
  onCall: boolean
  focused: boolean
  visible: boolean
  /** Lucius fills the page, or the phone's full-screen call covers it: no app screen to read. */
  covered: boolean
}

export function decideScreenReply(
  view: ScreenReadView,
): { status: ScreenReadStatus; rank: number } | null {
  if (!view.visible) return null
  if (view.covered) return { status: 'no_screen', rank: RANK_COVERED }
  const rank = view.onCall ? RANK_CALL : view.focused ? RANK_FOCUSED : RANK_VISIBLE
  return { status: 'ok', rank }
}
