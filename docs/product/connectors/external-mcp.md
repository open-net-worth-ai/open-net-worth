---
title: External MCP servers
description: Give Lucius new tools by connecting an MCP server — a hosted URL or a local command — step by step, plus the risks you accept when you do.
category: connectors
order: 40
audience: [user, operator]
status: stable
related: [lucius/permissions, lucius/confirm-cards, connectors/web-search]
keywords: [mcp, model context protocol, third-party, stdio, local command, remote server, oauth, api key, playwright, stripe, setup, security]
---

# External MCP servers

MCP (Model Context Protocol) is a standard way for an app to offer tools to an AI assistant. Many
services now publish an MCP server — accounting, payments, market data, a full web browser — and
connecting one gives Lucius whatever tools that server offers.

This is the most powerful connector in the app, and the one that deserves the most care: **a
server you connect is someone else's software, and what it returns goes straight into Lucius's
conversation.** Only connect servers you trust, and start every tool at **Needs approval** (the
default).

There are two kinds:

- **Remote server (URL)** — a service hosted by the vendor. You give its address and, usually,
  sign in or paste a key.
- **Local command (stdio)** — a program the app starts on this machine, such as
  `npx -y @playwright/mcp@latest`. It runs with the same access to this computer as Open Net
  Worth itself.

## Add a server from the directory

The directory includes ready-made entries — Playwright (a real web browser), Firecrawl, Stripe,
QuickBooks Online, Xero, Mercury, Interactive Brokers, Robinhood, moomoo, Alpaca MCP, Coinbase,
OKX, Kraken, Expensify, Avalara AvaTax, Twelve Data, SEC EDGAR and FRED.

Each connector shows coloured tags for what it's for, such as **Brokerage**, **Banking** or
**Web search**. Searching the Connectors page matches those tags too. In **Browse & add**, click a
category in the row of small tags under **All · Channels · Data sources · Web · MCP**, or click a
tag on any card, to see only that kind; click it again (or **All**) to clear it. On a phone the
category tags sit behind the filter icon beside search, which lights up while one is picked. A server you've
already added shows once, with a settings gear instead of **+**. **Suggested**
lists up to three connectors you haven't added yet — Email, Telegram, Plaid, Interactive Brokers and
Coinbase first — once at the bottom of **All**, or under the section on a single tab such as
**MCP** — and on a tab with nothing set up yet, inside its empty view, under **Browse & add** — and at the top of **Browse & add** before you
search or filter; click one to read about it and add it. **Sort** orders the full list below it:
**Popular** puts the connectors you've added first, then popular ones, then the rest A–Z.

1. Open **Connectors** in the sidebar, click **Browse & add**, and choose **MCP**.
2. Click a card to read what it does and which tools it offers, then click **Add**. The connection
   form opens with the address or command already filled in.
