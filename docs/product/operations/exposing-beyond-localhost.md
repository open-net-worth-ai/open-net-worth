---
title: Exposing this beyond localhost
description: Reach your instance from your phone or anywhere, safely — a private network (Tailscale) or a reverse proxy with HTTPS, step by step, plus gateway mode.
category: operations
order: 50
audience: [operator]
status: stable
related: [operations/security-and-privacy, operations/multi-device, operations/connect-ai-apps]
keywords: [remote access, expose, reverse proxy, Caddy, nginx, Tailscale, VPN, tunnel, TLS, HTTPS, 0.0.0.0, LAN, gateway mode, X-Onw-Gateway-Auth, ONW_GATEWAY_TOKEN, authenticating proxy, CORS]
---

# Exposing this beyond localhost

Out of the box, a server install only answers on the computer it runs on. To use it from your
phone, another computer, or a hosted AI app, you make it reachable — and since it holds your
whole financial picture behind one password, you do that carefully. This page shows the two safe
ways and the settings that matter.

Using the **desktop app**? It only ever accepts connections from its own computer. To reach your
book from other devices, run Open Net Worth as a server (see
[Installing](../getting-started/installation.md)) and point the desktop app at it with
[Connect to a server](desktop.md#connect-to-a-server).

## Who listens where by default

| How you run it | Listens on |
|---|---|
| `onw start`, `make start`, `python3 -m backend.main`, `onw autostart on` | `127.0.0.1:8080` — this computer only |
| Docker (`docker-compose.yml`) | `127.0.0.1:8080` on the host — this computer only |
| `make serve` (developer target) | `0.0.0.0:8080` — **every device on your network** |

Change the address with `onw start --host … --port …` (or the `HOST` / `PORT` environment
variables). `--host 0.0.0.0` makes it reachable from your whole network.

## Before you expose anything

1. **Set a password.** On the computer running Open Net Worth, open **Settings → Security** and
   set one. Until a password exists, the app refuses every connection that isn't from its own
   computer.
2. **Know what protects you.** The app speaks plain HTTP and has one password — no second factor,
   no IP allowlist. Five wrong passwords from one address lock that address out for 15 minutes
   (the count resets when the app restarts). So:
   - never port-forward it on your router or put it on a public address as-is — your password
     would cross the internet unencrypted;
   - put it behind a private network or an HTTPS reverse proxy, as below.

## Option 1 — a private network (simplest, recommended)

A private network such as [Tailscale](https://tailscale.com) (or WireGuard, ZeroTier) connects
your devices to each other, encrypted, without opening anything to the internet.

1. Install Tailscale on the computer running Open Net Worth and on your phone/laptop, signed in to
   the same account.
2. Start Open Net Worth so it listens beyond localhost:

   ```bash
   onw start --host 0.0.0.0
   ```

   As a boot service: `onw autostart off`, then `HOST=0.0.0.0 onw autostart on`.
   Docker: in `docker-compose.yml`, change the `ports:` line to `"0.0.0.0:${PORT:-8080}:8080"` and
   run `docker compose up -d`.
3. On your other device, open `http://<computer's Tailscale name or IP>:8080` and sign in with
   your password.

Binding to `0.0.0.0` also makes it reachable from your home network (Wi-Fi). If you don't want
that, bind to the computer's Tailscale address instead (`--host 100.x.y.z`).

## Option 2 — a reverse proxy with HTTPS

Use this for a real public address such as `https://wealth.example.com` (needed for ChatGPT and
other hosted AI apps). The proxy handles HTTPS; Open Net Worth stays on `127.0.0.1:8080` behind
it.

1. **Turn off password-free access on the server.** In **Settings → Security → Auth behaviour**,
   turn off **Don't ask for the password on this computer**. A proxy on the same computer
   connects from `127.0.0.1`. The server already treats a request carrying `X-Forwarded-For`,
   `Forwarded`, `X-Real-IP` or `X-Forwarded-Host` as coming from elsewhere and asks for the
   password, but a proxy that adds none of them (nginx's bare `proxy_pass` is one) would
   otherwise let every visitor in **without a password**. (Setting `AUTH_TRUST_LOOPBACK=false`
   only works on a brand-new install; afterwards use the switch.)
2. **Configure the proxy.** It must pass the original `Host` header, the visitor's address
   (`X-Forwarded-For`) and the scheme (`X-Forwarded-Proto`), and must not buffer responses (Lucius
   streams his replies).

   **Caddy** (gets the certificate for you):

   ```text
   wealth.example.com {
       reverse_proxy 127.0.0.1:8080
   }
   ```

   **nginx**:

   ```nginx
   location / {
       proxy_pass http://127.0.0.1:8080;
       proxy_set_header Host $host;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_buffering off;
       client_max_body_size 200m;
   }
   ```

   `client_max_body_size` raises nginx's 1 MB upload limit, which is too small for statements
   and book exports; set it to whatever your largest upload needs.

3. **Check it:** open `https://wealth.example.com`, sign in, and make a small change (for example
   rename a category and rename it back). Then, from a phone on mobile data, confirm you get the
   sign-in screen rather than your book.

The same applies to tunnels that run on the server and forward to `localhost` (Cloudflare Tunnel,
`tailscale serve`, ngrok): treat them as a same-computer proxy and do step 1.

## Troubleshooting

**Every change fails with "cross-origin request refused" (403)** — the proxy is rewriting the
`Host` header (nginx does by default). Add `proxy_set_header Host $host;`.

**"host not allowed" (421)** — you reached the app by a name it doesn't recognise while
password-free access is on and you aren't signed in. Sign in, or add the name to
`ONW_ALLOWED_HOSTS` (comma-separated).

**Lucius's replies arrive all at once, or hang** — the proxy is buffering. In nginx add
`proxy_buffering off;`.

**"too many attempts, try again later" (429)** — five wrong passwords from that address; wait
15 minutes.

**Another device says "This server doesn't have a password yet"** — set a password in **Settings
→ Security** on the computer running Open Net Worth.

**Keep the web app and the server on one address.** Serving the web app from a different domain
than the server isn't supported for signed-in use; the `CORS_ORIGINS` setting exists for local
development only.

## Running behind an authenticating proxy (gateway mode)

For advanced setups where a proxy you control already signs people in (this is how the hosted
Private Cloud runs). The app then defers to the proxy instead of showing its own login. Set
`ONW_GATEWAY_TOKEN` to a random secret of at least 32 characters (a shorter one refuses to boot)
and have the proxy add these headers to every request it forwards:

| Header | Meaning |
|---|---|
| `X-Onw-Gateway-Token` | **Required** on every request, including the web app's own files. Compared in constant time; missing or wrong gets `403 {"detail":"gateway token required"}`. Only `GET /api/health` is exempt, for health checks. |
| `X-Onw-Gateway-Auth` | `session` when the proxy has signed the owner in; `none` when it has not. The proxy must strip this header from clients and set it itself. Only the exact value `session` signs the request in — anything else is treated as not signed in. |
| `X-Onw-User-Email` | Optional. Shown in the app for display only — never used to decide who may do anything. |
| `X-Onw-Portal-Url` | Optional. An absolute `http(s)` URL; the app shows it as a "Private Cloud portal" link. Display only. |
| `X-Onw-Session-Started` | Optional. Unix time the proxy signed this session in. Only the [terminal](terminal.md) reads it, to ask for a recent sign-in. |

The three optional headers are believed only on a request that also carries
`X-Onw-Gateway-Auth: session`. `GET /api/health` answers with `status`, `version`, `contracts`
and `gateway` — `gateway` is `true` only while this mode is on, so a proxy can tell an app that
really enforces its token from one that is merely up.

A signed-in request sees no login or set-password screens; **Settings → Security** shrinks to who
is signed in, the API token and sign-out. **Sign out of this app** and **Sign out everywhere**
each `POST` a form with `scope=app` or `scope=everywhere` to `/_gateway/logout`, a path your proxy
must serve. When a session expires, the proxy can answer an API call with `401` and
`{"reauth": "<absolute URL>"}` (same origin or the portal's): the app keeps the message being
typed, shows "Signing you back in…", goes there, and restores the text on return. A `401` without
`reauth` reloads the page. Either way the app leaves at most once every 30 seconds.

**Callers with their own credentials** — phone Shortcuts, the CLI and MCP clients — carry the
API token (`Authorization: Bearer …`). Have the proxy forward them with `X-Onw-Gateway-Auth:
none`; the app then checks the token itself, and anything else gets `401`. Such requests are
always treated as remote: password-free local access and the *Disable auth* switch never apply.

Bind the app to an address only the proxy can reach and keep the token out of anything a browser
can see — anyone who can reach the port *and* knows the token is signed in:

```bash
ONW_GATEWAY_TOKEN="$(openssl rand -hex 32)" onw start --host 0.0.0.0 --port 8080
```

## Related pages

- [Using it from more than one device](multi-device.md)
- [Security and privacy](security-and-privacy.md)
- [Use your book from other AI apps](connect-ai-apps.md) — hosted apps need the public address
