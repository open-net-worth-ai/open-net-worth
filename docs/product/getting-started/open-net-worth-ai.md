---
title: Open Net Worth AI
description: Our own AI service for Lucius — included with Private Cloud, or bought as credit and connected with a key on the desktop app or your own server.
category: getting-started
order: 45
audience: [user]
status: stable
related: [getting-started/connecting-an-ai-provider, lucius/models-and-cost, getting-started/first-run]
keywords: [open net worth ai, ai credit, private cloud, api key, relay, haiku, privacy, zero retention]
---

# Open Net Worth AI

Open Net Worth AI gives Lucius a model without an account at an AI company. It is one
endpoint we run, with a short list of models picked to work well with Lucius. It appears in
**Settings → AI** as **Open Net Worth AI**.

## On Private Cloud

Private Cloud includes a monthly AI credit, and your server comes with Open Net Worth AI
already connected. There is nothing to set up: on a new server Lucius starts on **Claude Haiku
5.5**, and you can pick another of the listed models under **Default model**.

The row reads **Managed by Private Cloud**. Its endpoint and key belong to your Private Cloud
server, so the row has no **Edit** button. Below it you see what is left this month, for example
**$4.20 of $10.00 left · resets Dec 1**. If that line is missing, the figure could not be read
just now. Lucius still works.

You can still add any other provider beside it.

## With a key (desktop app or your own server)

1. Buy credit on [cloud.opennetworth.ai](https://cloud.opennetworth.ai) and create a key.
2. In **Settings → AI**, click **＋ Add provider** and pick **Open Net Worth AI**.
3. Leave **Base URL** as `https://inference.opennetworth.ai/v1`, paste the key into **API key**,
   choose a **Default model**, and click **Add provider**.

The app checks the key and loads the models it can use. The key is stored like every other
provider key: on the machine running Open Net Worth, never shown to Lucius, never in an export.

The address must start with `https://`. Plain `http://` is accepted only for `localhost` and
the Private Cloud network address `10.99.0.1`.

A key does not show the credit left in the app yet. Check it on cloud.opennetworth.ai.

## When the credit runs out

Lucius stops and says so once:

- **On Private Cloud:** "This month's AI credit is used up. It resets on (date), or add credit
  on cloud.opennetworth.ai."
- **With a key:** "Your Open Net Worth AI credit is used up. Add credit at
  cloud.opennetworth.ai."

An Automation that runs out is marked **Held: AI credit is used up**. Until there is credit
again, the scheduled runs of Automations that use Open Net Worth AI wait instead of running:
on Private Cloud until the credit resets (or sooner, once credit is added), with a key for an
hour at a time. The first scheduled run after that goes ahead.

## Privacy

Requests pass through our relay to the model provider and back. The relay doesn't store your
messages or the answers, and they are never used for training. Requests only go to model
providers that keep nothing (zero-retention routing).

## Related

- [Connecting an AI provider](connecting-an-ai-provider.md)
- [Models and cost](../lucius/models-and-cost.md)
