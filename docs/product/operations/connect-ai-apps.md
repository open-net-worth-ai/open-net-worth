---
title: Use your book from other AI apps
description: Step-by-step setup for Claude Desktop, Claude Code, Cursor, VS Code, ChatGPT or any MCP client, so it can read and work on your book.
category: operations
order: 62
audience: [user, operator]
status: stable
related:
  [
    operations/security-and-privacy,
    operations/exposing-beyond-localhost,
    connectors/external-mcp,
    features/settings,
  ]
keywords:
  [
    mcp,
    claude desktop,
    claude code,
    cursor,
    vs code,
    chatgpt,
    claude.ai,
    claude mobile,
    custom connector,
    oauth,
    connected apps,
    read-only,
    cline,
    continue,
    stdio,
    onw mcp,
    api token,
    external client,
    copy config,
  ]
---

# Use your book from other AI apps

Open Net Worth is itself an MCP server, so any AI app that speaks MCP — Claude Desktop, Claude
Code, Cursor, VS Code (Copilot), ChatGPT, Cline, Continue — can connect to your book and use the
same tools Lucius has: look up balances, search transactions, add and categorize them, check the
budget, and so on.

This is the opposite direction from [External MCP servers](../connectors/external-mcp.md), where
Lucius uses someone else's server.

## Before you start

