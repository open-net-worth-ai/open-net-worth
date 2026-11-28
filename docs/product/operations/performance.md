---
title: Performance
description: What to check when a screen or Lucius feels slow — the common causes, where to see timings, and the settings that help.
category: operations
order: 70
audience: [user, operator]
status: stable
related: [operations/logs, getting-started/installation, lucius/models-and-cost]
keywords: [performance, slow, speed, latency, turn time limit, model performance, dev server, build, PERF-REQ]
---

# Performance

Most screens open in well under a second, even on a large book. When something feels slow, it's
usually one of three things: the way the app is being run, the AI model Lucius is using, or one
particularly heavy report. Here's how to tell which, and what to do.

## A screen is slow

1. **Check how you're running it.** The desktop app, `onw start`, Docker and `make start` all
   serve the finished, optimized web app — nothing to do. If you run from a source checkout with
   the developer setup (`npm run dev` and `uvicorn --reload`), every page loads hundreds of
   unoptimized files, which is genuinely slow, especially on a phone. For everyday use, run
   `make setup start` (or `npm run build` once, then `make start`) instead.
2. **Expect a few things to take longer.** A full financial report, returns over a long history,
   or the first load after the app starts do more work than an ordinary screen. Prices show the
   last known value straight away and refresh in the background, so a slow price provider never
   holds up a page.
3. **Still slow? Note which screen.** "The Commitments tab takes 4 seconds" is something that can
   be fixed; "the app is slow" usually isn't. Send it with
   [Send feedback](../troubleshooting/reporting-a-problem.md) and tick the box to include
   recent log lines — they carry the timings described below.

### Seeing the timings yourself

Every request the app answers writes one line to the [log](logs.md), like:

```text
PERF-REQ GET /api/commitments total=850ms db=120ms(14q) compute=730ms
```

`total` is how long the request took, `db` is time spent in the database (and how many queries
ran), and `compute` is everything else. A request that's mostly `db` with a very large query
count points at the data side; one that's mostly `compute` points at the calculation.

## Lucius is slow

Lucius's speed is almost entirely the speed of the AI model behind him.

- **See how your models compare:** **Settings → Cost & Performance → Model performance** shows,
  per model, the time to the first word (*TTFT p50*), how long a reply takes (*Duration p50*) and
  output speed (*Tok/sec p50*), over the last 50 recorded model calls. It fills in after Lucius's first reply.
- **Pick a faster model** for everyday questions in **Settings → AI** — see
  [Models and cost](../lucius/models-and-cost.md).
- **Replies stopped mid-task?** A turn (one reply, or one Automation run) is stopped after
  20 minutes by default. If a slow model keeps hitting that, raise it under
  **Settings → AI → Turn time limit → Stop a turn after** — up to 2 hours, or **No limit**.
  With no limit, a turn runs until it finishes or you press Stop.

## Related pages

- [Logs](logs.md) — where the timing lines are written
- [Models and cost](../lucius/models-and-cost.md) — choosing a model
- [Reporting a problem](../troubleshooting/reporting-a-problem.md) — sending a slow screen to the
  developers
