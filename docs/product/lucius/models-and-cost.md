---
title: Models and cost control
description: Pick the AI model Lucius uses, per chat or per Automation, send background jobs to a cheaper one, set effort and time limits, and see what it costs.
category: lucius
order: 60
audience: [user]
status: stable
related: [getting-started/connecting-an-ai-provider, lucius/memory, lucius/automations]
keywords: [model, provider, cost, api key, effort, task models, preferred models, turn time limit, spend, subscription, claude code, codex]
---

# Models and cost control

Lucius isn't tied to one AI company. You connect your own provider — a Claude or ChatGPT
subscription you already pay for, an API key, or a model running on your own computer — and the
app talks to it directly. Open Net Worth adds no markup; you pay your provider, or nothing with
a local model.

## Set up a model

1. Open **Settings → AI**.
2. In **AI providers**, either:
   - under **Connect a subscription**, follow the steps for **Claude Code CLI** or **Codex CLI**
     (install it, then sign in from a terminal) — no API key needed; or
   - click **＋ Add provider**, choose the **Provider**, paste your **API key** (or a **Base
     URL** for a local or custom endpoint), pick a **Default model**, and click **Add
     provider**.
3. Under **Default model**, choose the model Lucius should use unless a chat or Automation picks
   another.
4. Check it works: ask Lucius *"what's my net worth?"* in a new conversation.

Step-by-step instructions for each provider, and where to get a key, are in [Connecting an AI
provider](../getting-started/connecting-an-ai-provider.md).

The providers you can add: **Anthropic**, **OpenAI**, **Google Gemini**, **xAI (Grok)**, **Zhipu
(GLM)**, **OpenRouter**, **Ollama (local)**, **LiteLLM Proxy (local)**, **Custom
(OpenAI-compatible)** for any other endpoint that speaks the OpenAI API, and the **Claude Code
CLI**, **Codex CLI** and **Antigravity CLI** programs if they're installed on this computer.

Each provider row shows whether it works: **Ready**, **Key rejected** or **Can't connect** (for
a CLI: **Installed** or **Not installed**). Click **Edit** on a row to change it, **Test
connection**, or **Remove** it. The switch on a row turns a provider off without deleting it.

Your API key is stored on this machine and never sent to Lucius or shown in a chat. The key field
hides it; you can reveal a saved key to check it.

## Choose a model for one chat or one Automation

- **A chat:** click the model name in the message box's toolbar (it shows **Default** until you
  change it) and pick another. That conversation keeps it.
- **An Automation:** in its editor, set **Model** under the instructions. Changing your default
  later doesn't change an Automation that has its own model.

**Preferred models** (in **Settings → AI**) is your shortlist: the models you pick there are
pinned to the top of every model picker.

## Effort

Effort is how hard the model thinks before answering — more effort is slower and costs more, less
is quicker and cheaper.

- **In a chat:** open the model picker and use its **Effort** tab. The choices depend on the
  model: **Low**, **Medium**, **High**, **Xhigh**, **Max**, plus **Off** on some OpenRouter
  models. A new chat starts on **Medium**. Models that don't support effort don't show it.
- **In an Automation:** **Low effort**, **Medium effort** or **High effort**, beside its
  **Model**.

## Send background jobs to a cheaper model

