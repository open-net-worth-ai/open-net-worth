---
title: Ports and URLs
description: The address to open the app at, how to change the port, and how to reach it from another device.
category: reference
order: 20
audience: [user, operator]
status: stable
related: [getting-started/installation, reference/environment-variables, operations/exposing-beyond-localhost, operations/desktop]
keywords: [port, url, localhost, "8080", "5174", address, lan, health check]
---

# Ports and URLs

## Where to open the app

| How you run it | Open | Notes |
|---|---|---|
| `make start`, `onw start`, or Docker | `http://localhost:8080` | One port serves the whole app. |
| The desktop app | — | Nothing to open. The app runs its own private server on `127.0.0.1`, on a new random port each launch, so 8080 stays free. |
| Developer mode (`cd frontend && npm run dev`) | `http://localhost:5174` | Only when you're changing the app's code. It forwards requests to the backend on `8080`, so run that too. |

`onw start --open` opens your browser at the right address once the app is up.

## Useful addresses

All relative to the app's address (e.g. `http://localhost:8080`):

| Path | What it is |
|---|---|
| `/` | The app. |
| `/help` | This help center, inside the app. |
| `/api/health` | A health check — returns `{"status": "ok", …}` with the version when the server is up. Works without signing in, so it's safe for a monitoring tool or a load balancer. |

To see which address a running server is on, run `onw status`.

## Changing the port

8080 already taken, or want a different one?

- `onw start --port 8081`, or `PORT=8081 onw start`
- From a checkout: `PORT=8081 make start` (or `make dev PORT=8081` for the auto-reloading
  developer server)
- Docker: `PORT=8081 docker compose up -d` — the app inside the container still uses 8080;
  only the port on your machine changes.

Then open `http://localhost:8081`. See [Environment variables](environment-variables.md#server).

## Reaching it from another device

By default the app only listens on this machine (`127.0.0.1`), and Docker publishes it to this
machine only. To open it from your phone or another computer on the same network:

1. Set a password first (**Settings → Security**). Other devices always need it.
2. Start the app with `HOST=0.0.0.0` (e.g. `HOST=0.0.0.0 onw start`). With Docker, change the
   `ports:` line in `docker-compose.yml` to `"0.0.0.0:${PORT:-8080}:8080"` (it's there,
   commented out).
3. On the other device, open `http://<this-machine's-LAN-IP>:8080`, e.g.
   `http://192.168.1.42:8080`.

Before you do this, read [Exposing this beyond localhost](../operations/exposing-beyond-localhost.md)
— anyone on that network can reach the login screen. Never forward the port to the internet
without something in front of it.

## Ports you can ignore

The project's own tools use `5175`/`8175` (a throwaway demo) and `8090`–`8099` (automated
tests). They never touch your data and aren't part of normal use.

## Related

- [Environment variables](environment-variables.md)
- [Installing Open Net Worth](../getting-started/installation.md)
- [The backend won't start](../troubleshooting/backend-wont-start.md#the-port-is-already-in-use)
