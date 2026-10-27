# Sorting an existing book's memory — the owner's order

The memory redesign (ADR `2026-10-25-memory-keeps-only-what-has-no-other-home`) stamps every
fact written before it `unsorted`. The memory model then files each one into a section and topic,
or retires it. On a book with real memories, review that sort before it is written:

1. **Stop the app** (the `:8080` instance, the desktop app or the boot service).
2. **`make db-upgrade`** — applies revision `202610271200` (with a backup), which stamps the
   legacy facts `unsorted`.
3. **`.venv/bin/python -m backend.scripts.migrate_memory_topics`** — the dry run. It asks the
   memory model and prints one line per fact (`id | section · topic | new text`, or
   `id | retire: reason`). Nothing is written.
4. **Review** every line. If a decision is wrong, fix the fact after the sort (edit, forget, or
   tell Lucius) or run the dry run again.
5. **`.venv/bin/python -m backend.scripts.migrate_memory_topics --apply`** — files and retires the
   facts through the memory write choke.
6. **Start the app.**

**Why in this order:** the background sort starts at boot. The memory sweep runs in the worker
that holds the scheduler lock, and its first pass sorts every unsorted fact. If you start the app
before step 5, the model has already filed and retired the facts before you can review a dry run.
Turning memory off first does not help, because the script needs "Generate memory from chats" on.

Both steps need "Generate memory from chats" on and a memory model that answers. A retired fact
stays under **Moved out of memory** for 30 days. For a scratch run, set `ONW_ROOT` and
`ONW_QA_MODE=1`.
