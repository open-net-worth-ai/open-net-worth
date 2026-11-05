---
title: Lucius can ask the open app what is on screen
status: Accepted
date: 2026-11-05
deciders: orchestrating agent (Claude) + repo owner
---

# Lucius can ask the open app what is on screen

## Context

A screen snapshot reached Lucius one way: attached to a typed message by `POST /api/chat`. A voice
call starts its turn directly (`voice/desk.ask`), so on a call he could not see the screen at all,
while the owner assumed he could. `get_screen_detail` does not help: it re-reads the database or
the web from a handle's source string, never the page, and only for handles a snapshot listed. A
typed turn also had no way to look again once started.

The server had no way to ask a client a question and wait for the answer.

## Decision

- **`read_screen` asks; the app answers.** `services/screen_read` publishes a `screen_request` on
  `GET /api/events`; an app showing that conversation captures the page with the same
  `captureAndSerialize` a typed message uses and posts it to `POST /api/screen-reads/{id}`. The turn
  waits at most 3s. Ids are unguessable and single-use; nothing is stored; 3 reads per turn.
- **`/api/events`, not `/api/sessions/{id}/live`.** Every open app holds the first. The second has a
  replay buffer that would re-deliver an old request to a late watcher, and a call's panel can
  attach to it late.
- **The device the owner is on wins, by rank.** The one running the call (3) ends the wait; a focused
  one (2) beats a merely visible one (1) within a 250ms settle window, so a slow link doesn't lose to
  an idle tablet. A device out of view doesn't answer.
- **Refusing is not the same as having nothing to show.** With Screen awareness off a device refuses
  at its full rank, so no other device is read in its place. A page covered by Lucius or the phone's
  full-screen call answers at rank 0 and opens no window, so a device in view can still answer.
- **Only the owner at the app may ask.** The tool requires a `chat:web` or `chat:voice` audit surface
  on a non-run session that no chat channel owns. A Telegram sender, an Automation and inbound MCP get "no screen", and the demo
  refuses the tool and locks the route: on a demo every open app is a different visitor.
- **A Private file's contents stay out of the capture.** Text and table previews are real DOM text, so
  a look could have sent them to the model unprompted. `PrivateContent` marks them `data-ctx-secret`
  wherever they show, and the Terminal is marked the same way. This also closes it on the typed path.
- **An open dialog is read first, whatever is behind it.** A text-only dialog over a list of 3+ rows
  used to lose its text, and a stack read the dialog underneath. Every open dialog's text now leads the
  snapshot, top first, so the owner's "this" means what they are looking at on either path.
- **A call offers it unasked.** `read_screen` is not in `CORE_TOOL_NAMES` (typed turns carry a snapshot
  and the offer is cost-sensitive); `desk.ask` loads it, since nobody should wait on the line through a
  `find_tools` round. A call also clears the manifests of any earlier look: a call turn has no screen.

## Consequences

- The returned block is `format_screen_context`'s, sanitised and marked untrusted like the typed path;
  it is the same content under the same toggle, reached at a different moment.
- Screen awareness now also governs a look the owner didn't start. The chat's tool row is the only cue;
  a call has no indicator of its own yet.
- A refusal arriving after the settle window is lost, so a slow device with sharing off can be read
  around by a faster one whose toggle is on. Each device's toggle is its own, so this is low risk.
- A call clears the command manifest too, like any turn with no screen: `run_screen_command` on a call
  needs a `read_screen` first, which is also how the model learns what the screen offers.
- A device showing a different conversation doesn't answer, so a call on one conversation can't read a
  laptop open on another. Deliberately narrow; widen it only if owners ask.

## Alternatives rejected

- **The app pushes snapshots during a call.** Sends screen data nobody asked for, and can still be stale
  when used.
- **First answer wins.** Picks the device with the best connection, not the one being looked at.
- **Screenshots.** Pixels add cost and bypass the capture's secret-field and opt-out rules.
