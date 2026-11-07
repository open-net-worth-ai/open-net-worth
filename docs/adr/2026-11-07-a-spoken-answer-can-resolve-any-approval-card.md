---
title: A spoken answer can resolve any approval card
status: Accepted
date: 2026-11-07
deciders: Claude (voice calls session) + owner
---

# A spoken answer can resolve any approval card

Builds on `2026-11-03-voice-calls-are-a-fast-front-voice-over-lucius`.

## Context

On a real call (2026-11-06) the owner asked for two approvals out loud. The Auto judge allowed one and
asked for a card on the others, the same request behaving differently each time. A card was only
announced as being on screen, and the rest of the owner's sentence, arriving as a new message,
cancelled it (`_abort_stale_pending`). The owner wants a call they can finish without looking at a
screen, and accepts the risk knowingly: "it's the best user experience".

Separately, the same call's front voice said a charge was approved that wasn't, with an invented
amount and date. Its prompt forbade that, so a prompt isn't enough.

## Decision

We will let a spoken answer confirm or reject ANY card on a call. It takes the same path as a tap
(`desk.answer_card`, the twin of `POST /api/chat/{id}/resolve`), so the app runs what the card stored,
and neither a voice nor a model can change it.

A card is read out word for word (`session.say`, `cards.card_reading`), with no model in between, built
from what its screen version shows: amounts, recipients, named transactions. It has to be ALL of what
the card does: one with an id we can't name, a long list cut short, or text past the limit can't be
approved by voice and needs a tap. After a second answer too soon to count, so does any card, as a noisy
line would otherwise never let one through.

The worker accepts a spoken yes only if all of these hold:

- That exact card was read out, to the end, with no failure, and only the voice's re-ask of it has
  been said since: a "yes" to anything else mustn't reach it.
- The words' own start came after the voice last stopped, plus a margin. Not the latest sound: a cough
  afterwards mustn't date words that began during the reading.
- The whole answer is on the allowlist of plain yeses for the call's language or English
  (`cards.is_plain_yes`). The front
  model only says which way it thinks they went, and its reading is never enough: text read out on
  the call reaches it.

Rejecting needs only their own words, as it changes nothing.

The app takes the card only if it is still open: its pending row exists, belongs to this conversation
and isn't an Automation's, and its tool call is still open at the transcript's tail (one of two
stays open once the other has its result). A row for anything else is
dead, as approving it would run the tool with its result nowhere to go.

While a card waits, a request waits with it, so the owner's words can't cancel it, and the app refuses
one that gets through (`desk.ask`, `card_open`). Small talk with a card open stays on it, never after it.

We will enforce what the front voice may say in code (`FrontAgent.llm_node`). Whatever it writes, except
lines of our own, is held until complete, and if it states a figure or claims anything happened, or is
long, it's never spoken. A reply to the owner then goes to Lucius.

## Consequences

### Positive

- A call can review and approve from start to finish, hands-free.
- The front voice is far less able to state an amount or claim an action on its own. Digits and length
  are refused in any language; spelled-out figures and claims only in English and Vietnamese, by word
  list. A paraphrase can still get through, so this narrows the risk, and doesn't remove it.

### Negative

- **This lowers the bar for every Ask tool, moving money included, for the length of a call.** Someone
  else's voice near the phone counts as the owner's. A mishearing could turn into a yes, though only
  within the checks above. There is no speaker verification.
- **A compromised worker process can now approve a card** on a conversation it holds a call for, as the
  checks on the owner's words run there. Until now it could only start requests. The app still checks
  the card is open and belongs to that conversation, and rate-limits answers with requests.
- A language with no allowlist can only approve by a tap. Some real yeses aren't listed, and get asked
  again.
- A card is read in its own words, usually English, whatever language the call is in.
- A yes said over the end of the reading is asked again.
- A reply from the front voice waits for the whole of itself, adding a little to small talk's first sound.
- The owner's top merchants go to the speech provider as hints. The product docs say so.

### Follow-ups

Consider a setting to require a tap for cards above some size, if the owner wants one.

## Alternatives considered

- **Voice for reviews only, a tap for the rest:** safer, and the agent's recommendation. The owner
  chose every card.
- **A spoken code for money movement:** safer, but breaks the point of talking on a call.
- **Let the new message cancel the card and re-propose:** how the cancelled approval happened.
- **Have the model confirm through a tool that runs the write:** bypasses the stored arguments.
- **Trust the model's reading with a blocklist of "no" words:** built first. The security review approved cards on "hmm, give me a sec" and "nein, warte".
- **Have the model word the card:** built first. A merchant's name could change the question asked.
- **Make the front voice templates only:** can't invent anything, and sounds like a phone menu.

## Agent provenance

- **Decided by:** the owner, for every card; Claude, voice calls session, for the checks, on branch
  `voice/call-log`.
- **Driving artifacts:** the owner's call of 2026-11-06 and the transcript and worker log behind it.
- **Why this way:** resolving through the tap's own path keeps one approval mechanism, and every check
  is on the owner's words, not on how a model read them.
