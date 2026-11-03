---
title: Restoring a message undoes all of that message's checkpoints and every later turn of the conversation; an approved card's call is recorded in a checkpoint on the message that proposed it
status: Accepted
date: 2026-11-03
deciders: Owner (bug report, 2026-11-03); Claude (implementer)
---

# Restore undoes the message and every later turn

## Context

The Restore dialog says "undoes this and any later changes"; the docs say restore puts the data
"back as it was before that message"; the `restore_checkpoint` tool says "and every turn after
it". The owner restored an earlier message and found two things still in the book:

1. **An approved write was recorded nowhere.** A gated call runs in the resolve step, after the
   proposing turn paused and finalised its checkpoint and before the resumed turn opened one. No
   checkpoint was open, so the account it created could not be restored by any button.
2. **Later turns were never undone.** `restore()` replayed only the chosen checkpoint. Later turns
   were marked orphaned, so their Restore buttons disappeared, but their writes stayed.

## Decision

- **Restore replays a chain.** Restoring a turn checkpoint replays the message's first checkpoint,
  every other checkpoint on the same message, and every later active turn checkpoint of the same
  conversation (`_restore._restore_chain`). The chain runs as one merged replay: each row goes
  back to its image in the oldest checkpoint that touched it, and each file to its oldest snapshot.
  The DB half is still one transaction. The pre_restore (Undo) snapshot covers the union, so Undo
  brings back everything in one step. Other conversations' turns are not undone.
- **An approved call has its own checkpoint**, anchored on the message that proposed the card
  (`assistant._run_approved_tool_use`), and emits `checkpoint_created`. A resumed turn's checkpoint
  anchors on that message too, matching where the client already showed it. Any checkpoint on a
  message restores the whole message. The restored mark goes on the message's first checkpoint,
  which is the one left active after the later ones are superseded.
- **A rewind flags the turns it rewound** (`rewound`), because the next message reuses their
  transcript index. Flagged turns never get grouped with the new message or carry its Restore
  button. They stay active, so a chat-only rewind's data is still undone by restoring an earlier
  message.

## Consequences

- Restoring an early message of a long conversation undoes more than it used to, which is what the
  dialog always promised. The preview counts every turn the restore will undo.
- A pre_restore checkpoint still replays alone, so Undo, and Undo of an Undo, keep their
  one-level-at-a-time behaviour.
