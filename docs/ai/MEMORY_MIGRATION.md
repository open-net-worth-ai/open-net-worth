# Sorting an existing book's memory

The memory redesign (ADR `2026-10-25-memory-keeps-only-what-has-no-other-home`) stamps every
fact written before it `unsorted`. The memory model then files each one into a section and topic,
or moves it out of memory.

## The normal path: nothing to do

Upgrade and start the app as usual — this is what every user, the owner included, goes through.

1. On boot, the database migrations run: `202610271200` (with a backup) stamps the legacy facts
   `unsorted`; `202610271800` adds the forgotten-fact fingerprints and renames a stored You topic
   "About you" to "Profile"; `202610272100` adds the topic summaries.
2. The memory sweep's first pass (in the worker that holds the scheduler lock) sorts every
   unsorted fact in one go — about 25 facts per model call, back to back, so a book of ~130
   facts takes about a minute — then writes the topic summaries.
3. Every later pass finds nothing unsorted and does nothing. A fact the model skipped is offered
   once more in the same pass, then again an hour later.

Until a fact is filed it shows under **Older memories — being sorted** in Settings → Memory; it
can be edited or forgotten there, and `recall_memory` finds it. A fact moved out is kept 30 days
(not shown), then deleted. It needs "Generate memory from chats" on and a memory model that
answers; with memory off, the facts wait unsorted until it is turned on.

## Optional: preview the sort by hand (operators)

`.venv/bin/python -m backend.scripts.migrate_memory_topics` prints the plan — one line per fact,
`id | section · topic | new text` or `id | retire: reason` — and writes nothing; `--apply` runs
it. Because the sweep sorts at boot, a preview is only meaningful with the app stopped: stop it,
`make db-upgrade`, run the preview, then `--apply` or just start the app. For a scratch run, set
`ONW_ROOT` and `ONW_QA_MODE=1`.
