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
  (The Screen awareness refusal was reversed the same day; see the amendment.)
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

## Amendment, 2026-11-05: Screen awareness only decides what a message carries

The owner's call, once the refusal had shipped: the switch is about whether the screen is sent by
default, and it should not block anything else. So `read_screen` works whichever way it is set, on
every device, and `sharing_off` is gone. An app still open from before this change gets a 422 on its
old refusal, which counts as no answer.

- **What still keeps content from Lucius** is what kept it from the typed path: `data-ctx-secret`,
  password-shaped fields, Private files, and only a device in view showing this conversation answers.
  The chat's "Read your screen" row is still the only cue. Turning the switch off no longer stops him
  looking: an owner who wants that has no control for it today.
- **A turn with no snapshot offers `read_screen`.** `api/chat` loads it, as a call already does. Its
  schema is small, and only sessions that sent a message with the switch off carry it. A surface that
  replaces the screen sends the command catalog, not null, so it doesn't load it.
- **A read is a full substitute for a snapshot.** Same capture, same block and same manifests, so
  `get_screen_detail` and `run_screen_command` act on it in the same turn, and their empty-manifest
  errors now point at `read_screen`. A screen's own tools join this turn's scope as well as the
  session's: the offer is seeded once at turn start, so they used to arrive a turn late, and a later
  `find_tools` persisted that scope over them.
- The consequences above about Screen awareness governing a look and a refusal arriving late no
  longer apply.
- **The name stays, so the bar explains it.** The owner kept "Screen awareness". Flipping the switch
  shows what it did, and while it's off the bar says what turning it on does: "Attach screen content
  as context".
- **A default joins the per-device switch.** `profile.screen_awareness_default` (on) is what a device
  uses until it has a stored choice, so an existing choice survives. Changing it in Settings → Chat
  drops the stored choice on that device only: another device keeps the choice it made.
