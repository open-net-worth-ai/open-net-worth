---
title: Use your book from other AI apps
description: Step-by-step setup for Claude Desktop, Claude Code, Cursor, VS Code, ChatGPT or any MCP client, so it can read and work on your book.
category: operations
order: 62
audience: [user, operator]
status: stable
related: [operations/security-and-privacy, operations/exposing-beyond-localhost, connectors/external-mcp, features/settings]
keywords: [mcp, claude desktop, claude code, cursor, vs code, chatgpt, cline, continue, stdio, onw mcp, api token, external client, copy config]
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

1. In Open Net Worth, open **Settings → MCP**.
2. Under **Connect from…**, click your app: **Claude Desktop**, **Cursor**, **VS Code (Copilot)**
   or **Cline / Continue / other**.
3. If you run Open Net Worth as a server rather than the desktop app, pick how you installed it:
   **Installed (onw)** or **Running from source**.
4. Click **Copy config**. The copy contains your real API token (the screen shows a placeholder,
   `ONW_API_TOKEN_PASTED_ON_COPY`, so the token never appears on the page).
5. Paste it where the hint on screen says:

   | App | Where to paste |
   |---|---|
   | **Claude Desktop** | **Settings → Developer → Edit Config** opens `claude_desktop_config.json`. Paste it in (merge it into `"mcpServers"` if you already have other servers), save, and **restart Claude Desktop**. |
   | **Cursor** | `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project). |
   | **VS Code (Copilot)** | `.vscode/mcp.json` (workspace) or your user `mcp.json`. VS Code uses `"servers"`, not `"mcpServers"`. |
   | **Cline / Continue / other** | The app's MCP settings file, in its `mcpServers` section. |

   For Cursor, VS Code and "other", the copied text holds **two alternatives** separated by
   `// — or —` lines. Paste **only one** of them (the *stdio* one, for an app on the same
   computer) — the comment lines and the second block would make the file invalid.
6. **Check it works:** in the AI app, ask *"Using open-net-worth, what's my net worth?"* It should
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

| Install | What the config runs |
|---|---|
| **Desktop app** | The Python bundled inside the app, with `-m backend.cli mcp`. Instead of an address, the config names your book folder (`ONW_ROOT`), because the desktop app picks a new address each time it starts and the bridge finds the current one there. If you move the app or switch book folders, copy the config again. |
| **Installed (`onw`)** | The `onw` command, with `mcp`. Settings fills in its full path when it can, because apps like Claude Desktop don't search your shell's `PATH`. |
| **Running from source** | `python3 -m backend.mcp.stdio_bridge`, run in your checkout folder. See the troubleshooting note below if your app can't start it. |

## Set up Claude Code

Claude Code has no tab of its own in Settings → MCP, but it takes the same configuration:

1. In **Settings → MCP**, choose **Claude Desktop** and click **Copy config**.
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

## Set up ChatGPT or another hosted app

Hosted apps run on their provider's servers, so they can't start a program on your computer or
reach `localhost`. They connect over HTTP to `<your address>/mcp` with the token as a Bearer
header.

1. Make your instance reachable at a public HTTPS address first — see
   [Exposing beyond localhost](exposing-beyond-localhost.md). (This needs a server install; the
   desktop app only accepts connections from its own computer.)
2. Open the app through that public address, go to **Settings → MCP**, choose **ChatGPT** or
   **Generic HTTP / remote**, and click **Copy config**.
3. **ChatGPT:** **Settings → Connectors** (with Developer mode on) **→ Add custom connector**, and
   enter the **MCP server URL** (`https://your-address/mcp`) and the Bearer token from the copied
   text. **Other apps:** use the copied `url` and `Authorization: Bearer …` header.

## What a connected app can do

An app holding your API token **acts as you**: it can read your whole book and make changes.

- **No approval cards.** When Lucius wants to make a change that needs your approval, he shows you
  a card first. Other apps don't get those cards: a tool set to **Ask** runs straight
  away when another app calls it. Only tools set to **Block** in
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
