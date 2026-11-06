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

> **Experimental.** Voice calls are on by default, and nothing happens until you connect a LiveKit
> project. To hide them, turn off **Settings → Developer → Experimental → Voice calls**. While the
> switch is off, nothing on this page appears in the app.

Press **Call** in Lucius's chat and talk instead of typing. Lucius answers out loud, looks things
up, and proposes changes exactly as he does in the chat — and the call shows up in that same
conversation as it happens, so you can read along and approve cards on screen.

## How a call works

A call has two voices working together:

| Who | What it does |
|---|---|
| **The front voice** | A fast model that keeps the conversation natural: it greets you, answers "are you still there?", lets you interrupt, and says goodbye. It answers in about a second. It **never sees your data** and **never states a number or claims something is done**. |
| **Lucius** | Does every real thing — balances, spending, budgets, investments, changes — with the same tools, permissions and approval cards as the chat. His answers are **read out word for word**, so an amount is never re-phrased. |

**The whole call is in the chat** — the greeting, small talk the front voice answers itself, and
every request with Lucius's answer — so you can scroll back through it later, and Lucius knows
what was said on the call next time you talk.

When you ask for something real, the front voice hands it to Lucius and says something short
about what you asked (*"okay, let me check your grocery spending"* — never a promise of how long it takes, or a hint of the answer). Lucius's question and answer appear in the chat. If a lookup is genuinely slow,
you hear that it's taking longer than usual — once, and only when it's true. After about 10 seconds
of hearing nothing, he tells you he's still on it (*"still working on your grocery spending"*), then
again every 30 to 45 seconds until the answer starts. He stays quiet while you're talking or after
you've asked him to wait. These lines repeat what you asked, never a figure. To cut him off, say at
least two words (*"hold on"*, *"stop there"*): a lone *"okay"* or background noise doesn't.
Correct him (*"no, I meant the other card"*) and he moves on to what you meant, skipping the first
answer if it hasn't been read out yet; he also knows you never heard it. Asking something different
keeps the first answer, in order. A change can't be taken back this way: if the first request did
change something, you always hear how it went, then he gets to your correction.
Only his final reply is read out. A link in it shows in the chat: inside a sentence you hear just its
name, and on a line of its own you don't hear it at all.
Ask *"what are you doing?"* and the front voice tells you what Lucius is working on right now.

**Changes follow your usual approvals**, which depend on the conversation's mode and the tool's
permission. When one waits for you, he says so, and the approval card appears in the chat on your
screen. Tap **Approve** and he tells you on the call when it's done.
Your [permission settings](../lucius/permissions.md) apply exactly as they do in the chat.