3. Fill in what the server needs:
   - **OAuth** servers (Mercury, Interactive Brokers, Robinhood, moomoo, Expensify, Avalara,
     Twelve Data): click **Connect** and sign in to the vendor in the window that opens.
   - **API key** servers (Stripe): paste the key into **API key**. For Stripe, a *restricted*
     key with only the permissions you want is the safer choice.
   - **Local commands** with **Environment variables (optional)** pre-listed (Firecrawl, Xero, SEC
     EDGAR, FRED, Alpaca MCP, Coinbase, OKX, Kraken): fill in the value after each `=`, for example
     `FRED_API_KEY=abc123`. Get the value from the vendor — the card's **Documentation** or
     **Source repository** link says where. The trading ones start without real money where the
     vendor offers that: Alpaca MCP on paper trading, OKX with `--demo` in its arguments, Kraken
     with only market data, a read-only account and paper trading; the card says how to switch.
     A value that is a private key (`-----BEGIN … KEY-----` … `-----END … KEY-----`) can be
     pasted as it is — over several lines, on one line, or with `\n` in place of the line breaks,
     the way a downloaded JSON key file holds it; the app rebuilds it into a key the server can
     read.
     Coinbase needs Node.js 22 or later and a key created at
     [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com/api-keys/secret) with
     **Signature algorithm: ECDSA** (Advanced Trade refuses the default Ed25519 key): its key name
     (`organizations/…/apiKeys/…`) goes in `COINBASE_KEY_ID` and its private key in
     `COINBASE_KEY_SECRET`. Alpaca MCP needs [uv](https://docs.astral.sh/uv/); Kraken
     needs the [Kraken CLI](https://github.com/krakenfx/kraken-cli) installed first (macOS or
     Linux).
4. Click **Connect**. The app starts the server, checks it answers, and lists its tools — *Testing
   connection to … and listing tools…*. A local command can take a few minutes the first time
   while its package downloads.
5. Review the tools it found. Each starts at **Needs approval**; change any to **Always allow** or
   **Blocked** if you want. Click **Add server**.

The toast *"… added — N tools ready for Lucius"* confirms it. Nothing is saved until the
connection test succeeds.

QuickBooks Online has no ready-made settings — follow the links on its card for the vendor's own
instructions, and enter what they give you in the form (or add it as a custom server, below).

## Add any other server

1. **Connectors → Browse & add → MCP → Custom MCP server**.
2. If the server's README has a JSON config block (the `"mcpServers": { … }` kind), click **Paste
   from a config snippet** and paste it — the fields below fill themselves in. Otherwise:
3. Pick a **Transport**:
   - **Remote server (URL)** — enter the **Server URL** (starting with `https://`, or `http://`
     for a server on your own machine), then choose **Authentication**: **None**, **API key**
     (sent to the server as a bearer token), or **OAuth** (you sign in to the vendor in a pop-up).
     A line under the menu says what each choice is for. If you pick the wrong one, the error
     says which the server wants — for example "This server needs you to sign in" — with a
     button that switches it for you.
     If no sign-in window appears, click **Open the sign-in page** under "Waiting for sign-in…";
     the screen picks up the result once you finish, whichever window you signed in from.
   - **Local command (stdio)** — enter the **Command** (for example `npx` or `uvx`), the
     **Arguments** (for example `-y @modelcontextprotocol/server-filesystem /path/to/folder`) and
     any **Environment variables**, one `NAME=value` per line (a private key may span several lines).
4. Click **Connect**, review the tools, and click **Add server** — the same as steps 4–5 above.

A local command needs its runtime installed on the machine running Open Net Worth — Node.js for
`npx`, [uv](https://docs.astral.sh/uv/) for `uvx`.

For a remote server, Open Net Worth fills in a readable name and icon from what the server says
about itself, or else from its website, instead of its bare address. That's what the site says
about itself, not a check of who runs the server, so the badge still reads **Unknown
publisher**. A name you typed yourself is never replaced. **⋯ → Refresh tools list** reads the
name and icon again.

## Check that it works

Ask Lucius to use it, for example after adding Playwright:

> *Open example.com in the browser and tell me what the page says.*

The first time he uses each tool, you'll get a card to approve it. The server's page under
**Connectors** lists its tools and their levels under **Tool permissions**.

## Managing a server

Open the server under **Connectors**:

- **⋯ (More actions) → Edit configuration** — change the address, key, command or environment.
  The connection is tested again before anything is saved. A saved key is never sent back to your
  browser, so to re-test with it, reveal it with the eye icon or paste it again. The transport
  can't be changed — add the server again instead.
- **⋯ → Refresh tools list** — pick up tools the server added since you connected it.
- **Pause** / **Resume** — Lucius loses access to its tools while paused. Resuming a server that
  has never listed its tools connects to it and lists them; if it can't be reached, it shows an
  error instead of Connected.
- **⋯ → Remove connector** — removes the server and its saved credentials.
- **Reconnect** — appears if the server stopped answering or a sign-in expired.

## Permissions — start cautious

Every tool from an MCP server starts at **Needs approval**: Lucius shows you exactly what he's
about to call and waits for you. You can change each tool's level under **Tool permissions**, or
by choosing **Always allow** on an approval card. See [Permissions](../lucius/permissions.md).

Tools are listed under **Read-only tools** or **Write & delete tools**. When the server marks a
tool as read-only (or not), that's the heading it gets; otherwise the app guesses from the tool's
name. The heading is only a label. It never changes a tool's level or whether a call runs.

**Always allow** really means always — including inside a scheduled Automation with nobody
watching. Only use it for a tool you've used enough to trust, and never for one that can change or
send things (a payment, an invoice, an email) unless you're sure. Remember the vendor can update
their server at any time.

## Risks, in plain terms

- **A local command can do anything you can do on this machine** — read and write your files, use
  the network. It runs for as long as the connector is enabled, not just during a call. Only add
  commands from sources you trust, like any software you install.
- **A server's answers can try to trick Lucius.** Text from a third-party server is treated as
  data, never as instructions from you, and is cleaned and shortened before Lucius sees it. That
  lowers the risk; it doesn't remove it — which is why approval is the default.
- **Some servers write to real accounts.** Stripe, QuickBooks, Xero and Mercury tools can create or
  change records in those services, and Interactive Brokers, Robinhood, moomoo, Alpaca MCP,
  Coinbase, OKX and Kraken tools can place orders (Robinhood only in a separate agent account it opens
  for you). The directory card says so when that's the case. For a server you run with an API
  key, the key's own permissions are the limit — create it with only what you want allowed.

What protects you:

- Lucius never sees the server's full address or command line — only its host or program name —
  because a connection string often contains a password or key. You see the full details on the
  Connectors page.
- Lucius can't add a server, change one, or raise a tool's permission. Only you can, from the
  Connectors page.
- Keys, OAuth sign-ins and environment variables are stored on this machine only, never sent to
  the AI model, and left out of exports and sanitised backups.

## Troubleshooting

**"Could not reach the server."** — check the URL or command is right and the server is running.
For a local command, the box under the spinner shows the command's own output; an error there
(for example *command not found*) usually means Node.js or uv isn't installed.

**"Enter a full URL, starting with http:// or https://."** — include the `https://` part.

**The test fails with an authorization error** — the server needs credentials. Switch
**Authentication** to **API key** or **OAuth** and try again.

**OAuth: "Register this redirect URI with the vendor, then try again"** — the vendor needs you to
set up an app on their side. Register the address shown, then click **Connect** again.

**OAuth: a Client ID field appears** — this server doesn't support automatic sign-up. Create an
app registration at the vendor, paste its client ID, and try again.

**"Sign in above, then test the connection."** — finish the OAuth sign-in before clicking
Connect again.

**Was connected — sign-in expired** — click **Reconnect** (or **Edit configuration**) and sign in
again.

**It hangs on a first connect** — a local command may still be downloading. If the output box has
been quiet for a long time, click **‹ Back** at the top, check the command, and try again.

## Related pages

- [Permissions](../lucius/permissions.md) — Needs approval, Always allow and Blocked
- [Confirm cards](../lucius/confirm-cards.md) — what approving a call does
- [Web search](web-search.md) — built-in search, no MCP server needed
