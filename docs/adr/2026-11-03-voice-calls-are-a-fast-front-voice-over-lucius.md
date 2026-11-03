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
   LiveKit Inference) — holds the conversation, speaking AS Lucius in the first person (it never
   mentions anyone behind it — "let me ask Lucius" confused the owner, 2026-11-03). It has **no
   data** and three tools: `work_on_request(request)`, `check_progress()`, `end_call()`. It
   never states a fact or a number and
   never claims a change is done; a bare "go ahead" is forwarded only as agreement to Lucius's own
   offer, never turned into an instruction.
2. **Lucius** runs every real request as a **normal chat turn on the conversation the owner has
   open**: `turns.spawn(..., cost_surface="web")` with `conversation.stream_turn`, exactly what
   `POST /api/chat` spawns. The chat panel follows it live, approval cards render there, and every
   permission and write choke applies unchanged. The voice instruction rides as a harness note,
   hidden from the transcript's bubbles. **Lucius's answer is spoken verbatim** (`session.say`).
3. The LiveKit worker runs **in a child process of its own** (`connectors/voice/worker.py`,
   started and watched by `connectors/voice/host.py`). It first ran in the app process, and a
   SIGSEGV in LiveKit's native layer took the whole app down (2026-11-03); now a crash ends the
   calls and the worker restarts (3, 10, 30 s). The app keeps everything that is the app's — the
   call registry, Lucius's turns, the transcript — and answers the worker over the child's
   stdin/stdout (JSON lines): only an allow-listed six operations, and Lucius's side only on a
   conversation the app itself handed that worker for a call. The worker's settings arrive over
   that pipe, never argv or the environment, and its environment is the `core.child_env`
   allowlist; it starts from the app's own folder in safe-path mode, so nothing in the folder the
   app was launched from can be imported into it. The worker is the part assumed compromised
   (it parses remote media), so the APP holds the limits again on its side: 10 requests a minute
   per call, voice calls re-checked on every request, Lucius's model read from the owner's
   setting, a call's conversation forgotten when the call ends or after two hours, and a
   malformed or over-long line from the worker ignored or fatal to that worker only. It
   connects out to the owner's LiveKit project, so the app opens no port. Credentials live in the `voice`
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
   into `work_on_request` by text it heard has no owner words to send and is refused. Requests are
   capped at 3 queued and 10 a minute; a call never starts a second turn beside a running one,
   never joins an Automation's conversation, and plain `ws://` is accepted only to this machine.
5. Every piece is swappable behind a setting: transport (LiveKit Cloud or the self-hosted
   open-source server), speech-to-text, voice and front model. The voice libraries are optional
   (`requirements-voice.txt`).

## Consequences

### Positive
- Greetings, acknowledgements, "are you there?" and goodbyes answer in about a second; talking over
  the front voice is natural, and a request in flight is never cancelled by "sure".
- No second write path, confirm path or permission check: a call is the chat, spoken.
- Numbers are only ever Lucius's own words; the front model cannot invent one into the record.
- No new authentication path: the worker reaches turns through its own process's pipe, not over
  loopback HTTP, so there is no new credential or route to guard.
- A crash in LiveKit's native layer ends the calls, never the app.

### Negative
- A second model is in the loop for the conversational layer — reversing the spec's first
  "Lucius is the only voice" call. Its side of the conversation crosses LiveKit and the model
  provider.
- One more long-lived process (about the memory of a second Python with LiveKit loaded), and
  every Lucius request makes one extra hop over the pipe.
- The voice pipeline's CPU work (voice-activity detection) shares the machine with the app; a busy
  machine slows both.
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
  internal credential and auth path into `/api/chat` (and gateway mode refuses loopback calls
  without its token). The child's stdin/stdout reach the same turn machinery with no new route.
- **The worker in the app process** (shipped first) — rejected after a SIGSEGV in LiveKit's
  native layer killed the app mid-call (2026-11-03).
- **Running the call as a chat connector (`connectors/processor.process_message`)** — rejected: its
  turns don't reach `live_sessions`, so the open chat panel would not show the call or its cards.

## Amendment (2026-11-03): calls on the public demo, capped by the minute

Owner decision: visitors of the public demo may make calls, on a LiveKit project of the demo's
OWN (never the owner's personal one), because one person's testing used ~100 call-minutes of a
project's inference credit. A call is billed by the minute whether or not Lucius is asked
anything, and the demo's token budgets (`services/demo_quota`) see none of that, so:

- **One new demo write, nothing else.** `POST /api/voice/call` joins the demo's write allowlist;
  every other voice write (the LiveKit project, its keys, the call settings) stays locked
  (`api/demo_lockdown.py`). The keys are stored from the server's shell (`demo-aws.sh voice`,
  `DEMO_MODE=0` for that one process, as `demo-aws.sh lucius` does), checked against LiveKit
  first, and carried across the daily book reset like Lucius's AI settings.
- **A call is the visitor's own.** It goes only into a conversation the visitor owns (another
  visitor's or an Automation run's is a 404, as in the chat) and claims the one it creates. Its
  conversation is bound to the visitor's address when the call starts, so the Lucius turns the
  worker spawns later — with no request behind them — are charged to that visitor's token budget
  and run as that visitor (`demo_quota.bind_session`); a spent budget is said on the call instead
  of starting a turn.
- **Call caps in minutes** (`services/demo_calls`, env-configurable like `DEMO_TOKENS_*`): longest
  call 10 min, 3 calls and 15 call-minutes per visitor per rolling day, 120 call-minutes per day
  across all visitors, 2 calls at once. Each call gets its own limit at start — the shortest of
  the longest call, the visitor's minutes left and the demo's — and **reserves that whole limit
  while it is on**, so concurrent calls cannot add up past an allowance; once ended it counts
  what it used. A call whose end never arrives counts as having run to its limit; a minted call
  the worker never picks up within a minute counts one call and no minutes.
- **The worker hangs up; the app makes sure.** The worker asks for the call's limit
  (`call_limit`, a new allow-listed host call) and the front voice says it is nearly out of time
  30 s before, then says goodbye and hangs up at the limit, in the call's language. The app, which
  never trusts the worker for a limit, refuses the worker's requests for that call 20 s past it
  and closes the room itself through LiveKit's room service (`connectors/voice/rooms.py`).
- **The image carries the voice libraries** behind a `VOICE` build arg (default off, so the
  published image is unchanged), from a hash-pinned lock (`requirements-voice.lock`) compiled
  with the main lock as a constraint.

Off the demo nothing changes: no cap, no limit, no new check. Rejected: limiting by the cookie
identity (cleared in a click; the address is what the token budgets use); limiting only the
front voice's tokens (the speech services bill by the minute, not by the token); trusting the
worker alone to hang up (it is the process the host assumes compromised).

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
