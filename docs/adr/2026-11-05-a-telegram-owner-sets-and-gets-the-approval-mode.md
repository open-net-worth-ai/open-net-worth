---
title: A Telegram owner's turn follows the approval mode, and the owner can set it from the bot
status: Accepted
date: 2026-11-05
deciders: orchestrating agent (Claude) + repo owner
---

# A Telegram owner's turn follows the approval mode, and the owner can set it from the bot

## Context

A conversation stores an approval mode (`manual`, `auto`, `skip`), read once per turn by
`turns._drive`. A channel turn does not run through `_drive`: `processor.pump_channel_turn` renders
it. So the mode was never bound there. A Telegram thread stored as Auto showed a card for every Ask
tool and the judge never ran, while the app's picker said Auto (owner report, 2026-11-05).

Binding it everywhere was not the answer. Every new conversation is stored as Auto, so that would
have quietly loosened approvals for every channel at once, shared workspaces included. A Telegram
bot's "operator" trust is not a statement about the sender either: a bot on the `open` policy, or an
allowed group, puts strangers behind the same `trust_level`.

The mode was also browser-only, on the reasoning that a model able to set its own mode turns a
prompt injection into an approvals-off switch. The owner asked for it in the bot's command menu.

## Decision

- `pump_channel_turn`, the one path of both a message and a confirm tap, binds the mode only when
  the adapter's `is_owner_chat` says so. Nothing bound means a card, and any failure lands there.
- On Telegram the owner is the private chat of a bot with exactly one approved person. The access
  policy is not consulted. A group is never one, and approving a second person puts everyone back
  on cards, because pairing and manual entry write the same list and nothing tells them apart. No
  other adapter implements it, so they keep their cards.
- It needs `permissions_v2`. Auto there is a per-call judge; the old Auto lets nearly every write
  through, which nobody chose for a phone.
- `/approvals` sets the mode through the one writer, `services.conversation_approval.set_mode`. A
  tool still cannot reach it: the rule was always "human-only", and the browser was how we had
  said so. It offers `manual` and `auto`, not `skip`: a card shows the call and Always allow names
  one tool, while `skip` is a blanket pass, third-party MCP tools included, that no card can grant.
  A thread already on `skip` from the app still follows it.
- `/new` keeps `manual`, since a new conversation otherwise starts in Auto, and ends `skip`.
- A Deny tap keeps the rest of that turn on cards. The judge cannot see a refusal, so a retry would
  otherwise be judged from the words the owner just said no to.
- The judge reads only what the owner said. A reply's quote and a forward are fenced and left out:
  either can relay a fetched page or a scammer's request, and the owner's words are its only source
  of intent. A photo's or document's caption is read, past the attachment blocks in front of it.
- A suggestion chip starting with `/` is dropped: a model-written chip must not be one tap from a
  command.

## Consequences

### Positive
- Telegram does what the app's picker says, and the setting lives where the other bot settings do.

### Negative
- An owner who approves a second person loses Auto on Telegram, and has to read `/approvals` to
  learn why. Safer than guessing who is who, but it is a real surprise.
- "yes" under a quoted message, or a forward with no words of the owner's, tells the judge less, so
  such a turn asks more often. A forward is also relayed to Lucius as data, so "book this" has to
  be typed.
- The Deny rule covers the channel path only. A web retry after a Deny still reaches the judge.

### Follow-ups
- Slack, Discord and WhatsApp have no owner yet, so their picker value is still not followed.
- A designated owner id, set in the app, would let a shared bot keep Auto for the owner.
- The web path needs the same Deny rule.

## Alternatives considered
- Bind the mode for every channel — rejected: it widens shared workspaces silently.
- Use `trust_level == operator` as the owner test — rejected: it describes the connector, not the
  sender, and is true for a stranger on an open bot.
- Keep the mode browser-only — rejected by the owner: it belongs with the bot's other settings.
- Offer `skip` on the bot — rejected: external MCP cards carry no Always allow, so it would grant
  from one tap what the Connectors screen alone can grant.
- Treat anyone on the allowlist as the owner — rejected: the owner asked for owner only, and a
  person approved through pairing would get Auto in a shared book.

## Agent provenance

- **Decided by:** orchestrating Claude agent, on the owner's "owner only" and "have that as a config"
- **Lane / branch:** `main-work`, pushed to `main`
- **Alternatives weighed:** every channel, operator trust, browser-only, skip on the bot, allowlist as owner
- **Driving artifacts:** owner's Telegram thread, 2026-11-05; `app.log` showing cards with no judge; security review of this change
- **Why this way (one line):** the owner's private chat gets what the picker says; everyone else's
  behaviour does not change.
