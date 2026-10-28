---
title: Connecting an AI provider
description: Give Lucius a model — a Claude or ChatGPT subscription, an API key, or a local model — with step-by-step setup for each, and what it costs.
category: getting-started
order: 40
audience: [user]
status: stable
related: [getting-started/first-run, lucius/models-and-cost, operations/terminal, getting-started/what-is-open-net-worth]
keywords: [ai provider, model, api key, claude code, codex, subscription, anthropic, openai, openrouter, gemini, ollama, lm studio, cost, setup]
---

# Connecting an AI provider

Lucius needs an AI model to think with. You connect one during [first run](first-run.md)
(Step 2, **Connect Lucius**), and can add or change it any time in **Settings → AI**.

Open Net Worth itself is free; you pay your model provider, or nothing at all with a local model.
There are three kinds of connection:

| Option | You need | Cost |
|---|---|---|
| **[A subscription you already pay for](#use-a-claude-or-chatgpt-subscription)** — Claude Code or Codex | A Claude or ChatGPT plan that includes it | Included in your plan; plan limits apply |
| **[An API key](#use-an-api-key)** — OpenRouter, Anthropic, OpenAI, Gemini, xAI, Zhipu | An account with that provider | Pay per use, billed by the provider |
| **[A local model](#use-a-local-model)** — Ollama, LM Studio, LiteLLM | A model running on your own computer | Free, but needs a capable machine |

Lucius works through long chains of tool calls, so a strong model matters. If you're unsure,
start with a subscription you already have, or OpenRouter (one key, many models, including
cheaper capable ones).

## Use a Claude or ChatGPT subscription

No API key needed. The app runs the official command-line tool (**Claude Code** for Claude,
**Codex** for ChatGPT) on the same computer, signed in to your account.

**Install it on the computer running Open Net Worth.** With the desktop app, that's your
computer. With a server, it's the server — which may not be the device showing the browser.

1. In first run's **Connect Lucius** step, or in **Settings → AI** under
   **Connect a subscription**, find **Claude Code** or **Codex** and expand **Install & sign in**.
2. Copy step 1, the install command (it matches the server's operating system), and run it in
   Terminal (macOS/Linux) or PowerShell (Windows):

   | | macOS / Linux | Windows (PowerShell) |
   |---|---|---|
   | Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash` | `irm https://claude.ai/install.ps1 \| iex` |
   | Codex | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` | `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 \| iex"` |

3. Open a **new** terminal and run step 2, the sign-in command:
   - Claude Code: `claude auth login`, then choose your Claude subscription.
   - Codex: `codex login`, then choose **Sign in with ChatGPT**.
4. Go back to the app. It checks every few seconds, so the row changes to **Found Claude Code on
   this machine** (or Codex) on its own. Click **Use this**.
5. The **Model** is preset to whatever that tool already runs by default. Click **Continue**.

In Settings, a found tool appears in **AI providers** with an **Installed** badge; choose one of
its models under **Default model** to make Lucius use it.

Other install methods are in the official [Claude Code setup](https://code.claude.com/docs/en/setup)
and [Codex](https://developers.openai.com/codex/cli) instructions.

**Google Antigravity:** if Google's Antigravity CLI (`agy`) is installed and signed in on that
computer, it's offered as **Antigravity CLI** the same way.

**Installed somewhere unusual?** In **Settings → AI**, click **Edit** on the provider and set
**Binary path** to the full path of `claude` or `codex`. The macOS desktop app already looks in
`~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`.

**When the tool updates itself:** Claude Code and Codex update on their own. The first time Open
Net Worth starts with a new version, it quietly checks that Lucius's tools still work through it.
If an update broke that, a notification in the bell says so; switch model in **Settings → AI**,
or update Open Net Worth.

## Subscription on a remote server

Install and sign in on the server itself, over SSH. If the server's administrator has turned on
the in-app terminal, you can do it from **Settings → Terminal** instead — see
[Terminal](../operations/terminal.md).

## Subscription under Docker

The Docker image already includes Claude Code and Codex, but the container has its own
filesystem, so it can't see your computer's sign-in. To share it:

1. On your computer (not in the container), sign in first:
   - Claude Code: run `claude setup-token`. This is needed even if `claude` already works for
     you — macOS keeps the sign-in in the Keychain, which the container can't read.
   - Codex: `codex login` is enough.
2. In `docker-compose.yml`, uncomment the matching line under `volumes:`
   (`~/.claude/.credentials.json` or `~/.codex/auth.json`). Do this **after** step 1 —
   mounting a file that doesn't exist yet creates an empty folder instead.
3. Run `docker compose up -d` again.

## Use an API key

1. Get a key from the provider (table below). Most providers need a payment method or prepaid
   credit before the key works.
2. **In first run:** below **or use a provider**, pick the **Provider**, paste the key into
   **API key**, and click **Continue**. The app checks the key with the provider and loads its
   model list — if the provider refuses the key, you can't go on until you fix it. Then pick the
   **Model** and click **Continue**.
3. **In Settings → AI:** click **＋ Add provider**, pick the **Provider**, paste the **API key**,
   choose a **Default model**, and click **Add provider**. To make Lucius use it, pick one of its
   models under **Default model**.

| Provider (as listed in the app) | Where to get a key |
|---|---|
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) → sign in → **Keys** → **Create key**. Add credits under **Credits**. One key reaches models from many companies. Pick a model on par with Claude Sonnet, such as GLM or Kimi — weaker models slip on long tasks. |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) → **API keys** → **Create key**. Add credit under **Billing**. Keys start with `sk-ant-`. On a Claude Max or Team plan, claim the plan's monthly API credits first: on [claude.ai](https://claude.ai), **Settings → Billing** → **API credits** → **Link organization**, then create the key in that Console organization. Credits expire at the end of each billing cycle. Pro includes none. |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) → **API keys** → **Create new secret key**. Add credit under **Billing**. |
| **Google Gemini** | [aistudio.google.com](https://aistudio.google.com) → **Get API key** → **Create API key**. |
| **xAI (Grok)** | [console.x.ai](https://console.x.ai) → **API keys** → create a key. |
| **Zhipu (GLM)** | [z.ai](https://z.ai) → API keys. The endpoint can be changed if you use the mainland China service. |
| **Custom (OpenAI-compatible)** | Any service that speaks the OpenAI API (DeepSeek, Moonshot, a self-hosted gateway). Enter its **Endpoint** URL and key. |

The key is stored on the machine running Open Net Worth only. It's never sent to Lucius, never
included in an export, and the field hides it (you can reveal it in Settings to check it).

## Use a local model

Free, and nothing leaves your machine — but you need a computer that can run a capable model
with tool calling. Small models often struggle with Lucius's longer tasks.

**Ollama**

1. Install Ollama from [ollama.com](https://ollama.com) and start it.
2. Download a model, for example `ollama pull <model-name>` in a terminal.
3. In the app, pick **Ollama (local)**. The **Endpoint** is preset to
   `http://localhost:11434/v1`. Click **Continue**, then pick your model.

**LM Studio** — start LM Studio's local server, then pick **Custom (OpenAI-compatible)** with
the endpoint `http://localhost:1234/v1`. LM Studio doesn't check keys, so any text works in
**API key**.

**LiteLLM Proxy (local)** — for your own LiteLLM gateway. The endpoint is preset to
`http://localhost:4000`; the key is optional (only if your proxy was started with one).

"Local" means local to the machine running Open Net Worth. A model on your laptop isn't reachable
from a server elsewhere.

## Check that it works

Open the **Lucius** chat and ask something like:

> *What's my net worth?*

A real answer means you're connected. In **Settings → AI**, you can also click **Edit** on a
provider, then **Test connection**.

## What it costs

Open Net Worth charges nothing. A subscription uses your plan's limits; an API key is billed by
the provider per use. There's no single honest number — it depends on the model and how much you
use Lucius.

To see and control it:

- **Settings → Cost & Performance → AI spend** shows what you've spent — today, yesterday, the last
  7 or 30 days, or all time — with a timeline, a breakdown by surface, provider and model, and a
  projected monthly cost.
- **Settings → AI → Task models** runs background chores (like automations) on a cheaper model;
  chat keeps your default.
- **Settings → Chat** has switches for extras that use tokens, such as follow-up
  suggestions and memory, and **Show cost per turn** to see each reply's cost in the chat.

More in [Models and cost](../lucius/models-and-cost.md).

## Without a model

First run asks for a model — there's no skip. If you don't want to pay, use a free local model.
If your model later becomes unavailable (out of credit, signed out), everything except Lucius
keeps working: accounts, the ledger, budgeting, investments and CSV/OFX/QIF import. What needs a
model: chatting with Lucius, reading PDF statements and screenshots, auto-categorizing,
automations and memory.

## Troubleshooting

- **"That key was rejected by …"** — the provider refused the key. Copy it again with no spaces
  at either end, and check the key hasn't been revoked.
- **"Could not reach …"** — the app couldn't get an answer from the provider. Check your internet
  connection, that you've added credit to the account, and — for a local model — that Ollama or
  LM Studio is running and the endpoint is right.
- **Claude Code or Codex stays "Not installed"** — install it on the computer running Open Net
  Worth, then open a new terminal so it's on your `PATH`. Still not found? Set its **Binary path**
  in **Settings → AI**.
- **Found, but Lucius fails on the first message** — finding the tool doesn't prove it's signed
  in. Run `claude auth login` or `codex login` again on that computer.
- **"Choose a default model on another provider first"** — you tried to switch off the provider
  Lucius uses by default. Pick another **Default model** first.

## Related

- [Your first run](first-run.md)
- [Models and cost](../lucius/models-and-cost.md)
- [Terminal](../operations/terminal.md)
