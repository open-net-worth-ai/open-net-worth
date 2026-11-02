---
title: Voice calls are a fast front voice over Lucius, spoken verbatim, in-app and vendor-neutral
status: Accepted
date: 2026-11-03
deciders: orchestrating Claude agent + repo owner
---

# Voice calls are a fast front voice over Lucius, spoken verbatim, in-app and vendor-neutral

## Context

The owner wants to talk to Lucius out loud. A proof of concept (LiveKit, run outside the app) made
every utterance a full Lucius turn: speech-to-text → Lucius → text-to-speech. A Lucius turn takes
5–25 s (a Claude Code start, the full chat context, thinking, and the chat's hold on short lines),
so the slow brain did the turn-taking: "hello", "go ahead" and "are you still there?" all waited on
it, and talking over it cancelled real work. The owner found it unusable. OpenClaw, Open Dot and
Hermes (its GPT-Live mode) all split a call into a fast front voice and the real agent behind it.

Forces: a finance assistant must never let a model paraphrase an amount; every write already has
one choke and one confirm mechanism (`CLAUDE.md` Write choke points, "One confirm mechanism for
every tool"); `always_allow` is never degraded; connectors read no environment variables and keep
credentials in `connector_secrets`; a feature flag's off path is the old app; and the owner does
not want to be bound to OpenAI Realtime or any single voice vendor.

## Decision

We will build voice calls as **two layers** inside the app, behind the `voice_calls` flag:

1. A **front voice** — a fast text model in a LiveKit pipeline (default Gemini 2.5 Flash-Lite via
   LiveKit Inference) — holds the conversation. It has **no data** and three tools:
   `ask_lucius(request)`, `lucius_status()`, `end_call()`. It never states a fact or a number and
   never claims a change is done; a bare "go ahead" is forwarded only as agreement to Lucius's own
   offer, never turned into an instruction.
2. **Lucius** runs every real request as a **normal chat turn on the conversation the owner has
   open**: `turns.spawn(..., cost_surface="web")` with `conversation.stream_turn`, exactly what
   `POST /api/chat` spawns. The chat panel follows it live, approval cards render there, and every
   permission and write choke applies unchanged. The voice instruction rides as a harness note,
   hidden from the transcript's bubbles. **Lucius's answer is spoken verbatim** (`session.say`).
3. The LiveKit worker runs **in the app process**, on its own event loop in a daemon thread (its
   jobs in threads), and hops to the app loop for turns (`connectors/voice/bridge.py`). It connects
   out to the owner's LiveKit project, so the app opens no port. Credentials live in the `voice`
   connector's secrets; the browser gets a 15-minute token for one fresh room, minted with stdlib
   HMAC so the API path needs no voice library.
4. **A call is only one the app started** (security review, 2026-11-03). The LiveKit project secret
   could otherwise dispatch Lucius into any room with any conversation id and read the book out
   to whoever is in it. So `POST /api/voice/call` records each minted room (room → conversation,
   15 min); the worker accepts a job only for a recorded room, takes the conversation from that
   record (never from dispatch metadata), claims it once, re-checks the flag and the connector on
   every job, listens only to the `owner` identity its token names, and accepts no typed text.
   A request reaches Lucius as the **owner's own transcribed words** since the previous one; the
   front model's reading rides in a harness note labelled as a relay, so a front model talked
   into `ask_lucius` by text it heard has no owner words to send and is refused. Requests are
   capped at 3 queued and 10 a minute; a call never starts a second turn beside a running one,
   never joins an Automation's conversation, and plain `ws://` is accepted only to this machine.
   Stopping the worker removes the LiveKit variables it copied into the environment.
5. Every piece is swappable behind a setting: transport (LiveKit Cloud or the self-hosted
   open-source server), speech-to-text, voice and front model. The voice libraries are optional
   (`requirements-voice.txt`).

## Consequences

### Positive
- Greetings, acknowledgements, "are you there?" and goodbyes answer in about a second; talking over
  the front voice is natural, and a request in flight is never cancelled by "sure".
- No second write path, confirm path or permission check: a call is the chat, spoken.
- Numbers are only ever Lucius's own words; the front model cannot invent one into the record.
- No new authentication path: the worker reaches turns in-process, not over loopback HTTP.

### Negative
- A second model is in the loop for the conversational layer — reversing the spec's first
  "Lucius is the only voice" call. Its side of the conversation crosses LiveKit and the model
  provider.
- LiveKit's worker writes `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` into the app
  process's environment. Every child the app spawns for a model or user code builds an
  allow-listed environment (`core.child_env`, the code sandbox, the MCP stdio client), so the secret
  does not reach them; a future subprocess that inherits `os.environ` wholesale would.
- The voice pipeline's CPU work (voice-activity detection) runs in the app process; a busy machine
  slows both.
- Real answers still take as long as a chat turn; this design hides the wait, it does not remove it.

### Follow-ups
- Phone numbers (inbound via LiveKit numbers, outbound via a SIP trunk), the caller allowlist and
  PIN from the phone-calls spec.
- A lean "phone" context and letting voice turns skip the short-line hold, to shorten Lucius's own
  answers.
- Front-model and speech providers on the owner's own keys (Anthropic, OpenAI, Deepgram…) and a
  local preset, beside LiveKit Inference.
- A "Test voice" check on the settings page that measures each layer's latency.

## Alternatives considered
- **One layer (every utterance a Lucius turn)** — rejected: built and tried; 5–25 s per reply made
  turn-taking unusable and barge-in cancelled work.
- **A speech-to-speech model (OpenAI Realtime / gpt-live) as the front** — rejected as the only
  option: it binds the product to one vendor (owner: "we can't be bound by OpenAI realtime voice").
  It may be offered later as one more front option.
- **A hosted voice-agent platform (Vapi, Retell, ElevenLabs Agents)** — rejected: they run the
  agent themselves and need a public webhook into the app.
- **The worker as a separate process talking to the app over HTTP** — rejected: it needs a new
  internal credential and auth path into `/api/chat`; in-process reuses the turn machinery and the
  existing auth boundary, at the cost of the environment note above.
- **Running the call as a chat connector (`connectors/processor.process_message`)** — rejected: its
  turns don't reach `live_sessions`, so the open chat panel would not show the call or its cards.

## Agent provenance

- **Decided by:** orchestrating Claude agent, with the owner choosing "rebuild it" after the POC.
- **Lane / branch:** `feat/phone-poc` (worktree `.claude/worktrees/phone-poc`).
- **Alternatives weighed:** one-layer cascade, speech-to-speech front, hosted platforms, out-of-
  process worker over HTTP, connector-processor turns.
- **Driving artifacts:** the phone-calls spec (`docs/superpowers/specs/2026-11-02-lucius-phone-calls-design.md`,
  local), the POC call sessions of 2026-11-03, source reads of OpenClaw, Open Dot, Hermes, LiveKit
  Agents and Pipecat.
- **Why this way (one line):** the conversation needs a fast voice, the numbers need Lucius's own
  words, and the safety needs the chat's single turn path.