- **Open Net Worth must be running** whenever the other app uses it (for the desktop app, it must
  be open or [running in the background](desktop.md#keep-it-running-in-the-background)).
- **Your API token** is what lets the other app in. One exists already — you don't create it.
  It lives in **Settings → Security → API token**, and **Copy config** (below) puts it into the
  config for you.

## Set up Claude Desktop, Cursor, VS Code, Cline or Continue

These apps run on your computer, so they connect through a small bridge program that the app
starts for you.

1. In Open Net Worth, open **Settings → MCP** and choose **API token** at the top.
2. Under **Your app**, click your app: **Claude Desktop**, **Cursor**, **VS Code (Copilot)**
   or **Cline / Continue / other**.
3. If you run Open Net Worth as a server rather than the desktop app, pick how it runs:
   **Installed (onw)**, **Running from source**, or **Docker or another computer** (see
   [Docker, or an AI app on another computer](#docker-or-an-ai-app-on-another-computer)).
   With Docker, Settings picks the last one for you.
4. Click **Copy config**. The copy contains your real API token (the screen shows a placeholder,
   `ONW_API_TOKEN_PASTED_ON_COPY`, so the token never appears on the page).
5. Paste it where the hint on screen says:

   | App                          | Where to paste                                                                                                                                                                                 |
   | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | **Claude Desktop**           | **Settings → Developer → Edit Config** opens `claude_desktop_config.json`. Paste it in (merge it into `"mcpServers"` if you already have other servers), save, and **restart Claude Desktop**. |
   | **Cursor**                   | `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project).                                                                                                                       |
   | **VS Code (Copilot)**        | `.vscode/mcp.json` (workspace) or your user `mcp.json`. VS Code uses `"servers"`, not `"mcpServers"`.                                                                                          |
   | **Cline / Continue / other** | The app's MCP settings file, in its `mcpServers` section.                                                                                                                                      |

   For Cursor, VS Code and "other", the copied text holds **two alternatives** separated by
   `// — or —` lines. Paste **only one** of them (the _stdio_ one, for an app on the same
   computer) — the comment lines and the second block would make the file invalid.

6. **Check it works:** in the AI app, ask _"Using open-net-worth, what's my net worth?"_ It should
   answer with your figure. In Claude Desktop, the server also appears under its connectors/tools
   list.

A Claude Desktop config looks like this (server installed with `onw`):

```json
{
  "mcpServers": {
    "open-net-worth": {
      "command": "/Users/you/.local/bin/onw",
      "args": ["mcp"],
      "env": {
        "ONW_BASE_URL": "http://127.0.0.1:8080",
        "ONW_API_TOKEN": "your-api-token"
      }
    }
  }
}
```

What the `command` is depends on how you installed Open Net Worth — **Copy config** fills in the
right one:

| Install                        | What the config runs                                                                                                                                                                                                                                                                                                |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Desktop app**                | The Python bundled inside the app, with `-m backend.cli mcp`. Instead of an address, the config names your book folder (`ONW_ROOT`), because the desktop app picks a new address each time it starts and the bridge finds the current one there. If you move the app or switch book folders, copy the config again. |
| **Installed (`onw`)**          | The `onw` command, with `mcp`. Settings fills in its full path when it can, because apps like Claude Desktop don't search your shell's `PATH`.                                                                                                                                                                      |
| **Running from source**        | `python3 -m backend.mcp.stdio_bridge`, run in your checkout folder. See the troubleshooting note below if your app can't start it.                                                                                                                                                                                  |
| **Docker or another computer** | `onw mcp`, installed on the computer where the AI app runs, with the address of your Open Net Worth. See below.                                                                                                                                                                                                     |

## Docker, or an AI app on another computer

When Open Net Worth runs in Docker, or on another computer (a home server, a NAS), the AI app
can't start anything from it: the programs inside the container or on the server don't exist on
the computer where the AI app runs. So that computer gets its own copy of the bridge:

1. On the computer running the AI app, install the bridge:

   ```bash
   uv tool install open-net-worth
   ```

   This doesn't start a second Open Net Worth there. `onw mcp` only passes each message from the
   AI app to your instance's address and hands the answer back.

2. On that same computer, open Open Net Worth in the browser, go to **Settings → MCP**, choose
   **Docker or another computer** and click **Copy config**. Copying it there puts in the
   address that computer reaches your instance at.
3. Paste it into the AI app as in the steps above.

**You don't need to update the bridge when you update Open Net Worth.** It doesn't contain any
Open Net Worth tools: each time the AI app connects, it asks your instance for the tool list, so
new tools appear on their own. It depends only on the MCP standard itself.

If you'd rather install nothing from Open Net Worth on that computer, the generic `mcp-remote`
bridge does the same job. It needs Node.js. Turn on **Use npx instead** under **Docker or another
computer** and **Copy config** gives you this, with your token filled in:

```json
{
  "mcpServers": {
    "open-net-worth": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://your-server:8080/mcp",
        "--allow-http",
        "--header",
        "Authorization:${ONW_AUTH}"
      ],
      "env": { "ONW_AUTH": "Bearer your-api-token" }
    }
  }
}
```

`--allow-http` is only needed for an `http://` address. The token sits in `env` because some
apps break an argument that contains a space.

## Set up Claude Code

Claude Code has no tab of its own in Settings → MCP, but it takes the same configuration:

1. In **Settings → MCP**, choose **API token**, then **Claude Desktop**, and click **Copy config**.
2. Take the part inside `"open-net-worth": { … }` and add it from a terminal:

   ```bash
   claude mcp add-json open-net-worth '{"command":"/Users/you/.local/bin/onw","args":["mcp"],"env":{"ONW_BASE_URL":"http://127.0.0.1:8080","ONW_API_TOKEN":"your-api-token"}}'
   ```

   Add `--scope user` to make it available in every project.

3. Check with `claude mcp list` — `open-net-worth` should show as connected — then ask Claude Code
   about your book.

If Open Net Worth runs as a server (not the desktop app), you can instead connect over HTTP:

```bash
claude mcp add --transport http open-net-worth http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer your-api-token"
```

## claude.ai, Claude on your phone and ChatGPT (sign in with OAuth)

**Experimental — off by default.** In **Settings → MCP**, choose **OAuth** at the top: a
checklist shows each step, with a button to the setting that fixes it. The first is turning on
**Mcp oauth** under **Settings → Developer → Experimental** (see
[Experimental features](../features/settings.md#experimental-features)).

claude.ai (web and mobile), Claude Desktop's **Add custom connector** and ChatGPT run on their
provider's servers. They can't start a program on your computer or reach `localhost`, and they
can't send your API token: they sign in with OAuth instead. You approve each app once, with your
password, and choose whether it may only read or may also make changes.

1. Make your instance reachable at a public HTTPS address — see
   [Exposing beyond localhost](exposing-beyond-localhost.md). (This needs a server install; the
   desktop app only accepts connections from its own computer.)
2. In **Settings → Security**, set a password if you haven't, and turn off **Don't ask for the
   password on this computer**. Behind a reverse proxy or tunnel every visitor looks like "this
   computer", so the connection is refused while it's on.
3. In **Settings → MCP → OAuth**, enter your public address (for example
   `https://wealth.example.com`) and click **Save**, or set `ONW_PUBLIC_URL` on the server. If
   you opened Settings through that HTTPS address, it's already filled in; it's saved only when you
   click **Save**. The **OAuth** card shows **Ready** when everything is in place, or how many steps are left.
4. Add the connector in the app, using the **Connector address** the panel shows
   (`https://your-address/mcp`; **Copy** puts it on your clipboard):
   - **Claude:** open **Connectors** (under **Customize** or **Settings**) and choose **Add
     custom connector**. Leave the advanced OAuth fields empty. Add it once and it works on the
     web, on your phone and in Claude Desktop.
   - **ChatGPT:** with Developer mode on, add a custom MCP server (connector) with that URL and
     choose OAuth.
5. A page from your own address opens. Check the address bar and type your Open Net Worth
   password. It confirms this one connection only: it doesn't sign that browser in to Open Net
   Worth.
6. The next page shows which app is asking and what it will be able to do. Choose **Read**,
   **Read and change**, or **Talk to Lucius**, then **Allow**.

Only approve a request you started yourself, a moment ago, from your own account. If someone
sends you a link to this page, choose **Deny**: approving it would connect _their_ account to your
book.

**What happens after you approve**

- **Talk to Lucius** apps can only talk to Lucius (see [Talk to Lucius from another
  app](#talk-to-lucius-from-another-app)). They don't open your book themselves, but Lucius may
  share anything he can see in his answers.
- **Read-only** apps see only the tools that read. They can't add, edit or delete anything, send
  anything or run code. They can read everything a read tool reads, including past conversations
  and the database through the SQL tool, but never a document marked Private.
- **Read and write** apps can do what an app with your API token can (below).
- **Neither** can use the tools of MCP servers you've connected to Open Net Worth (a broker's,
  say): those stay Lucius's. A connected app sees only Open Net Worth's own tools.
- You get a notification each time an app is connected.
- **Settings → MCP → OAuth** lists every connected app, when it last used your book, and a
  **Revoke** button.
- Changing your password, or the public address, disconnects every app. So does an app reusing an
  old sign-in token, which can mean the token was copied. You get a notification then too.
- An app that stays unused for 30 days, or has been connected for 180 days, has to be approved
  again.

**Another hosted app.** If an app asks for a client ID and secret, create them in **Settings → MCP
→ OAuth → Client ID and secret**: enter the app's name and its redirect (callback) URL. The
secret is shown once, with **Copy client ID** and **Copy secret** buttons. An app shows under
**Connected apps** once it has finished signing in.

**If your hosted app accepts a header instead.** Some apps let you set a fixed
`Authorization: Bearer …` header. In **Settings → MCP**, choose **API token**, then **Generic HTTP / remote**, click
**Copy config**, and use the copied `url` and header. That token has full access (see below).

**Firewalls.** If you limit who can reach your address, allow Anthropic's outbound range
(`160.79.104.0/21`) for Claude and OpenAI's published egress addresses for ChatGPT.

### Behind a reverse proxy or a sign-in gateway

If something in front of Open Net Worth asks for its own login (Authelia, Authentik, Cloudflare
Access, oauth2-proxy, a basic-auth proxy), Claude and ChatGPT can't get past it: adding the
connector fails at "Connecting to the server" with **Not found** or a **302**. Let these paths
through to Open Net Worth and keep everything else behind your gateway:

| Path | What it is |
|---|---|
| `/mcp` | The MCP endpoint. It still needs a token from Open Net Worth. |
| `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp` | Where the app learns who issues tokens |
| `/.well-known/oauth-authorization-server` | The sign-in endpoints |
| `/oauth/*` | Authorize, token, revoke, and the approval page itself (`/oauth/consent`) |
| `/api/oauth/consent*` | The approval page's password check, Allow / Deny and the app's icon |
| `/assets/*`, `/theme-stamp.js`, `/logo-mark.svg`, `/favicon.svg`, `/favicon.ico`, `/manifest.webmanifest` | The approval page's scripts, styles and icons (no data) |

Each of these is still locked by Open Net Worth: `/mcp` refuses a request without a token, and a
token is issued only after you type your password on the approval page. Without the last two
rows, approving works only in a browser already signed in to your gateway; on a phone it lands
on the gateway's login instead.

As an Authelia rule (`access_control`, `policy: bypass`, above your other rules for the domain):

```yaml
- domain: wealth.example.com
  policy: bypass
  resources:
    - '^/mcp([/?].*)?$'
    - '^/\.well-known/oauth-(protected-resource|authorization-server)([/?].*)?$'
    - '^/oauth/.*$'
    - '^/api/oauth/consent.*$'
    - '^/assets/.*$'
    - '^/(theme-stamp\.js|logo-mark\.svg|favicon\.(svg|ico)|manifest\.webmanifest)$'
```

With Cloudflare Access, add a second self-hosted application for the same hostname with these
paths and a **Bypass** policy for **Everyone**; the more specific path wins, so the rest of the
site keeps your existing login.

The proxy itself needs only the usual: pass `X-Forwarded-Proto: https`, and allow long-lived
requests (HAProxy: `timeout tunnel 3600s`).

To check from a terminal, `curl -I https://wealth.example.com/mcp` should answer `401` with a
`WWW-Authenticate` header naming `resource_metadata`. A `302` means the gateway is still in the
way.

## Talk to Lucius from another app

An app connected with **Talk to Lucius** or **Read and change** (or your API token) can talk to
**Lucius** instead of reading your book itself. In that app, say for example: _"Ask Lucius
whether I can afford these headphones"_, _"Ask Lucius which of these flights fits my plans"_, or
_"Tell Lucius I bought them on Card B"_.

- **Each one is a real conversation with Lucius.** It appears in your Lucius history under the
  app's name, so you can read it, continue it or stop it. The app can continue only its own
  conversations.
- **Lucius has his usual tools and follows your permissions.** Tools you've blocked stay blocked;
  for tools set to ask, the conversation uses **Auto**, so the Auto judge decides, as in your own
  chats. When Lucius needs your approval, **a card with Approve and Decline appears right in the
  app's chat** (Claude and ChatGPT show it; only a tap on the card answers it, never the app's
  AI). Anything that deletes, removes or sends something out is approved in Open Net Worth
  instead: the card says so, and you get a notification. In an app that can't show the card, you
  get a notification and approve in Open Net Worth. A card you can answer right in the app's chat
  sends no notification.
- **The app's words are treated as a question, not as orders** to Lucius, and never as your
  approval: a change set to ask needs **you**, even if the app says you agreed.
- **Progress shows while Lucius works**, in apps that display it.
- It uses your instance's AI model, like a message to Lucius. It isn't available on the public
  demo.

For a quick check right before paying, an app with Read access can also use **Ask Lucius before
you buy** (see [Skills](../lucius/skills.md#ask-lucius-before-you-buy)), which answers instantly
from your budget.

## What a connected app can do

An app holding your API token, or approved with **Also let it make changes**, **acts as you**: it
can read your whole book and make changes.

- **No approval cards.** When Lucius wants to make a change that needs your approval, he shows you
  a card first. Other apps don't get those cards: a tool set to **Needs approval** runs straight
  away when another app calls it. Only tools set to **Blocked** in
  [Settings → Permissions](../lucius/permissions.md) are refused. If you'd rather an app couldn't
  change something, block that tool.
- **Changes are recorded.** Each change another app makes is logged and saved as a checkpoint, so
  you can undo it like one Lucius made. Things that leave the app — an email or message sent, code
  run — can't be undone.
- **One token for every app.** Rotating it in **Settings → Security → API token → Rotate**
  disconnects every app configured with the old one, and every installed iPhone Shortcut, at once.
  There's no confirmation step. Afterwards, **Copy config** again and paste the new one into each
  app.

## Troubleshooting

**Claude Desktop says "Server disconnected" / the tools don't appear**

- Open Net Worth isn't running — start it, then restart Claude Desktop.
- The app can't find `onw`. Replace `"command"` with the full path printed by `which onw`
  (Windows: `where onw`).
- The log says `Failed to spawn process: No such file or directory` and Open Net Worth runs in
  Docker or on another computer: `onw` isn't installed where the AI app runs. See
  [Docker, or an AI app on another computer](#docker-or-an-ai-app-on-another-computer).
- The JSON is invalid — check you pasted only one block and didn't leave `//` comment lines in.

**"cannot reach Open Net Worth MCP server at …"** — the bridge started but Open Net Worth isn't
running at that address. Start it. With a server install, check `ONW_BASE_URL` matches the
address you open in your browser.

**It worked, then stopped**

- You rotated the API token, or set a password for the first time (setting the first password
  also replaces the token). Copy the config again.
- Desktop app: a config containing a `127.0.0.1:<port>` address goes stale when the app
  restarts. Copy the config from **inside the desktop app** so it uses `ONW_ROOT` instead.

**"Running from source" fails with `No module named …`** — the app started your system
`python3`, not the checkout's environment. Change `"command"` to the checkout's own `onw`,
`/path/to/open-net-worth/.venv/bin/onw` (created by `make setup`), set `"args"` to `["mcp"]`, and
remove `"cwd"`.

**"authentication required" (401)** — the token in the config is wrong or old. Copy the config
again.

**ChatGPT can't connect** — it can't reach `localhost` or a home-network address; it needs a
public HTTPS address (see step 1 above).

## Related pages

- [Exposing beyond localhost](exposing-beyond-localhost.md) — a public address for hosted apps
- [Permissions](../lucius/permissions.md) — blocking tools you don't want changed
- [External MCP servers](../connectors/external-mcp.md) — the other direction
- [Security and privacy](security-and-privacy.md)

## Guides and comparisons

Longer write-ups on the Open Net Worth blog:

- [Personal Finance MCP Server: Use Your Ledger from Claude](https://opennetworth.ai/blog/personal-finance-mcp-server/): a walkthrough of the MCP server with Claude
- [An Open-Source Claude Personal Finance Alternative](https://opennetworth.ai/blog/claude-personal-finance-alternative/): Claude's own personal finance features next to a self-hosted ledger
