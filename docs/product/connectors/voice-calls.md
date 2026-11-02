---
title: Voice calls
description: Talk to Lucius out loud from the chat. A fast voice keeps the conversation going while Lucius does the real work, with the same approvals.
category: connectors
order: 35
audience: [user, operator]
status: beta
related: [connectors/telegram, lucius/confirm-cards, lucius/permissions, operations/security-and-privacy]
keywords: [voice, call, talk, speak, microphone, livekit, phone, speech, hands-free]
---

# Voice calls

> **Experimental.** Voice calls are off by default. Turn them on in **Settings → Developer →
> Experimental → Voice calls**. While the switch is off, nothing on this page appears in the app.

Press **Call** in Lucius's chat and talk instead of typing. Lucius answers out loud, looks things
up, and proposes changes exactly as he does in the chat — and the call shows up in that same
conversation as it happens, so you can read along and approve cards on screen.

## How a call works

A call has two voices working together:

| Who | What it does |
|---|---|
| **The front voice** | A fast model that keeps the conversation natural: it greets you, answers "are you still there?", lets you interrupt, and says goodbye. It answers in about a second. It **never sees your data** and **never states a number or claims something is done**. |
| **Lucius** | Does every real thing — balances, spending, budgets, investments, changes — with the same tools, permissions and approval cards as the chat. His answers are **read out word for word**, so an amount is never re-phrased. |

When you ask for something real, the front voice hands it to Lucius and says something short
("Sure, one sec"). Lucius's question and answer appear in the chat. If a lookup is genuinely slow,
you hear *"the live prices lookup is taking longer than usual"* — once, and only when it's true.
Ask *"what are you doing?"* and the front voice tells you what Lucius is working on right now.

**Changes still need your approval.** When Lucius proposes one, he says so, and the approval card
appears in the chat on your screen. Tap **Approve** and he tells you on the call when it's done.
Your [permission settings](../lucius/permissions.md) apply exactly as they do in the chat.

Say *"bye"* or *"that's all"* and Lucius says goodbye and hangs up. Closing the tab ends the call
too.

## Set it up

Voice calls run on your own [LiveKit](https://livekit.io) project. LiveKit carries the call's
audio and runs the speech-to-text, the voice and the front voice model, so there is no other
account to create. Its free plan includes a monthly allowance.

1. **Install the voice libraries** on the machine Open Net Worth runs on, then restart the app:

   ```bash
   pip install -r requirements-voice.txt
   ```

2. **Create a LiveKit project** at [cloud.livekit.io](https://cloud.livekit.io) and, in its
   **Settings → Keys**, create an API key. Keep the **URL** (`wss://…livekit.cloud`), the **API
   key** and the **API secret**.
3. In Open Net Worth, open **Connectors → Voice calls** and paste the three values. Save.
4. When the page says **Ready for calls**, press the **phone** button in Lucius's chat. Your
   browser asks for the microphone the first time.

The browser only allows the microphone on a secure address: `localhost` on your own computer
works; from another device the app must be reachable over HTTPS (see
[Exposing beyond localhost](../operations/exposing-beyond-localhost.md)).

## Settings

| Setting | What it changes |
|---|---|
| **Front voice** | The model that holds the conversation. *Gemini 2.5 Flash-Lite* is the default: it was the fastest in testing and the only one that asked "go ahead with what?" instead of guessing. |
| **Lucius on calls** | The model Lucius answers with during a call. Unset, he uses the conversation's own model. A faster one keeps the waits short. |
| **Speech to text** | Which service turns your voice into text. |
| **Voice** and **Voice id** | Which service speaks, and which of its voices (blank = its default). |

The LiveKit key and secret are stored like every connector credential: never shown to Lucius,
readable back only by you through the eye on the field. Pausing the connector stops taking calls.

## What reaches whom

- The call's audio, the front voice's side of the conversation, and Lucius's spoken answers cross
  your LiveKit project and the speech and model services it runs.
- Your book itself stays with Lucius, as it does in the chat. The front voice has no access to it.
- The conversation is saved like any chat with Lucius.

## Troubleshooting

**The Call button isn't there.** Voice calls are off: turn them on in **Settings → Developer →
Experimental**.

**"Set up Voice calls in Connectors first."** The connector has no LiveKit URL, key or secret yet,
or it is paused.

**"The voice worker isn't running."** Open **Connectors → Voice calls**: it says why — usually the
voice libraries aren't installed yet (step 1 above), or the LiveKit details are wrong.

**Lucius takes a while to answer.** The front voice answers at once; Lucius's own answer takes as
long as a chat reply. Pick a faster model under **Lucius on calls**, and close heavy programs —
a very busy computer also slows how fast the call notices you've stopped talking.

## Related pages

- [Telegram](telegram.md) — chat with Lucius from your phone
- [Confirm cards](../lucius/confirm-cards.md) — approving what Lucius proposes
- [How Lucius's permissions work](../lucius/permissions.md)
