---
title: Desktop app
description: Set up the desktop app — where your book lives, connecting to your own server, background mode, watched folders, notifications and automatic updates.
category: operations
order: 65
audience: [user, operator, developer]
status: stable
related: [getting-started/installation, getting-started/first-run, reference/keyboard-shortcuts, operations/upgrading]
keywords: [desktop, macOS, Windows, Linux, AppImage, Electron, folders, notifications, backup, updates, tray, start at login, connect to server]
---

# Desktop app

The desktop app is the easiest way to run Open Net Worth: it includes everything it needs, keeps
your book in a folder on your computer, and updates itself. It can also be a window onto an
Open Net Worth server you run elsewhere.

It runs on macOS (Apple Silicon), Windows 10/11 (x64) and Linux (x86_64, AppImage or `.deb`).
To download it, see [Installing Open Net Worth](../getting-started/installation.md).

## First launch: choose where your book lives

1. Open the app and click **Get started**.
2. On **Where should we keep your book?**, the app suggests a **Book folder** on this computer —
   `~/.open-net-worth` in your home folder (on Windows, `%USERPROFILE%\.open-net-worth`).
   - Click **Continue** to use it. If a book is already there, it opens.
   - Or click **Change** to pick another folder. An empty folder starts a new book. A folder
     that already holds other files gets a new **Open Net Worth** subfolder for the book, so it
     never mixes with your files (the app asks first).
   - Or, under **Or keep your book elsewhere**, choose **On your own server** — see
     [Connect to a server](#connect-to-a-server).
3. The app then walks you through [your first run](../getting-started/first-run.md). To bring
   back a book you exported earlier, click **I have a backup to restore** on the first step — see
   [Export and portability](export-and-portability.md).

### Opening a book you already have

The desktop app starts with its **own** book, separate from a browser/server install. To open an
existing book instead:

- **Settings → Desktop → Your book → Open another folder…**, or **File → Open book folder…**
- Choose the book's main folder (the one containing `data/onw.db`). Choosing its `data` folder
  works too.

The app confirms, closes any unfinished reviews, reloads with the chosen book and remembers it
next time. Your previous book stays where it is. A folder with unrelated files, a book already
open in another program, or a book from a newer version is refused, and the app stays on the
book you had.

Only one program can have a book open at a time. If an Open Net Worth server on this computer is
already using a book, [connect to that server](#connect-to-a-server) instead of opening its folder.

## Connect to a server

Use this when Open Net Worth runs on another machine (or as a server on this one) and you want
the desktop app as its window.

1. At first launch choose **On your own server**, or later use **File → Connection…** or
   **Settings → Desktop → Connection → Change connection**.
2. In **Server URL**, enter the address you open in your browser — the root only, such as
   `https://wealth.example.com` or `http://127.0.0.1:8080` (no path).
3. Click **Connect** and sign in with **that server's** password.

Rules for the address: a public server must use `https://`. Plain `http://` works for this
computer (`localhost`/`127.0.0.1`), and for a private home-network address after you confirm
**Connect anyway** on the "This connection is not encrypted." warning.

While connected, your book, AI settings and Automations all live on the server and keep running
when you quit the app. The desktop features that act on a local book — import, export, open book
folder, watched folders, review documents — are turned off; use the server's own upload and
download screens instead. Your last local book is remembered; switch back from the same
**Connection** screen (**On this computer**).

When the app starts, it waits up to a minute for the server to answer, so a server on this
computer that starts at login (`onw autostart install`) has time to come up. If it still can't be
reached, you see **Can't reach your server.** with **Try again** and **Change connection…** (which
also offers **On this computer**, to use a local book instead). The app keeps checking in the background on that screen — it
shows **Trying again in 8s…** counting down to the next check, waiting a little longer each time up
to 15 seconds — and opens the server by itself as soon as it answers.

## Keep it running in the background

After setup, a one-time **Ready when you need it** window offers two switches, both off until you
turn them on. Change them later in **Settings → Desktop → Background activity** (or the
**Background and startup…** menu item).

- **Keep running when the window closes** (called *Keep running in the background* in the
  one-time window) — the app keeps an icon in the menu bar (macOS) or system tray
  (Windows/Linux), and your imports, Automations and browser access keep running after you close
  the window. **Quit** stops it.
- **Start at login** — starts quietly in the tray when you sign in to your computer. No window
  opens until you open the app. It doesn't wake a sleeping computer.

### Keep computer awake

**Keep computer awake** (Settings → Desktop → Background activity, or the **Keep awake** switch on
Automations) stops idle sleep while the app runs, so Automations run on time. The display can
still turn off; closing a laptop lid still sleeps (unless it's plugged in with an external display,
where Keep awake still holds). Off by default.

Running the server yourself (`onw`) on macOS or Windows? The same **Keep awake** switch appears on
Automations in the browser and holds that computer awake while the server runs. It isn't offered
on Linux or in Docker — use the machine's own power settings there.

With background mode off, closing the window quits the app on Windows and Linux. On macOS,
closing the window only hides it; the app keeps running until you quit.

On Linux, stock GNOME shows no tray icon. With background mode on, start Open Net Worth again
from the applications menu to bring the window back. Start at login there is an entry in
`~/.config/autostart`, removed when you turn it off.

When connected to a server, your Automations run on the server whether or not the app is open.

## Notifications

**Settings → Desktop → Notifications → Show desktop notifications** (on by default) shows a system
banner for:

- everything that appears in Activity, and
- Intel alerts: a price alert firing, a sharp-drop warning on a holding, an explained watchlist
  alert, and a news analysis worth acting on.

Each banner shows the alert's own title and summary — amounts included — so it can appear on your
lock screen. To hide that, turn off previews for Open Net Worth in your system's notification
settings. No notification channel needs to be set up; the app shows these itself, once each.

If more than three arrive at once (say, after your computer wakes), you get one banner,
"N new alerts", naming the newest few. When the app starts, only Intel alerts from the last
15 minutes are announced; older ones wait in Intel. Clicking a banner opens the transactions to
review, or Intel for an Intel alert.

## Watch a folder for new statements

1. Go to **Settings → Desktop → Statement folders** and click **Choose folder…**.
2. Pick the folder your bank statements download to.
3. When a new file lands there, you get "New documents are ready to review." Click **Review** next
   to it (or **View → Review documents**) to open it in the usual import review. Nothing is booked
   until you approve the rows, and nothing is sent to an AI model just because it arrived.

A document marked Private stays Private and refuses an AI preview until you change that in
Documents. **Remove access** stops future reads; files already copied into your book stay.
Folder access belongs to this computer and doesn't travel with an exported book.

You can also import one-off files with **File → Import statement or receipt…**
(<kbd>⌘</kbd>/<kbd>Ctrl</kbd> + <kbd>O</kbd>). Each file opens in the review separately.

## Updates

The app checks for a new version 30 seconds after it starts and every 4 hours after that.

1. When one is out, it downloads in the background — nothing to answer — and the menu shows
   **Downloading Update…**.
2. When it's ready, you get a notification and the menu shows **Restart to Update**. Choose it to restart now, or keep working: the update installs the next time you
   quit. The app never restarts on its own, and it closes your book safely before installing — a
   small **Installing Open Net Worth X…** window shows while it does.
3. After restarting, a notification confirms "Open Net Worth was updated to X."

To check right away: **Open Net Worth → Check for Updates…** on macOS, **Help → Check for
Updates…** on Windows and Linux. A manual check asks first — **Download and Install**, **Skip This
Version** or **Later** — always offers the newest version, even one you skipped, and says "You're
up to date." otherwise.

- **Skip This Version** is remembered until a newer version comes out; background checks don't
  download it.
- If a background download fails, the menu shows **Update Available…** and the next check tries
  again.
- **Linux AppImage** downloads the new AppImage, checks it against the release's signed
  `SHA256SUMS`, and swaps it in place. A `.deb` install, or an AppImage in a folder you can't
  write to, can't update itself: you get a notification and **Update Available…** offers **Open
  Download Page**.
- **A copy you built yourself** can't install updates; it says so and offers the download page.
  Install the released app once and later updates work normally.
- To turn automatic checks off, start the app with `ONW_DESKTOP_UPDATES=0`.

When the new version starts, it updates your book's database automatically, with a backup first —
see [Upgrading](upgrading.md). An older version can't open a book a newer one has updated.

## Back up your book

**Settings → Desktop → Your book → Export backup…** or **File → Export book…**
(<kbd>⇧</kbd><kbd>⌘</kbd><kbd>E</kbd> / <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>E</kbd>) saves your
whole book as one `.zip` you can keep anywhere outside the book folder. This is the same export
as Settings → Data — see [Export and portability](export-and-portability.md). The app also keeps
its own daily backups — see [Backup and restore](backup-and-restore.md).

## Window and shortcuts

On macOS the window has no separate title bar: the traffic-light buttons sit in the app's top bar,
next to a sidebar toggle and Back / Forward arrows. Drag an empty part of the bar to move the
window; double-click it to zoom. Windows and Linux use the system title bar and a menu bar.

The window reopens at the size and position you left it, zoomed or maximized if it was. If that
spot was on a display that is no longer connected, it opens at the same size on the main display.

| Action | macOS | Windows / Linux |
|---|---|---|
| Reload / Force Reload (View menu) | <kbd>⌘</kbd><kbd>R</kbd> / <kbd>⇧</kbd><kbd>⌘</kbd><kbd>R</kbd> | <kbd>Ctrl</kbd><kbd>R</kbd> or <kbd>F5</kbd> / <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>R</kbd> |
| Back / Forward (Go menu) | <kbd>⌘</kbd><kbd>[</kbd> / <kbd>⌘</kbd><kbd>]</kbd> | <kbd>Alt</kbd><kbd>←</kbd> / <kbd>Alt</kbd><kbd>→</kbd> |
| Show or hide the sidebar | <kbd>⌘</kbd><kbd>B</kbd> | <kbd>Ctrl</kbd><kbd>B</kbd> |
| Command palette | <kbd>⌘</kbd><kbd>K</kbd> | <kbd>Ctrl</kbd><kbd>K</kbd> |
| Go to Lucius, Assets, Accounts, Plan, Investments, Intel, Commitments, Dashboard (Go menu) | <kbd>⌘</kbd><kbd>1</kbd> to <kbd>8</kbd> | <kbd>Ctrl</kbd><kbd>1</kbd> to <kbd>8</kbd> |
| Close Window (hides it, like the red button) | <kbd>⌘</kbd><kbd>W</kbd> | not applicable |
| Keyboard Shortcuts (Help menu) | <kbd>⇧</kbd><kbd>⌘</kbd><kbd>/</kbd> | <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>/</kbd> |
| Open Lucius | <kbd>⌘</kbd><kbd>L</kbd> | <kbd>Ctrl</kbd><kbd>L</kbd> |
| New Lucius conversation — in the Lucius side panel when it's open, otherwise on the Lucius screen | <kbd>⌘</kbd><kbd>N</kbd> | <kbd>Ctrl</kbd><kbd>N</kbd> |
| New transaction | <kbd>⇧</kbd><kbd>⌘</kbd><kbd>N</kbd> | <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>N</kbd> |
| Import statement or receipt | <kbd>⌘</kbd><kbd>O</kbd> | <kbd>Ctrl</kbd><kbd>O</kbd> |
| Export book | <kbd>⇧</kbd><kbd>⌘</kbd><kbd>E</kbd> | <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>E</kbd> |
| Settings | <kbd>⌘</kbd><kbd>,</kbd> | <kbd>Ctrl</kbd><kbd>,</kbd> |
| Hide amounts | <kbd>H</kbd> | <kbd>H</kbd> |

**Keyboard Shortcuts** uses <kbd>⇧</kbd> because plain <kbd>⌘</kbd>/<kbd>Ctrl</kbd> +
<kbd>/</kbd> shows or hides the Lucius panel. With the sidebar hidden, hovering the sidebar button
previews it (<kbd>Esc</kbd> closes the preview); clicking pins it open. Shortcuts that also work
in a browser are under [Keyboard shortcuts](../reference/keyboard-shortcuts.md).

Where things are in the menus:

- **Open Net Worth** (macOS only): About, **Settings…**, **Check for Updates…**,
  **Background and startup…**, Quit.
- **File**: **Connection…**, **Open book folder…**, **Open in browser**, **Import statement or
  receipt…**, **Export book…**, **New conversation**, **New transaction**. On Windows and Linux, File also holds
  **Settings…**, **Background and startup…** and Quit.
- **View**: command palette, **Toggle Sidebar**, Lucius, **Review documents**, zoom, reload, full
  screen.
- **Go**: Back, Forward and the eight main screens.
- **Window**: includes **Show Main Window**, which brings the app back even when it's hidden to the
  tray.
- **Help**: **Open Net Worth Help** (this documentation), **Keyboard Shortcuts**, a
  **Troubleshooting** submenu (**Open Logs Folder**, **Reveal Book Folder**, **Restart Server**),
  **Report a Problem…**, and on Windows/Linux **Check for Updates…**.

The tray icon menu has **Open Open Net Worth**, **Open in browser**, **Import statement or
receipt…**, **Review documents** and **Quit Open Net Worth**.

## Troubleshooting

**"Your book folder isn't there anymore"** — the folder the app remembered was moved, renamed,
deleted, or is on a drive that isn't connected. Nothing was created or deleted at the old path.
Reconnect the drive and choose **Open another folder…**, or pick **Create a new book…**,
**Restore from backup…** (needs an empty folder, then restores an exported `.zip`) or **Connect to
a server**.

**"Book already open"** — another program (usually an Open Net Worth server on this computer) has
this book open. Stop it and choose **Retry startup**, or **Choose another folder**, or connect to
that server instead.

**The book is from a newer version** — click **Check for updates** to update the app. An older
app never changes a newer book.

**"Open Net Worth could not start"** — click **Try again**. If it keeps failing, **Open logs
folder** shows the reason (see [Logs](logs.md)), and **Report this problem** sends it to the
developers after you've reviewed it.

**Something's stuck** — **Help → Troubleshooting → Restart Server** restarts the app's local
engine without quitting (unsaved changes and unfinished reviews close).

## What the desktop app doesn't do

Lucius's code sandbox, local (stdio) MCP servers for Lucius, and the app-building coding engines
aren't available in the desktop app; HTTP MCP servers and your configured AI providers work as
usual. The other direction does work: Claude Desktop, Cursor and other AI apps can use your book
through the app — see [Use your book from other AI apps](connect-ai-apps.md).

## Related pages

- [Installing Open Net Worth](../getting-started/installation.md)
- [Your first run](../getting-started/first-run.md)
- [Upgrading](upgrading.md)
- [Backup and restore](backup-and-restore.md)
- [Logs](logs.md)
