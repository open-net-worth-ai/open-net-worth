---
title: A run's behaviour lives in its instructions, and the app only supplies facts about runs
status: Accepted
date: 2026-11-05
deciders: orchestrating agent (Claude) + repo owner
---

# A run's behaviour lives in its instructions, and the app only supplies facts about runs

## Context

Every Automation run was told it had never run. `materialize_automation_session` wrote the opening
message from a request built by hand, without `last_completed_run` or `configured_trigger`, and that
message is the only opening a run ever gets. All 100 stored runs that carry the field show `null`.

The Email finance digest repeated a week of alerts because of it. Reading the bug also showed two
design faults in the skill. It told each email once by comparing `received_at` with the last run's
start, so anything a run skipped, or failed to deliver, was thrown away by the next one. And it knew
how it was being run: internal field names, "on a scheduled run", the named send tools. A skill also
used from a chat has no business knowing those, and "read back to the run before last" is a rule
about runs, not about judging email.

## Decision

Three layers, each knowing only its own part:

- **Skill:** what matters and how to write it. It takes three inputs, each with a default: the period
  to read (else the last 3 days), what they've already been told (else nothing), and where to send
  it (else the message is the reply). It never asks how it was started.
- **Automation instructions:** how this Automation behaves. The digest template says to read since
  the run before last, leave out what the last run told them, and tell them again what didn't reach
  them.
- **App:** facts about runs, for every Automation, knowing nothing of email. `run_context` adds
  `run_before_last` and `last_run_record` (the summary, plus each `<connector>_send_message` message
  and whether it arrived). `materialize_automation_session` now uses the one `request_for_run`.

"Did I already tell them this?" compares content, so overlap is how a missed email gets a second
chance rather than a repeat. Reading back to the run before last gives every email two reads on any
schedule, and one run's record is always enough to cover them.

## Consequences

- A fix to the instructions does not reach an existing Automation, as a skill fix does. Each must be
  updated, which is acceptable with one user today and is a real cost once there are more.
- The comparison is the model's. A repeat is less likely, not impossible.
- The record is not tied to a channel. A run that sent nothing has an empty `sent`, which is itself
  information, and only the size-capped text is added to each run's first message.
- `log-transactions-from-email` still reads `last_completed_run.started_at` and says it runs on a
  schedule. Its behaviour works and was left alone, so it does not yet follow this split.

## Alternatives rejected

- **The time cutoff, with the date fixed.** It stops repeats but loses anything skipped.
- **A 3-day window for everyone.** A guess, and it would need about five runs' records at 12 hours.
- **Changing the logging skill's window to match.** It works, so the digest searches for any part of
  its period that logging's fetch doesn't cover instead.
- **The app filtering already-told mail in code.** Stricter, but the app would have to know about
  email. Worth revisiting only if real runs still repeat.
- **Recording which emails a run opened.** It would save re-opening them, but it is email knowledge in
  a generic layer.

## Agent provenance

Made by the orchestrating agent (Claude) in the main checkout, with the owner directing the split:
the skill must not be tied to Automations, and run behaviour belongs in the instructions.