Some of Lucius's work happens in the background rather than as a reply. In **Settings → AI →
Task models** each job can use its own model; anything you leave on **Default** uses your default
model. Chat itself always uses your default (or the chat's own choice).

| Group | Tasks |
|---|---|
| **Assistant** | **Memory**, **Chat suggestions**, **Conversation titles**, **Image reading** (needs a model that can see images), **Auto-approval judge** |
| **Insights** | **Intel & watchlist**, **Daily briefing** (the morning note on the Dashboard), **News screening**, **Alert analysis**, **Price alerts** |
| **Statement import** | **Statement import** — reading statements end to end |

**Auto-approval judge** only matters when a conversation or Automation is in **Automatically
approve** mode: before each call that would otherwise ask, this task decides whether Lucius must
ask you first (see [Permissions](permissions.md#how-automatically-approve-decides)). Unlike the
others, leaving it on **Same as chat model** uses the model your conversation is running on, not
your default — a Claude Code or Codex model judges like any other. Each judgement waits at most 10
seconds, then asks you instead. A fast, non-reasoning model keeps Auto snappy. The judge is sent the pending call (the
tool's name and description, and the arguments) and your last few typed messages; if you pick a
different provider than your chat model, that provider receives them too.

A good pattern: a strong model for chat and **Alert analysis**, a cheap fast one for
**Conversation titles**, **Chat suggestions** and **Memory**. **Reset** puts every task back on
your default. If a task that reads images is set to a text-only model, a warning tells you that
those runs will fail until you choose a model with vision.

The **Daily briefing** *Automation* is a different thing — it runs on that Automation's own
**Model**.

## How long a turn may run

**Settings → AI → Turn time limit → Stop a turn after** sets how long Lucius may work on one
reply or one Automation run before it's stopped: **20 minutes (default)**, **30 minutes**,
**1 hour**, **2 hours** or **No limit**.

A stopped turn says so — *"This turn hit its 20-minute time limit and was stopped."* — and
nothing is left half-applied. Most turns finish in seconds; one that keeps hitting the limit is
usually waiting on a slow model. Pick a faster model (for an Automation, set its own **Model**)
or raise the limit. With **No limit**, a turn runs until it finishes or you press Stop. A single
call to the Claude Code or Codex CLI is still stopped after 20 minutes, since a call that long
means the CLI has hung.

## Long conversations

A long conversation is compacted automatically: older messages are folded into a summary while
recent ones stay word for word, so it keeps working and doesn't get more expensive with every
message. There's nothing to set. This is separate from [Memory](memory.md), which carries facts
*between* conversations.

## See what it costs

- **Per reply:** under each reply, the tokens, price and model that answered. Turn it off with
  **Settings → Chat & Memory → Show cost per turn** — hiding it changes nothing about what's
  spent or recorded.
- **Per conversation:** the running total next to the model name in the message box.
- **Per Automation:** its **Cost & usage** section; sort the Automations list by **Cost this
  month**.
- **Everything:** **Settings → Cost & Performance**. Pick a **Period** (**Today**,
  **Yesterday**, **7 days**, **30 days**, **All time**) to see **AI spend** — total, turns,
  tokens and cache hits, broken down **By surface**, **By provider** and **By model**, a cost
  trend, and a **Projected** monthly estimate at that period's rate. Below it, **Search &
  fetch** shows estimated charges from web-search providers, and **Model performance** shows
  each model's speed and reliability.

Prices are calculated from each provider's published rates. With the Claude Code CLI on a
Claude plan, the figure is what that usage would cost at list price — your plan covers it, and
its usage limits apply instead of a bill.

To cut costs: turn off **Follow-up suggestions** in **Settings → Chat & Memory** (that stops a
model call per reply), route background tasks to a cheaper model, and use **Low** effort for
simple chats.

## Troubleshooting

**Key rejected** — the API key is wrong or was revoked. **Edit** the provider and paste a new
one.

**Can't connect** — the endpoint isn't reachable. For a local model, check it's running and the
**Base URL** is right (Ollama's is `http://localhost:11434/v1`).

**The connection test failed** — the message under it says why. **Report this problem** opens
the problem report with that error filled in, for you to review before sending.

**"Choose a default model on another provider first"** — you can't switch off the provider your
default model uses. Pick a different **Default model**, then switch it off.

**"… can't read images"** — an image task (such as **Image reading**) is set to a text-only
model. Choose a model with vision for it in **Task models**.

## Related pages

- [Connecting an AI provider](../getting-started/connecting-an-ai-provider.md) — setup for each
  provider
- [Memory](memory.md) — has its own background model
- [Automations](automations.md) — each can use its own model