**He can look at your screen.** Ask *"what am I looking at?"* and he reads the screen open in the
app, whether or not [Screen awareness](../lucius/mentions.md#screen-awareness--lucius-sees-what-youre-looking-at)
is on: that switch only decides what goes along with a typed message. On a phone, fold the full-screen call away first, so there's a page for him to
read. If he can't see one, he says so.

You only ever talk to Lucius: the front voice speaks as him, so you'll hear *"let me check"*,
never *"let me ask Lucius"*.

Say *"bye"* or *"that's all"* and Lucius says goodbye and hangs up. Closing the tab ends the call
too. If the line goes quiet for about 20 seconds he asks *"are you still there?"*, and after
another 20 he says goodbye and hangs up — never while he's still working on something for you or
a change is waiting for your approval, unless the line has been silent for five minutes (his work
and the card carry on in the chat). A call also closes as soon as your phone drops off for good,
and after an hour at most. Need a moment? Say *"hold on"* or *"give me two minutes"*
and he waits quietly for as long as you asked (up to 10 minutes) before checking in.

## Set it up

Voice calls run on your own [LiveKit](https://livekit.io) project. LiveKit carries the call's
audio and runs the speech-to-text, the voice and the front voice model, so there is no other
account to create. Its free plan includes a monthly allowance. Everything voice calls need comes
with Open Net Worth on a Mac with Apple Silicon (macOS 14 or later), Linux (x86_64 or aarch64) and
Windows x64; on any other computer the settings page says calls aren't available there.

1. **Create a LiveKit project** at [cloud.livekit.io](https://cloud.livekit.io) and, in its
   **Settings → Keys**, create an API key. Keep the **URL** (`wss://…livekit.cloud`), the **API
   key** and the **API secret**.
2. In Open Net Worth, open **Connectors → Voice calls** and paste the three values. Save.
3. When the page says **Ready for calls**, press the **Call** button (four voice bars) in Lucius's chat — it sits
   where **Send** is while the message box is empty. Your browser asks for the microphone the
   first time.

## During a call

The call has its own view, with an **orb** that moves with whoever is talking: it swells and
glows with whoever is talking, you as much as Lucius, and the colour inside keeps swaying when
nobody is. While he looks something up it moves faster and the line under it says
*Lucius is working…*. Above the bottom row is the mic button to mute, and along the bottom are smaller buttons to attach a file, choose the model, type and end the call. A call uses the conversation's own model, the same one you see in the chat. Changing it there or on the call changes it for that conversation, without restarting the call: his next answer uses it. The gear at the top right opens Voice settings. A file you attach goes straight to Lucius and you stay on the call, where he answers it. To go back to the chat, swipe down or tap the arrow: the call carries on. You can mute from the moment you press
**Call**, while it's still ringing, and stay muted when he picks up. A short tone falls when you mute
and rises when you unmute. On a phone's full call screen
the light shows whose turn it is: a glow rises from the bottom while you talk, and the screen's edges
light up while he does, with a wave from the orb as he starts to answer. If you talk over him, the
mic button lights up.

To change language or who speaks during a call, use the gear at the top of the full call screen, even while the call is still starting, or
change it in Voice calls settings. A call picks its language when it starts, so changing it hangs up
and calls back in the same conversation, which takes a few seconds.

Lucius keeps a spoken answer to a sentence or two and offers more if there's more. Ask for the detail
when you want it.

| Where | What you see |
|---|---|
| **On a phone** | Pressing **Call** in the Lucius chat opens the full call screen. The arrow at the top (or the keyboard button) folds it into a strip above the message box so you can read the chat (approval cards included) or type, while you keep talking. The strip's expand button opens the full screen again. |
| **On a phone, away from the Lucius tab** | A small round button sits above the tab bar. With no call on it shows **voice bars**: tap it to call, or to go to Voice calls in Connectors if calls aren't set up. During a call it grows into a small pill with the moving **orb** and the time, or what's going on instead (*Working…*, *Poor connection*, *Reconnecting…*): tap it to open the full call screen, where **End** is. |
| **In the side panel or the Lucius tab** | The call sits just above the message box — a slim strip on a wide screen — so the chat above stays readable and its cards tappable. |
| **The floating Lucius button** | It has a **Call** button (four voice bars) beside **Ask Lucius**: press it and the call happens right in the button. It grows to show the orb, the time, **Mute** and **End**, and shrinks back when you hang up. Press the time to open the chat. On a poor connection it says so and shows the signal bars. If Lucius needs your approval it says **Needs approval**; press **Review** to see the card. |

Folding it never hangs up. If a call can't start or drops, a note at
the top of the box (or in the floating button) says why, with a button to try again, open Voice
calls settings or view the app's logs; dismiss it with **×**. That includes Lucius's side going
away mid-call (the app restarting, say) and a call nobody picks up within 30 seconds — the call
ends and says so instead of staying open, with a link to Voice calls settings. If LiveKit refuses
the call because your LiveKit project is out of inference credit (or hit its rate limit), the note
says exactly that, with a link to LiveKit Cloud to check your plan and billing. (LiveKit
only says the credit ran out — not whether or when a free allowance comes back.) LiveKit counts participant minutes per person on the call, so each call minute is two
— yours and Lucius's.

**You can type during a call, too.** The message box stays under the call: type a question or
attach a file, and it goes to Lucius like any other message — and he reads his answer aloud on the
call, as well as showing it in the chat. The same goes for a change you approve on screen: what
happens next is read out.

**Lucius answers out loud.** Ask for a list and he says the top few and how many there are, then
offers the rest. He only uses the screen for changes waiting on your approval, or when you ask to
see something: then he says it's there and shows it as a table, without reading the table out.

**In the Open Net Worth desktop app**, the first call asks macOS for the microphone (once — it
remembers). The desktop app allows the microphone for the app's own page only, and only audio;
this needs desktop version with voice-call support or later. If you said no, macOS won't ask
again: the call says so and offers **Open System Settings**, which opens **Privacy & Security →
Microphone**, where you turn Open Net Worth on. macOS then asks to quit and reopen the app: choose
**Quit & Reopen**, and it comes back ready for your call.

The browser only allows the microphone on a secure address: `localhost` on your own computer
works; from another device the app must be reachable over HTTPS (see
[Exposing beyond localhost](../operations/exposing-beyond-localhost.md)).

## Settings

| Setting | What it changes |
|---|---|
| **Front voice** | The model that holds the conversation. *Gemma 4* is the default: it's LiveKit's replacement for the retired Gemini 2.5 models, and in testing it asked what a bare "sure" was agreeing to instead of guessing. A setting saved with a retired model moves to it by itself. |
| **Language** | What you speak on calls: English, Vietnamese, Chinese, Japanese, Korean, Hindi, Indonesian, Spanish, French, German or Portuguese. Lucius hears it, his greeting and check-ins are in it, and his voice speaks it. |
| **Speech to text** | Which service turns your voice into text. *Automatic* uses Deepgram Nova-3, which hears every language here. The AssemblyAI models hear fewer (*Universal* hears English only), so picking a language they can't hear moves you to Automatic. All of these are told the common finance words to expect in your language, so they mishear them less. Those are general words: nothing from your own accounts goes with them. |
| **Voice model** | Which service speaks and what it costs. *Inworld 2.0 Flash* is the default for a new setup: it's the cheapest model that speaks every language here. A setup you've already saved keeps its choice until you change it. Only models that speak your language are offered. |
| **Voice** | Who speaks: a sample of male and female voices, marked with the gender the docs state. The default is a calm male voice where one is documented (*Dennis* on Inworld), and a setup with no voice picked uses it. Pick **Another voice…** to paste an id from that service's library, which has many more. Picking another service goes back to its default voice. A voice may not sound natural in every language, so try it with a short call. |

**What a call costs.** Lucius's voice is most of it, and it's charged by the character, so the price
beside each voice and the length of his answers matter most. Settings shows an estimate for the voice
you pick: on LiveKit's free $2.50 credit, about 80 minutes at $50 per million characters, about 140 at
$25 and about 200 at $15. A call that talks more uses it faster, and LiveKit sets the prices.

The LiveKit key and secret are stored like every connector credential: never shown to Lucius,
readable back only by you through the eye on the field. Pausing the connector stops taking calls.

## What reaches whom

- The call's audio, the front voice's side of the conversation, and Lucius's spoken answers cross
  your LiveKit project and the speech and model services it runs.
- Your book itself stays with Lucius, as it does in the chat. The front voice has no access to it.
- The conversation is saved like any chat with Lucius.

## On the public demo

The public demo lets visitors call Lucius too, about its fabricated book, with limits so the
demo stays affordable:

- a call lasts at most **10 minutes** — Lucius says when time is nearly up, then says goodbye;
- each visitor gets **3 calls and 15 minutes of calls a day**;
- the whole demo has **120 minutes of calls a day**, and takes **2 calls at once**.

A call goes into your own conversation and counts toward your daily AI allowance like a typed
message. When a limit is reached, the call doesn't start and the chat says which limit it was.
Your own copy of Open Net Worth has none of these limits.

## Troubleshooting

**The Call button isn't there.** Voice calls may be switched off: check **Settings → Developer →
Experimental**. Or they aren't set up yet: until then the message box keeps **Send**, and the **Call** button
on the floating Lucius button takes you to Voice calls in Connectors. It also gives way to **Send** as soon as you type.

**The button says "Set up voice calls".** Voice calls are on but not set up yet: pressing it opens
the Voice calls settings right where you are.

**"A call can't join an Automation's conversation."** An Automation's conversation continues on its
own, so a call can't join it. Press **Start a new chat**, then **Call**. On a phone's other tabs,
**Open Lucius** takes you to the chat first. The message stays with that conversation: it doesn't
follow you to another one.

**"Calls are ready."** The page had to reload before it could reach your voice server, for example
because it was already open when calls were set up on another device. Press **Call**.

**"Your browser blocked the connection to your voice server."** Reloading didn't help, so something
in front of the app, such as a proxy or an extension, is refusing the connection. **Details** has
the browser's words.

**"Couldn't connect to your voice server."** Your internet is down, or the LiveKit URL, key or secret
in Voice calls settings is wrong. **Details** has LiveKit's words.

**"Set up Voice calls in Connectors first."** The connector has no LiveKit URL, key or secret yet,
or it is paused.

**"Voice calls are starting up."** The part of the app that answers calls wasn't running when you
pressed Call. It retries by itself, and pressing **Call** or **Try again** starts it again, so try
again in a few seconds. If it keeps saying this, open **Connectors → Voice calls**: it says why —
usually the LiveKit details are wrong, or this computer is one voice calls aren't available on.

**A call dropped, then voice calls came back on their own.** The voice side of the app runs
separately from the rest of it, so a crash there ends the call but never the app; it restarts
itself within a few seconds. Call again.

**The call says the connection is poor.** A call runs from your phone through LiveKit to the computer
running the app, so any part of that path can break it. *Your connection dropped* is the device you're
holding. *Poor connection. Lucius may not hear you.* is anywhere past it: the computer running the
app, or a speech service having a moment. While it shows, anything you said may need repeating. The
**signal bars** on the call rate the weaker of the two connections, yours and Lucius's, so
they show when a network is struggling. If it keeps happening, the computer's network is the first
place to look, and a wired connection is the surest fix: a computer that keeps switching between wifi
bands drops calls even with a strong signal.

**Lucius takes a while to answer.** The front voice answers at once; Lucius's own answer takes as
long as a chat reply. Pick a faster model with the model button on the call, and close heavy programs —
a very busy computer also slows how fast the call notices you've stopped talking.

## Related pages

- [Telegram](telegram.md) — chat with Lucius from your phone
- [Confirm cards](../lucius/confirm-cards.md) — approving what Lucius proposes
- [How Lucius's permissions work](../lucius/permissions.md)
