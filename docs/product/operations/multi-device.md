---
title: Using this from more than one device
description: Open your book from your phone, laptop or tablet — how to set it up, how sign-in works, and how to sign every device out.
category: operations
order: 60
audience: [user, operator]
status: stable
related: [operations/exposing-beyond-localhost, operations/security-and-privacy, operations/desktop]
keywords: [multi-device, phone, tablet, sync, sessions, logout, sign out, single password, home screen, stay signed in]
---

# Using this from more than one device

Every device you use talks to the same Open Net Worth, so they all see the same live book —
there's nothing to sync and no per-device setup. Any browser (or the desktop app) that can reach
your instance and knows the password can sign in.

## Set it up

1. **Run Open Net Worth as a server** on a computer that stays on — the one-line server install,
   Docker or a source checkout (see [Installing](../getting-started/installation.md)). The desktop
   app's own book can only be opened on that one computer.
2. **Set a password** in **Settings → Security** on that computer.
3. **Make it reachable from your other devices** — the safe ways are on
   [Exposing this beyond localhost](exposing-beyond-localhost.md) (a private network like
   Tailscale is the simplest).
4. **On each other device**, open the address in a browser and sign in with the password. On
   another computer you can use the desktop app instead: **On your own server** at first launch,
   or **File → Connection…** — see [Connect to a server](desktop.md#connect-to-a-server).
5. **On a phone**, add it to your home screen (Safari: **Share → Add to Home Screen**; Chrome:
   **⋮ → Add to Home screen**) so it opens like an app. The phone keeps the icon it captured
   when you added it, so if an older shortcut shows a plain letter **N** instead of the logo,
   delete it and add it again.

## Signing in

Just the password: no approval on another device, no pairing code, no limit on how many devices
are signed in at once. Each device stays signed in for 30 days by default; change that under
**Settings → Security → Auth behaviour → Stay signed in for** (8 hours, 7 days or 30 days).

## Signing out

- **Sign out one device** — on a phone, open the **More** menu and choose **Log out**. It only
  signs out that browser. (A computer's browser has no Log out button; clear the site's cookies
  there, or use the next option.)
- **Sign out every other device** — **change your password** in **Settings → Security**. Every
  other device is signed out at once and needs the new password; the device you changed it on
  stays signed in. There's no list of devices to sign out one by one.

Your API token (used by other AI apps and phone shortcuts) isn't affected by a password change.
To cut those off, rotate the token — see [Security and privacy](security-and-privacy.md#login).

## On a Private Cloud server

Sign-in belongs to your Private Cloud account, not to the app. **Settings → Security** shows who
is signed in and links back to the Private Cloud portal. **Sign out of this app** ends the app's
session and takes you to the portal; **Sign out everywhere** also ends your portal session on
every device. If your session runs out while you work, the app saves the message you were typing,
shows "Signing you back in…", and puts it back when you return.

## One password per book, not per person

There are no separate accounts. Everyone in your household who uses it shares the same password
and sees the same book, with the same permissions.

## Related pages

- [Exposing this beyond localhost](exposing-beyond-localhost.md) — reaching your instance safely
- [Desktop app](desktop.md) — using the desktop app as a window onto your server
- [Security and privacy](security-and-privacy.md) — how sign-in is protected
