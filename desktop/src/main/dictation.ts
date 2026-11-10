import { BrowserWindow, Menu } from 'electron';

// macOS dictation, started from the page's mic (owner, 2026-11-03). Nothing that asks it to stop
// works from here — Start Dictation… again, AppKit's stopDictation:, moving focus within the page
// all left it listening — and macOS says nowhere whether it is listening (looking for its
// listening mark missed it, so stops were skipped). It does end when its window stops being the
// key window: that is the one lever, used every time, never guessed around.

/** The key window moves to an invisible 1-pixel window of ours and straight back, so the app stays
 *  in front — resigning the window itself let another app come forward, and the window vanished
 *  and reappeared (owner, 2026-11-03). The caret stays where it was. */
export async function stopDictation(win: BrowserWindow): Promise<void> {
  const [x, y] = win.getPosition();
  const helper = new BrowserWindow({ width: 1, height: 1, x, y, frame: false, transparent: true, hasShadow: false, skipTaskbar: true, show: false, webPreferences: { sandbox: true, javascript: false } });
  try {
    helper.showInactive();
    helper.focus();
    await new Promise(resolve => setTimeout(resolve, 200));
  } finally {
    helper.destroy();
    if (!win.isDestroyed()) giveBackFocus(win);
  }
}

/** The window AND the page in it: focusing only the window left the page without the text focus
 *  — no caret, and dictation started with nothing to type into (owner, 2026-11-04). */
function giveBackFocus(win: BrowserWindow): void {
  win.focus();
  win.webContents.focus();
}

/** Start Dictation… on the focused field — nothing before it: ending a dictation first (the
 *  resign above) left the next start dead, so the mic stayed red and nothing was heard (owner,
 *  2026-11-03). */
export function startDictation(win: BrowserWindow): void {
  giveBackFocus(win);
  Menu.sendActionToFirstResponder('startDictation:');
}
