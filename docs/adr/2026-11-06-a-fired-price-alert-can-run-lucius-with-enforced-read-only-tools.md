---
title: A fired price alert can run a Lucius search, with an enforced read-only tool list
status: Accepted
date: 2026-11-06
deciders: repo owner (chose a full Lucius run over one server-side search) + Claude (implementation)
---

# A fired price alert can run a Lucius search, with an enforced read-only tool list

## Context

An alert's note is presented as instructions: the form calls it "Additional instructions", the
tool describes it as "what Lucius does when it fires", and the placeholder reads "Check news and
social media sentiment…". The code behind it was a single model call with no tools, built to
narrate a level that had been crossed (ADR `2026-07-20-disable-reasoning-on-mechanical-tasks`). A
watched-stock alert told the owner "I couldn't check news", and the owner went looking for an outage.
Nothing had been tried, and the app already held articles on the exact risk the note asked about.

The cached-news gaps behind that report were fixed separately. This is the remaining mismatch: a
note can ask for something only a run with tools can do.

## Decision

Behind the flag `price_alert_lucius_run` (default off, never in a public demo), a fired price alert
runs two steps in the background (`monitoring/price_alert_run.py`, seam in `price_monitor._fire_alert_condition`):

1. **A search turn.** One Lucius turn follows the note, or finds out why the price moved when there
   is none. It may use `web_search` and `x_search`, and nothing else.
2. **The same tool-free narration as before**, which reads the findings as fenced untrusted data
   and writes the usual short alert.

**The split is the security boundary.** The first version gave one turn the tools, the web text and
the position together. Security review showed why that was wrong: a page could steer a turn holding
the user's numbers. Now the turn reading strangers' text never gets the position, thesis, profile
or cached headlines, and the call holding those has no tools. The alert's note does reach the search turn,
because following it is the point, but every figure in it is replaced with `[figure]` first: a cost
or a share count never reaches a turn reading strangers' text. Wording in a note can still carry
something private.

**The tool list is a real ceiling, for this turn only.** An explicit offer is advisory everywhere
else: a call outside it still dispatches (ADR `2026-07-18-remove-layered-ai-permission-system`,
pinned by `test_explicit_offer_turn_does_not_auto_load_an_unoffered_tool`), and an unattended turn
has no card to stop it. So `_skill_scope.enforce_offer()` makes the tool loop refuse every other
name, before either dispatch branch, because an external MCP tool (a broker's order) skips
`registry.dispatch`. It also caps the turn at 8 tool calls, and raises `ToolCallLimitError` at 16
attempts, because a refused call starts another model round. This does not bring the removed ceiling
back: no other turn sets it.

**A ceiling only the host enforces needs a host that sees every call.** The Antigravity CLI runs
its own tools with no off switch, and Codex's text-protocol fallback leaves `web_search`,
`view_image` and sub-agents on. Both refuse an enforced turn, and the quick alert goes out. The
Claude CLI runs with `--tools ""`, and Codex's native mode turns its built-ins off.

**`fetch_page` is left out** because it would let a page name a URL for this server to request.

**Every narration is cleaned at one seam** (`_narrated_fields` → `clean_narration`), including the
quick one, which reads cached headlines and is what a failed search falls back to. The cleaner
(`text_sanitize.strip_unknown_links`) is shared with the delivery decider, so a rewrite can't put a
form back. It normalizes first and removes angle brackets before looking for links, then removes
markdown links, URLs, IPv4 addresses and handles, and defuses bare domains in any script. Telegram
links a bare domain and fetches it for a preview with no click, so `acme-filings.com/r?v=<value>`
would be a leak. Pushes also ask Telegram for no preview, as chat replies already did. A domain with
a path is reduced to its name; one with no path reads as its name or gets spaces (`J. P. Morgan`),
and an all-caps ticker with an exchange suffix (`ACME.DE`) survives. Word limits are enforced here.

**The run cannot lose the alert.** The story and the cooldown are written at once, from the same
quick narration as the old path. The push waits for the run and falls back to that narration on a
timeout (90 s search, 30 s narration), an error, an unusable reply or the daily cap. A SIGTERM kills
the process before asyncio cancels anything, so lifespan shutdown calls `price_alert_run.drain()`
before the Telegram bot stops: it stops the investigations, and each task then sends its quick alert.
It never cancels a task that is already sending. A later story write re-reads the row in one
transaction (`merge_fired_story`), so a dismissal made during the run survives, and it skips the
write if the alert has fired again since.

**The scan's `web_search_enabled` setting does not govern this.** That switch and its budget belong
to Intel scans. The owner chose this run knowing the setting was off, so gating on it would have
left the feature inert; the flag is this feature's switch.

## Alternatives rejected

- **One server-side search, then the same narration.** Cheaper, faster and with less to go wrong,
  and it was recommended. Rejected by the owner: a turn can follow a note such as "check social
  sentiment", where a fixed query can't.
- **One turn holding both the tools and the private context.** The first version; see above.
- **Await the run inside the price check.** One slow run would hold up every other ticker's
  evaluation, the problem `_instant_news_freshen` was already moved off that path for.
- **Send a quick alert, then a second message.** No delay, but two pings per alert on a day when
  several fire at once, against the owner's push for shorter alerts.
- **A durable Automation run.** Gives an inspectable transcript, but needs an Automation row per
  alert and a whole run lifecycle for a few sentences.
- **Blocking tools by name instead of allowing them** (`AutomationApprovalContext.blocked_tools`).
  External MCP names are not known in advance, so a block list fails open on exactly the tools that
  matter most.

## Consequences

- An alert can reach the phone up to two minutes later, on top of any delivery-decider tick. A real
  search took about 10 seconds.
- Each run costs a model turn, a narration and search quota, capped at 20 runs a day and 8 tool
  calls a run. The daily counter is in memory, so a restart resets it.
- A hard crash during a run loses that alert's push, though not its Intel story.
- `core.headless_turn` gained its only live caller (`services.self_harness` is gone), so this path
  has had little production use. The flag is the way back: the owner's plan is to test it on `main`
  and remove the flag if it holds up, or default it off for releases while it improves if not.

The pushed text is recorded as Lucius's own message in the Telegram session, unfenced, like every
other Intel push. This is the owner's decision, not a gap: the narration did write it, so it isn't
treated differently. A page can still shape that wording, and a later turn with full tools reads it.

**Known gaps:**

- Searches still reach a third party, and with `search_reads_results` on, result pages are fetched
  from the owner's server, so a page that ranks learns the server's address and when the alert
  fired.
- An owner's Ask setting on a search tool does not stop this turn: the `scheduled` surface cannot
  show a card, so the tool runs. `blocked` is honoured.
## Review on merged main, 2026-11-06

The owner's rule: if testing on `main` holds up, remove the flag; if not, keep it so a release is
unaffected while this improves. The first pass held for the tool ceiling and the private-data split,
including live against pages written to hijack a real model, but review found the output cleaning
beatable, a push lost on SIGTERM and a late story write undoing a dismissal. So the flag defaulted
off and the findings were fixed in the decisions above. It stays off until a review of the fixes.

Retire the flag by 2027-03-01: keep the run and remove the narration-only branch, or delete this.

## Agent provenance

Claude (Opus 5.5), interactive session on `main`, following an owner report of 2026-11-05.
