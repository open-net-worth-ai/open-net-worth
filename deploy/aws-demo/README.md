# Hosted demo on AWS Lightsail

Runs a public Open Net Worth demo — fabricated data, no login, capped AI usage — on
one AWS Lightsail VM, reached through a Cloudflare tunnel. **About $12/month**: Lightsail
2 GB / 2 vCPU / 60 GB SSD, with IPv4 and 3 TB of transfer included. AI usage is billed by your
model provider.

```
visitor ─▶ Cloudflare (DNS + TLS) ─▶ demo-only tunnel ─▶ VM: cloudflared ─▶ 127.0.0.1:5175 ─▶ onw-demo container
                                                                                           └ /srv/onw-demo (the book)
```

- **No public HTTP port.** Only SSH is open; the tunnel connector dials out.
- **One container** built from the repo's `Dockerfile` serves both the API and the frontend.
  Its environment matches `scripts/run_demo.sh`: `DEMO_MODE` + `DEMO_OPEN_ACCESS`, a random
  password nobody is told, no connector credentials, and the same visitor quotas.
- **Secrets stay on the VM** in `/srv/onw-demo-stack/.env` (mode 600): the session signing
  key, the unpublished password, the tunnel token, the image tag. The OpenRouter key is stored
  in the book itself, the same way Settings stores it.
- **Visitors are operators with limits.** There is no login: visitors land
  straight in the app. `DEMO_MODE` allows writes only to the fabricated book and refuses
  everything else (secret reveal, AI settings, the Builder, MCP servers, the sandbox, uploads,
  Automations, backups, `/api/reset`, password changes — ADRs
  `2026-10-05-a-public-demo-visitor-is-an-operator-who-cannot-reach-past-the-book` and
  `2026-10-08-a-passwordless-demo-closes-what-the-password-used-to-guard`), so AI settings change
  only through `demo-aws.sh lucius`.
- **One proxy hop.** `DEMO_TRUSTED_PROXY_HOPS=1`: the Cloudflare edge appends the visitor's address
  to `X-Forwarded-For`, and the quotas and conversation privacy key on that rightmost entry. If you
  put another proxy in front, raise it to match; if it is wrong, visitors share a bucket.

## Requirements

On the machine you run the script from:
- AWS CLI v2 signed in to the account you want. `AWS_PROFILE` and `AWS_REGION` choose the
  account and region, as with any AWS CLI command.
- Docker with `buildx`. The image is built for `linux/amd64`.
- An SSH key at `~/.ssh/id_ed25519`, or another one set with `ONW_DEMO_SSH_KEY`.
- `cloudflared`, plus a Cloudflare zone for the demo's hostname.
- `sqlite3`, only if you copy an existing book.

## Set up

```bash
deploy/aws-demo/demo-aws.sh provision      # ~4 min: VM, firewall (22 only), static IP, Docker, swap
deploy/aws-demo/demo-aws.sh ship           # build committed HEAD for linux/amd64, load it on the VM
deploy/aws-demo/demo-aws.sh book --seed    # fresh fabricated book dated to today
deploy/aws-demo/demo-aws.sh lucius         # optional: OpenRouter key (hidden prompt) + model
deploy/aws-demo/demo-aws.sh verify         # http://127.0.0.1:15175 → the VM's demo, check it by hand
```

**Already hosting the demo with `make demo-host`?** Use `book [DIR]` instead of `book --seed`.
It copies that book (default `~/.open-net-worth-demo`) while it keeps serving, using SQLite's
online backup. It also carries the session key across, so visitors stay signed in.

### Point a hostname at it

Give the demo **a tunnel of its own**. Every connector of a Cloudflare tunnel serves every
hostname that tunnel routes. If you start the VM on a tunnel that also routes other sites,
Cloudflare sends some of those sites' visitors to the VM, and they get errors.

```bash
cloudflared tunnel login                                   # once: pick the zone in the browser
cloudflared tunnel create onw-demo
cloudflared tunnel token onw-demo | deploy/aws-demo/demo-aws.sh tunnel
cloudflared tunnel route dns --overwrite-dns onw-demo demo.example.com
```

The connector starts before the DNS record points at it, so the switch is a single DNS change.
If the hostname was served from somewhere else before, stop that host once the new one
answers.

A tunnel created in the Zero Trust dashboard works too. Give it one public hostname pointing
at `http://localhost:5175`, then pipe or paste its token into `demo-aws.sh tunnel`.

## Day to day

| Task | Command |
|---|---|
| Deploy new code | `demo-aws.sh ship` (committed HEAD) or `ship --worktree` (includes uncommitted changes) |
| Health / memory / disk | `demo-aws.sh status` |
| Tail app logs | `demo-aws.sh logs` |
| Shell on the VM | `demo-aws.sh ssh` |
| Roll back | `demo-aws.sh ssh docker images onw-demo`, then set `ONW_DEMO_TAG=<old tag>` in `/srv/onw-demo-stack/.env` and run `docker compose up -d app` there |
| Fresh book | `demo-aws.sh book --reset` (keeps Lucius's AI settings) |
| Change quotas | edit `DEMO_TOKENS_*` / `DEMO_TURNS_*` / `DEMO_TRUSTED_PROXY_HOPS` in `/srv/onw-demo-stack/.env`, then `docker compose up -d app` |

Running more than one VM? `ONW_DEMO_VM=<name>` picks which one; the default is `onw-demo`.

### Daily refresh from your Mac

```bash
AWS_PROFILE=… AWS_REGION=… deploy/aws-demo/daily.sh install 10:00   # or ONW_DEMO_HOST=<static ip>
deploy/aws-demo/daily.sh run                                         # the same job, by hand
deploy/aws-demo/daily.sh uninstall
```

The agent runs at that local time. Install looks up the VM's static IP once and stores it in the
agent, so a run needs only SSH, never an AWS sign-in (an `aws login` session expires within a
day). Each run ships `origin/main` if it differs from the live tag, then runs `book --reset`: a fresh
fabricated book dated to today, with Lucius's AI settings carried over inside the VM. Visitors'
edits, conversations and the app's nightly backups of them are discarded. If the new image never
reports healthy, the job rolls back to the tag that was running and raises a macOS
notification. It starts Docker Desktop only when it needs to build, deletes the local
`onw-demo` images and build cache afterwards, and quits Docker again if it started it. A Mac that
is asleep at the hour runs the job on wake; one that is off skips the day. Log:
`~/Library/Logs/onw-demo-daily.log`.

It deploys `main` whatever CI said about it — the health check is the only gate.

## Spend

The app's own caps bound what Lucius can cost: 30M tokens/day across the whole instance and
3M per visitor IP. The per-IP count uses the tunnel's `X-Forwarded-For`, which a visitor can
spoof, so the instance-wide cap is the one to rely on. For a hard stop, also set a **credit
limit on the provider key**, e.g. at https://openrouter.ai/settings/keys.

## Tear down

```bash
aws lightsail delete-instance --instance-name onw-demo
aws lightsail release-static-ip --static-ip-name onw-demo-ip
aws lightsail delete-key-pair --key-pair-name onw-demo-key
cloudflared tunnel delete onw-demo        # after removing its DNS record
```
