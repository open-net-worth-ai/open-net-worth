---
title: A finished conversation is the server's transcript; a sent message is known by its client id
status: Accepted
date: 2026-11-18
deciders: repo owner + Claude
---

# A finished conversation is the server's transcript; a sent message is known by its client id

## Context

A browser keeps its own copy of each conversation (IndexedDB, plus the live slice), built from
whatever stream events it happened to receive. The server keeps the transcript. When they
disagreed, `chatStore.shouldAdoptRemote` / `useChat.pickFresher` / `completeUnfinishedTail` /
`repairCutReplies` decided which copy was "fresher" by rules of thumb: last-id counters,
bubble counts, block counts, reply lengths, the question before the last reply. Each rule was
added after a live report and each is right for its case, but the two copies cannot be aligned
— live-born ids (`a_…`, a client uuid) never match the server's positional `{conv}-{n}` — so
every new way a stream can be cut produced a new half-finished copy that one of the rules
misjudged.

Live report 2026-11-18: a phone suspended mid-turn. Its stream died silently; a question
queued behind the turn never reached the server; on return iOS resumed the page's timers about a
minute before it fired any visibility event. When the catch-up finally ran, the saved copy split
the turn into more bubbles with more blocks than the server's rebuild, so it out-scored the
finished transcript. The rejected read still recorded the server's revision stamp, so every
later return skipped the re-read as "unchanged", and a restart re-ran the same comparison. The
queued question showed "Still queued" forever: a send that failed while Lucius was busy left
its chip up and nothing ever retried it or said it had failed.

## Decision

- **When no turn is running, the server's transcript is the conversation.** `GET
  /api/sessions/{id}/messages` returns `live` (`live_sessions.is_live`). On `live: false` the
  client takes the transcript as it is (`utils/chatServerSync.syncWithServer` →
  `takeServerTranscript`): no richness comparison. Older messages outside the fetched tail are
  kept only where they carry a server positional id below the tail; anything else is re-read
  by paging. Two things only the browser can know survive: a send the server never got (its
  bubble, then nothing or only an error bubble — identified by id, see below), and the
  provider-setup hint on a failure the server still ends on. While a turn IS running, or a
  server too old to say, the old rules still apply, because the live copy can legitimately be
  ahead of a mid-turn snapshot.
- **A user message is known by the id its client gave it.** The server stores
  `client_message_id` on the transcript entry (`assistant.CLIENT_ID_KEY`, all three entry
  points: a fresh turn, a mid-turn injection, a recovered residue) and renders it as
  `clientId`. A POST whose id the server already holds, in the transcript or queued for a turn
  that is still running, is answered `202 {"accepted": "duplicate"}` and runs nothing. That
  makes a resend safe. A copy stranded in the queue with no turn left to drain it is taken
  back, and the resend starts the turn that answers it, so it is never answered twice and
  never waved off.
- **Messages sent while Lucius is busy go through an outbox** (`state/chatOutbox.ts`). A send
  that fails, or that a finished transcript does not name, turns `pending: 'failed'` with
  Retry, which resends under the same id. One the finished transcript names leaves the outbox.
  A send still in flight is never failed by a read that raced its 202.
- **Recovery runs off the server's live set, not browser events.** The app-wide 4-second
  `/api/sessions/live` poll is a plain timer. A view still following a turn the server says has
  ended reconnects (the attach ends at once and re-reads). A turn that just left the live set
  re-reads. `focus` joins the return events. A re-read that showed nothing forgets its revision
  stamp, so the next return looks again.

## Consequences

### Positive
- "Is this finished, and what does it say?" is answered by the server, so a phone that missed
  the end of a turn, or restarted over a stale saved copy, lands on the same transcript as
  every other device.
- No message can sit at "queued" forever: it is delivered, or visibly failed with a safe Retry.
- No dependence on iOS firing `visibilitychange` on resume.

### Negative
- A finished conversation renders the server's rebuild, never this browser's richer live copy.
  Anything the rebuild cannot draw is gone after the next read. The rebuild already reconstructs
  cards, restore marks, receipts, search chips and cost chips; the setup hint is carried over by
  hand. A new live-only field must either be rebuilt by `rehydrate_messages` or added to
  `keepLocalOnly`.
- History older than the fetched tail that this browser held only under live-born ids is
  dropped from view until paged back in.
- Mid-turn the richness rules are still the arbiter; they remain as complex as before for that
  window.

### Follow-ups
- Tag live events with the server message id and a sequence number, so a reconnect resumes
  from a cursor and the mid-turn window needs no rules of thumb either. That needs
  `rehydrate_messages`'s positional ids to be known while a turn is being written.
- Once that lands, retire `shouldAdoptRemote` / `pickFresher` / `completeUnfinishedTail` /
  `repairCutReplies`.

## Alternatives considered
- **Another rule of thumb** (prefer the server when its last reply's text is missing locally,
  match the queued question by text) — rejected: the same class of guess that kept failing,
  and server text is rewritten (mentions, screen context, injection fence), so text matching
  is not exact.
- **A per-message status column + sequence number on `session_messages`, now** — deferred, not
  rejected: it needs a migration and a live-event protocol change. The server already knows
  the one status that matters for a finished conversation (`is_live`), and positional ids
  already align the transcript, so this decision gets the guarantee without either.
- **Persist the outbox across restarts** — rejected for now: a restart re-reads the finished
  transcript, which names everything the server got. A message lost before the server got it
  is lost with the tab, the same as an unsent draft.

## Agent provenance
- **Decided by:** Claude (Claude Code session), with the repo owner.
- **Lane / branch:** `fix/chat-sync-authoritative` (worktree `.claude/worktrees/chat-sync`).
- **Alternatives weighed:** a further rule of thumb; a full status/sequence protocol; this
  middle path.
- **Driving artifacts:** live report 2026-11-18 (server log and transcript for the
  conversation; phone screenshot).
- **Why this way (one line):** a finished conversation has exactly one truth, already on the
  server, so the client should read it instead of guessing which copy is newer.
