---
title: Krisp on calls runs on the voice worker, and the owner picks the filter
status: Accepted
date: 2026-11-08
deciders: orchestrating agent (Claude) + repo owner
---

# Krisp on calls runs on the voice worker, and the owner picks the filter

## Context

A call only had the browser's built-in noise suppression. LiveKit Cloud includes Krisp, in two forms:
background noise suppression, at no extra cost, and voice isolation, which also removes other voices
and comes with 100 free minutes a month. Each can run in the browser or on the agent, not both.

The browser package needed `blob:` and `'wasm-unsafe-eval'` in the page's `script-src`, a model
download from a new host, and proof that it works on an iPhone, where an analyser on the mic has
already read zero through a whole call. It also turns the browser's own noise suppression off first,
and can then stop working with no error reaching the app.

## Decision

Krisp runs on the voice worker (`connectors/voice/noise.py`), chosen per call by a `noise_filter`
setting: `off`, `standard` (the default) or `isolation`. It applies only on LiveKit Cloud, and a filter
that can't start leaves the call running without one. The browser's own noise suppression stays on,
which LiveKit's docs allow.

Settings shows one estimate of free minutes for everything picked, including what runs out first.

## Consequences

- Every install, the Docker image and the desktop app grow by about 138 MB unpacked. A choice between
  filters still installs both.
- `livekit-agents` moves to 1.8.11 and `livekit` to 1.2.0, which `livekit-plugins-krisp` requires.
- LiveKit turns its server-side gain control off when a filter is set directly. A quiet caller may be
  heard less well, so a real call has to check it.
- When LiveKit refuses voice isolation, the plugin logs an error every few seconds and passes audio
  through unchanged, seen for minutes with the real filter and an unreachable server, and pinned by a
  test. A project that has really used its 100 minutes hasn't been tried. Isolation doesn't step down
  to Standard, and nothing tells the caller.
- Both packages point to LiveKit's terms, not an open licence, and the image and desktop app would carry
  them. Whether that's allowed has to be settled before a release.
